"""Bounded collection of Japanese occupational-health policy and social news."""
from __future__ import annotations

import html
import re
import shutil
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urldefrag

from .core import Article, deduplicate, plain, safe_url
from .net import FetchError, request
from .sources import parse_date, parse_feed

MHLW = "https://www.mhlw.go.jp"
SHINGI = MHLW + "/stf/shingi/indexshingi.html"
EXPERTS = MHLW + "/stf/shingi/indexshingiother_128808.html"
NEWS_HOSTS = {"www3.nhk.or.jp", "news.web.nhk"}
CATEGORIES = ["指針・ガイドライン", "法令改正", "専門家会議", "社会ニュース"]
RELEVANT = ["産業保健", "産業医", "労働安全", "安全衛生", "労働災害", "労災", "職業性", "過労", "過重労働", "労働時間", "長時間労働", "ストレスチェック", "職場", "個人事業者", "フリーランス", "労働基準", "健康保持増進", "一般健康診断", "化学物質管理", "化学物質による", "じん肺", "石綿", "労働者の健康", "労働者の安全", "労働条件", "勤務間インターバル"]
EXCLUDE = ["採用選考", "職員募集", "採用案内", "大臣表彰", "功労者", "閣議後記者会見のお知らせ"]


def official_url(value: str, base: str = MHLW) -> str:
    url = urldefrag(urljoin(base, value))[0]
    return url if safe_url(url) and urlsplit(url).hostname == "www.mhlw.go.jp" else ""


class Page(HTMLParser):
    """Extract main text, anchor labels and table rows without navigation."""
    def __init__(self, markup: str):
        super().__init__(convert_charrefs=True)
        self.has_main = "<main" in markup.lower()
        self.inside = not self.has_main
        self.hidden = 0
        self.text, self.links, self.rows = [], [], []
        self.anchor = None
        self.anchor_text = []
        self.row = None
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "main":
            self.inside = True
        if tag in {"script", "style", "nav", "footer", "header"}:
            self.hidden += 1
        if not self.inside or self.hidden:
            return
        if tag == "tr":
            self.row = {"text": [], "links": []}
        if tag == "a":
            self.anchor = attrs.get("href")
            self.anchor_text = []
        if tag in {"p", "br", "div", "td", "li", "h1", "h2", "h3"}:
            self.text.append(" ")
            if self.row is not None:
                self.row["text"].append(" ")

    def handle_data(self, data):
        if self.inside and not self.hidden:
            self.text.append(data)
            if self.anchor is not None:
                self.anchor_text.append(data)
            if self.row is not None:
                self.row["text"].append(data)

    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav", "footer", "header"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "a" and self.anchor is not None:
            link = (self.anchor, plain(" ".join(self.anchor_text)))
            self.links.append(link)
            if self.row is not None:
                self.row["links"].append(link)
            self.anchor = None
        if tag == "tr" and self.row is not None:
            self.row["text"] = re.sub(r"\s+", " ", " ".join(self.row["text"])).strip()
            self.rows.append(self.row)
            self.row = None
        if tag == "main":
            self.inside = False

    @property
    def body(self):
        return re.sub(r"\s+", " ", " ".join(self.text)).strip()


def japanese_date(text: str) -> date | None:
    text = unicodedata.normalize("NFKC", text)
    match = re.search(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    if match:
        parts = tuple(map(int, match.groups()))
    else:
        match = re.search(r"(令和|平成)(元|\d+)年\s*(\d{1,2})月\s*(\d{1,2})日", text)
        if not match:
            return None
        era, year, month, day = match.groups()
        parts = ((2018 if era == "令和" else 1988) + (1 if year == "元" else int(year)), int(month), int(day))
    try:
        return date(*parts)
    except ValueError:
        return None


def classify(title: str) -> str:
    if any(t in title for t in ["会議", "審議会", "検討会", "研究会", "委員会", "分科会", "部会", "議事録"]):
        return "専門家会議"
    if any(t in title for t in ["法改正", "改正法", "法律", "法令", "省令", "施行", "政令"]):
        return "法令改正"
    if any(t in title for t in ["指針", "ガイドライン", "マニュアル", "手引", "通達"]):
        return "指針・ガイドライン"
    return "社会ニュース"


def rank_domestic(a: Article) -> Article:
    title = a.title
    if a.retracted or not a.url or any(t in title for t in EXCLUDE):
        a.score = 0
        return a
    # Do not qualify stories only because boilerplate contains ministry names.
    text = title + " " + a.abstract[:12000]
    a.tags = [t for t in RELEVANT if t in text]
    if not a.tags:
        a.score = 0
        return a
    a.score = 5 + sum(3 if t in title else 1 for t in a.tags)
    if a.category in {"法令改正", "指針・ガイドライン"}:
        a.score += 4
    if "開催案内" in title or "開催します" in title:
        a.score -= 5
    return a


def extract_pdf(payload: bytes) -> str:
    if not shutil.which("pdftotext"):
        raise FetchError("pdftotext未導入：PDFの内容未取得")
    with tempfile.TemporaryDirectory(prefix="research-pdf-") as tmp:
        pdf = Path(tmp) / "source.pdf"
        pdf.write_bytes(payload)
        try:
            result = subprocess.run(["pdftotext", "-f", "1", "-l", "12", "-enc", "UTF-8", str(pdf), "-"],
                                    capture_output=True, timeout=25, check=False)
        except subprocess.TimeoutExpired:
            raise FetchError("PDF文字抽出の時間上限：内容未取得") from None
        if result.returncode:
            raise FetchError("PDF文字抽出に失敗：内容未取得")
        text = re.sub(r"\s+", " ", result.stdout.decode("utf-8", "replace")).strip()
        if len(text) < 50:
            raise FetchError("画像PDF等のため文字抽出不可：OCR未対応")
        return text[:16000]


def load_page(url: str) -> Page:
    payload = request(url, attempts=2, timeout=35)
    if payload.startswith(b"%PDF"):
        return Page("<main>" + html.escape(extract_pdf(payload)) + "</main>")
    return Page(payload.decode("utf-8", "replace"))


def enrich(a: Article, max_documents: int = 2) -> Article:
    """Actual retrieved body/excerpts, never inferred meeting conclusions."""
    try:
        page = load_page(a.url)
    except FetchError as exc:
        a.content_basis = "本文取得失敗：" + str(exc)
        return a
    if page.body:
        a.abstract = page.body[:6000]
        a.content_basis = "公式ページ本文の抜粋" if a.source.startswith("厚生労働省") else "報道本文の抜粋"
    else:
        a.content_basis += "（本文の文字を抽出できず、取得済み概要のみ）"
    if urlsplit(a.url).path.lower().endswith(".pdf"):
        a.content_basis = "PDF冒頭最大12頁の文字抽出・抜粋"
    if "開催案内" in a.title or "開催します" in a.title:
        a.content_basis += "（開催案内。会議の結論ではありません）"
    if a.category == "専門家会議":
        docs = []
        for href, label in page.links:
            url = official_url(href, a.url)
            if url and urlsplit(url).path.lower().endswith(".pdf") and not any(t in label for t in ["名簿", "座席", "傍聴", "表紙"]):
                docs.append((url, label))
        # Prefer substantive material over attendance lists or an agenda alone.
        docs.sort(key=lambda x: ("議事次第" in x[1], not any(t in x[1] for t in ["概要", "とりまとめ", "報告書", "指針"])))
        chosen, groups = [], set()
        for doc in docs:
            label = unicodedata.normalize("NFKC",doc[1])
            number = re.search(r"資料\s*(?:No\.?)?\s*(\d+)",label)
            group = number.group(1) if number else label
            if group not in groups and len(chosen)<max_documents:
                chosen.append(doc)
                groups.add(group)
        for doc in docs:
            if len(chosen)<max_documents and doc not in chosen:
                chosen.append(doc)
        for url, label in chosen:
            try:
                text = extract_pdf(request(url, attempts=1, timeout=35))
                a.abstract += "\n\n【資料：" + label + "・冒頭最大12頁の抜粋】\n" + text[:10000]
                a.documents.append({"label": label, "url": url, "status": "取得済み（冒頭最大12頁・抜粋）"})
                if "会議資料PDF" not in a.content_basis:
                    a.content_basis += "＋会議資料PDFの抜粋"
            except FetchError as exc:
                a.documents.append({"label": label, "url": url, "status": str(exc)})
    return rank_domestic(a)


def mhlw_updates(since: date, until: date, config: dict):
    items = parse_feed(request(MHLW + "/stf/news.rdf"), until-timedelta(days=30), until)
    result = []
    for a in items:
        a.category = classify(a.title)
        a.published_basis = "公式新着フィードの掲載日"
        a.content_basis = "公式新着フィードの概要"
        if rank_domestic(a).score > 0:
            result.append(a)
    result.sort(key=lambda a: (a.score, a.published), reverse=True)
    limit = config.get("domestic_max_pages", 12)
    return [enrich(a) for a in result[:limit]], len(result) > limit


def meeting_rows(page: Page, title: str, base: str, since: date, until: date) -> list[Article]:
    items = []
    for row in page.rows:
        held = japanese_date(row["text"])
        if not held or not since <= held <= until:
            continue
        links = [(official_url(href, base), label) for href, label in row["links"]]
        links = [(u, t) for u, t in links if u]
        # Only rows with minutes/materials. An announcement alone does not
        # establish that a meeting took place or a policy was decided.
        content_links = [(u, t) for u, t in links if any(k in t for k in ["議事録", "議事要旨", "資料", "とりまとめ", "報告書"])]
        if not content_links:
            continue
        content_links.sort(key=lambda x: not any(k in x[1] for k in ["議事録", "議事要旨"]))
        url, label = content_links[0]
        number = re.search(r"第\s*[0-9０-９]+\s*回", row["text"])
        a = Article.create(title=title + (" " + number.group() if number else "") + "（" + label + "）",
             url=url, source="厚生労働省・審議会", kind="政策・公式発表", category="専門家会議",
             published=held.isoformat(), event_date=held.isoformat(),
             published_basis="会議開催日（公開日とは異なります）", abstract=row["text"],
             content_basis="公式会議一覧の議題。議論・決定の内容は未確認")
        # An archive PDF link can itself be the main source.
        items.append(rank_domestic(a))
    return items


def meetings(since: date, until: date, config: dict):
    main = load_page(SHINGI)
    experts_url = next((official_url(href, SHINGI) for href, label in main.links if label == "労働基準局"), EXPERTS)
    experts = load_page(experts_url)
    candidates = []
    for href, label in main.links + experts.links:
        url = official_url(href, SHINGI)
        if not url or not any(t in label for t in RELEVANT):
            continue
        if not ("/shingi" in urlsplit(url).path or any(t in label for t in ["検討会", "ワーキンググループ", "研究会"])):
            continue
        old = re.search(r"令和([0-9０-９]+)年度", label)
        if old and int(unicodedata.normalize("NFKC", old.group(1)))+2018 < until.year-1:
            continue
        if "平成" in label or "令和元年" in label or re.search(r"20(?:0\d|1\d|2[0-3])年", label):
            continue
        if any(t in label for t in ["指定・登録制度改革", "労働災害防止団体改革"]):
            continue
        priority = sum(t in label for t in ["ストレスチェック", "健康保持増進", "一般健康診断", "個人事業者", "安全衛生分科会", "安全衛生", "化学物質管理", "労働基準関係", "労働者」に関する"])
        if old and int(unicodedata.normalize("NFKC", old.group(1)))+2018 == until.year:
            priority += 3
        if label == "安全衛生分科会":
            priority += 3
        if any(t in label for t in ["労働基準関係法制", "労働基準法における", "労災保険部会"]):
            priority += 3
        candidates.append((priority, url, label))
    unique = {}
    for priority, url, label in candidates:
        if url not in unique or priority > unique[url][0]:
            unique[url] = (priority, url, label)
    indexes = sorted(unique.values(), key=lambda x: (-x[0], x[2]))
    limit = config.get("meeting_indexes", 12)
    result = []
    for _, url, label in indexes[:limit]:
        page = load_page(url)
        result.extend(meeting_rows(page, label, url, until-timedelta(days=30), until))
    # Enrich a bounded number of the strongest recent records.
    result = sorted(deduplicate(result), key=lambda a: (a.score, a.published), reverse=True)
    max_details = config.get("domestic_max_pages", 12)
    return [enrich(a) for a in result[:max_details]], len(indexes)>limit or len(result)>max_details


def news(since: date, until: date, config: dict):
    try:
        payload = request("https://www3.nhk.or.jp/rss/news/cat0.xml", attempts=1, timeout=35)
    except FetchError:
        payload = request("https://news.web.nhk/rss/news/cat0.xml", attempts=1, timeout=35)
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        raise FetchError("NHKニュースRSSが有効なXMLではありません") from None
    items = []
    for item in root.findall(".//item"):
        fields = {n.tag.rsplit("}", 1)[-1]: "".join(n.itertext()).strip() for n in item}
        url = safe_url(fields.get("link", ""))
        published = parse_date(fields.get("pubDate", ""))
        if not url or urlsplit(url).hostname not in NEWS_HOSTS or not published or not until-timedelta(days=30)<=published<=until:
            continue
        a = Article.create(title=fields.get("title", ""), url=url, source="NHK", kind="社会ニュース",
                  category="社会ニュース", published=published.isoformat(), abstract=plain(fields.get("description", "")),
                  content_basis="報道RSSの概要", published_basis="報道掲載日")
        if rank_domestic(a).score>0:
            items.append(a)
    if not root.findall(".//channel"):
        raise FetchError("NHKニュースRSSの形式が想定と異なります")
    limit = config.get("domestic_max_pages", 12)
    return [enrich(a, 0) for a in sorted(items,key=lambda a:(a.score,a.published),reverse=True)[:limit]],len(items)>limit


def select_domestic(items: list[Article], today: date, limit: int = 5):
    cutoff = (today-timedelta(days=30)).isoformat()
    pool = sorted([rank_domestic(a) for a in items if a.category and cutoff<=a.published<=today.isoformat()],
                  key=lambda a:(a.score,a.published),reverse=True)
    pool = [a for a in deduplicate(pool) if a.score>0 and not any(t in a.title for t in ["開催案内","開催します","開催について"])]
    selected = []
    # Diverse categories when present; never invent or repeat an old item to
    # fill five slots. Up to one item of each category before the remainder.
    for category in CATEGORIES:
        a = next((a for a in pool if a.category==category),None)
        if a and len(selected)<limit:
            selected.append(a)
    for a in pool:
        if len(selected)>=limit:
            break
        if a not in selected:
            selected.append(a)
    return selected
