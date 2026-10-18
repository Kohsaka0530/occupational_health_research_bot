from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlencode, urlsplit

from .core import Article, plain
from .net import FetchError, get_json, request
from .journals import searches


EUROPE_QUERY = '''(occupational OR "work-related" OR "workplace health" OR
"workplace safety" OR "gig workers" OR "platform workers" OR "delivery riders" OR
"freelance" OR "long working hours" OR "shift work" OR
("mental health" AND (workers OR employment)) OR
("digital health" AND workers) OR ("health promotion" AND "small business") OR
"workplace" OR "employment" OR "unemployment" OR "retirement" OR
"work disability" OR "return to work" OR "sick leave" OR "workers" OR "physician burnout")'''


def europe_article(item: dict) -> Article | None:
    title = item.get("title", "")
    if not title:
        return None
    source = item.get("source", "MED")
    doi = item.get("doi", "")
    pmid = item.get("pmid")
    identifier = item.get("id", "")
    if pmid:
        url = "https://pubmed.ncbi.nlm.nih.gov/" + quote(str(pmid), safe="") + "/"
    elif doi:
        url = "https://doi.org/" + quote(doi, safe="/")
    else:
        url = "https://europepmc.org/article/" + quote(source, safe="") + "/" + quote(identifier, safe="")
    types = item.get("pubTypeList", {}).get("pubType", [])
    evidence = ", ".join(types)
    journal_info = item.get("journalInfo", {})
    journal = journal_info.get("journal", {})
    return Article.create(
        title=title, url=url, source="Europe PMC", doi=doi,
        kind="プレプリント" if source == "PPR" or any("preprint" in t.lower() for t in types) else "学術論文",
        published=item.get("firstPublicationDate", str(journal_info.get("yearOfPublication", ""))),
        abstract=plain(item.get("abstractText", "")),
        journal=journal.get("title", journal.get("medlineAbbreviation", "")),
        authors=item.get("authorString", ""), evidence=evidence,
        retracted=any(t.lower() == "retracted publication" for t in types)
        or item.get("isRetracted", "N") == "Y")


def europe_search(query: str, config: dict, max_pages: int) -> tuple[list[Article], bool]:
    cursor = "*"
    results: list[Article] = []
    capped = False
    for page in range(max_pages):
        params = {"query": query, "format": "json", "resultType": "core",
                  "pageSize": config["page_size"], "cursorMark": cursor, "sort_date": "y"}
        payload = get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urlencode(params))
        if "hitCount" not in payload or "resultList" not in payload:
            raise FetchError("Europe PMCの検索応答が想定した形式ではありません")
        batch = payload["resultList"].get("result", [])
        for item in batch:
            article = europe_article(item)
            if article:
                results.append(article)
        next_cursor = payload.get("nextCursorMark")
        if not batch or not next_cursor or next_cursor == cursor or len(results) >= payload["hitCount"]:
            break
        cursor = next_cursor
        capped = page == max_pages - 1
        time.sleep(0.35)
    return results, capped


def europe_pmc(since: date, until: date, config: dict) -> tuple[list[Article], bool]:
    if config.get("curated_journals"):
        # Always revisit the full publication queue. This also backfills the
        # new journal policy when upgrading an existing installation.
        since = until - timedelta(days=30)
        window = f"FIRST_PDATE:[{since.isoformat()} TO {until.isoformat()}]"
        results, capped = [], False
        for name, scope, occupational in searches():
            query = f"{scope} AND {window}"
            if not occupational:
                query += f" AND {EUROPE_QUERY}"
            batch, batch_capped = europe_search(query, config, config["max_pages"])
            results.extend(batch)
            capped = capped or batch_capped
            time.sleep(0.35)
        return results, capped
    window = f"FIRST_IDATE:[{since.isoformat()} TO {until.isoformat()}]"
    results, capped = europe_search(f"{EUROPE_QUERY} AND {window}", config, config["max_pages"])
    # Bounded focus searches prevent major journals and niche themes from
    # being crowded out by the broad query's retrieval cap.
    for focus in config.get("focused_queries", []):
        time.sleep(0.35)
        batch, batch_capped = europe_search(f"{EUROPE_QUERY} AND {focus} AND {window}", config, 1)
        results.extend(batch)
        capped = capped or batch_capped
    return results, capped


def crossref_article(item: dict) -> Article | None:
    title = next(iter(item.get("title", [])), "")
    doi = item.get("DOI", "")
    if not title or not doi:
        return None
    parts = item.get("published", {}).get("date-parts", [[]])[0]
    published = "-".join(str(n).zfill(2) if i else str(n) for i, n in enumerate(parts))
    authors = ", ".join(" ".join(filter(None, [a.get("given"), a.get("family")])) for a in item.get("author", [])[:8])
    relations = item.get("relation", {})
    # Crossref does not supply a consistent peer-review status; do not infer it.
    return Article.create(title=title, doi=doi, url="https://doi.org/" + quote(doi, safe="/"),
                          source="Crossref", kind="プレプリント" if item.get("type") == "posted-content" else "学術論文",
                          published=published, abstract=plain(item.get("abstract", "")),
                          journal=next(iter(item.get("container-title", [])), ""),
                          authors=authors, evidence=item.get("type", ""),
                          retracted=bool(relations.get("is-retracted-by")) or
                          any(u.get("type") == "retraction" for u in item.get("update-to", [])))


def crossref(since: date, until: date, config: dict) -> tuple[list[Article], bool]:
    results: list[Article] = []
    capped = False
    for query in config["crossref_queries"]:
        params = {"query.bibliographic": query, "rows": config["page_size"],
                  "filter": f"from-index-date:{since.isoformat()},until-index-date:{until.isoformat()},from-pub-date:{since.isoformat()},until-pub-date:{until.isoformat()},type:journal-article"}
        payload = get_json("https://api.crossref.org/works?" + urlencode(params))
        message = payload.get("message", {})
        if "items" not in message:
            raise FetchError("Crossrefの検索応答が想定した形式ではありません")
        capped = capped or message.get("total-results", 0) > config["page_size"]
        for item in message["items"]:
            article = crossref_article(item)
            if article:
                results.append(article)
        time.sleep(1)
    return results, capped


def parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return parsedate_to_datetime(value).date()
        except (ValueError, TypeError):
            return None


def parse_feed(payload: bytes, since: date, until: date) -> list[Article]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        raise FetchError("厚労省フィードが有効なXMLではありません") from None
    local = lambda tag: tag.rsplit("}", 1)[-1]
    entries = [n for n in root.iter() if local(n.tag) in {"item", "entry"}]
    if not entries and local(root.tag) not in {"RDF", "rss", "feed"}:
        raise FetchError("厚労省フィードの形式が想定と異なります")
    articles = []
    for entry in entries:
        fields = {local(n.tag): "".join(n.itertext()).strip() for n in entry}
        link = fields.get("link", "")
        if not link:
            link = next((n.attrib.get("href", "") for n in entry if local(n.tag) == "link"), "")
        host = urlsplit(link).hostname or ""
        if host != "mhlw.go.jp" and not host.endswith(".mhlw.go.jp"):
            continue
        pub = parse_date(fields.get("date") or fields.get("pubDate") or fields.get("updated") or fields.get("published", ""))
        if not pub or not since <= pub <= until:
            continue
        articles.append(Article.create(title=fields.get("title", ""), url=link,
                        source="厚生労働省", kind="政策・公式発表", published=pub.isoformat(),
                        abstract=plain(fields.get("description") or fields.get("summary", ""))))
    return articles


def mhlw(since: date, until: date, config: dict) -> tuple[list[Article], bool]:
    from .domestic import mhlw_updates
    return mhlw_updates(since, until, config)


def mhlw_meetings(since: date, until: date, config: dict):
    from .domestic import meetings
    return meetings(since, until, config)


def domestic_news(since: date, until: date, config: dict):
    from .domestic import news
    return news(since, until, config)


COLLECTORS = {"europe_pmc": europe_pmc, "crossref": crossref, "mhlw": mhlw,
              "mhlw_meetings": mhlw_meetings, "domestic_news": domestic_news}
