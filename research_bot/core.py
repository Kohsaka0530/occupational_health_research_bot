from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from .journals import eligible_journal, journal_group
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"p", "br", "div", "section", "jats:p"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value: str | None) -> str:
    parser = TextParser()
    parser.feed(value or "")
    return re.sub(r"\s+", " ", html.unescape("".join(parser.parts))).strip()


def safe_url(url: str) -> str:
    p = urlsplit(url)
    return url if p.scheme == "https" and p.hostname and not p.username and not p.password else ""


def normalize_title(title: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", title).lower())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Article:
    id: str
    title: str
    url: str
    source: str
    kind: str
    published: str
    abstract: str = ""
    doi: str = ""
    journal: str = ""
    authors: str = ""
    evidence: str = ""
    retracted: bool = False
    tags: list[str] = field(default_factory=list)
    score: int = 0
    category: str = ""
    content_basis: str = ""
    published_basis: str = ""
    event_date: str = ""
    documents: list[dict] = field(default_factory=list)

    @property
    def title_key(self):
        return normalize_title(self.title)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def create(cls, *, title, url, source, kind, published, doi="", **kwargs):
        doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi.strip(), flags=re.I).lower()
        title = plain(title)
        url = safe_url(url)
        key = "doi:" + doi if doi else source + ":" + url
        return cls(id=hashlib.sha256(key.encode()).hexdigest()[:24], title=title,
                   url=url, source=source, kind=kind, published=published,
                   doi=doi, **kwargs)


TOPICS = {
    "新しい働き方": ["gig worker", "gig econom", "platform work", "platform-based", "delivery rider", "delivery worker", "freelanc", "precarious employ", "non-standard employ", "自営業", "個人事業者", "フリーランス", "ギグ", "配達員"],
    "過重労働": ["long working hour", "working hours", "overwork", "shift work", "night work", "workload", "karoshi", "過労", "過重労働", "労働時間", "交替制"],
    "メンタルヘルス": ["mental health", "depress", "anxiety", "burnout", "psychosocial", "suicid", "メンタル", "精神障害", "ストレス", "自殺"],
    "デジタルヘルス": ["digital health", "wearable", "machine learning", "artificial intelligence", "algorithmic management", "telehealth", "digital intervention", "デジタル", "機械学習", "ウェアラブル"],
    "中小企業・健康経営": ["small business", "small enterprise", "small and medium", "smes", "workplace health promotion", "health and productivity", "健康経営", "中小企業", "中小規模"],
    "安全・職業性疾患": ["occupational", "work-related", "work related", "workplace", "injury", "injuries", "accident", "heat stress", "chemical exposure", "silicosis", "asbestos", "労災", "労働災害", "安全衛生", "熱中症", "職業性", "産業保健", "産業医"],
}
WORK_TERMS = ["worker", "workplace", "occupational", "employment", "unemploy", "employee", "working", "job loss", "job insecur", "retirement", "labor", "labour", "freelanc", "gig econom", "delivery rider", "shift work", "労働", "職場", "雇用", "産業保健", "産業医", "健康経営", "個人事業者", "自営業", "フリーランス"]
HEALTH_TERMS = ["health", "safety", "injur", "accident", "disease", "mortality", "mental", "stress", "burnout", "fatigue", "exposure", "well-being", "wellbeing", "sick", "welfare", "健康", "疾病", "事故", "災害", "過労", "精神", "安全", "メンタル"]
MAJOR_JOURNALS = {"lancet", "new england journal of medicine", "n engl j med", "jama", "bmj", "annals of internal medicine", "ann intern med"}


def contains(text: str, term: str) -> bool:
    if term.isascii():
        end = r"\b" if term in {"labor", "labour", "smes"} else ""
        return re.search(r"\b" + re.escape(term) + end, text) is not None
    return term in text


def rank(article: Article) -> Article:
    if article.category:
        from .domestic import rank_domestic
        return rank_domestic(article)
    title = article.title.lower()
    text = title + " " + article.abstract.lower()
    # Grant names such as 'Research Council for Health, Working Life and
    # Welfare' must not turn an unrelated clinical trial into a work study.
    text = re.split(r"\b(?:funding|acknowledgements?|acknowledgments?|conflict of interest)\b", text, maxsplit=1)[0]
    # A minister's name on an award is not evidence of occupational relevance.
    for boilerplate in ["厚生労働大臣", "厚生労働副大臣", "厚生労働省"]:
        text = text.replace(boilerplate, "")
        title = title.replace(boilerplate, "")
    article.tags = [tag for tag, terms in TOPICS.items() if any(contains(text, t) for t in terms)]
    weak_context = {"employment", "unemploy", "working", "retirement", "labor", "labour"}
    title_work = any(contains(title, t) for t in WORK_TERMS)
    strong_work = any(contains(text, t) for t in WORK_TERMS if t not in weak_context)
    weak_mentions = sum(len(re.findall(r"\b" + re.escape(t), text)) for t in weak_context)
    work = title_work or strong_work or weak_mentions >= 3
    health = any(contains(text, t) for t in HEALTH_TERMS)
    group = journal_group(article.journal)
    if group in {"JOEM", "JOH", "SJWEH", "OEM"}:
        # Occupational journals provide context even for titles such as
        # 'Silica exposure and lung cancer' without the word 'worker'.
        work = True
        if not article.tags:
            article.tags = ["産業医学全般"]
        health = True
    if group and work and health and not article.tags:
        article.tags = ["労働と健康・公衆衛生"]
    uncurated = article.source in {"Crossref", "Europe PMC"} and not eligible_journal(article.journal)
    nonresearch = any(t.strip().lower() in {"editorial", "comment", "published erratum", "news"}
                      for t in article.evidence.split(","))
    nonresearch = nonresearch or "this special communication" in text or "this perspective" in text
    if article.retracted or not article.url or not work or not health or not article.tags or uncurated or nonresearch:
        article.score = 0
        return article
    article.score = 3 + sum(3 if any(contains(title, t) for t in terms) else 1
                            for terms in TOPICS.values() if any(contains(text, t) for t in terms))
    if article.tags and article.tags[0] == "新しい働き方":
        article.score += 3
    if article.journal.lower().rstrip(".") in MAJOR_JOURNALS:
        article.score += 2
    if group:
        article.score += 3
    if article.kind == "政策・公式発表":
        article.score += 2
    return article


def deduplicate(articles: list[Article]) -> list[Article]:
    """Prefer the richer abstract; match both DOI and exact normalized titles."""
    result: list[Article] = []
    for article in sorted(articles, key=lambda a: (a.kind != "プレプリント", len(a.abstract)), reverse=True):
        match = next((a for a in result if a.id == article.id or
                      (a.url and a.url == article.url) or
                      (a.doi and a.doi == article.doi) or
                      (a.title_key and a.title_key == article.title_key)), None)
        if match is None:
            result.append(article)
        elif article.retracted:
            match.retracted = True
            match.score = 0
    return result


def select(articles: list[Article], limit: int) -> list[Article]:
    key = lambda a: (a.score + (3 if a.abstract else -2), a.published)
    ranked = sorted((a for a in articles if a.score > 0), key=key, reverse=True)
    selected: list[Article] = []
    # Keep some room for occupational hazards beyond the priority themes.
    broad = next((a for a in ranked if a.kind != "政策・公式発表" and "安全・職業性疾患" in a.tags and
                  not any(t in a.tags for t in ["新しい働き方", "メンタルヘルス", "デジタルヘルス"])), None)
    policy = next((a for a in ranked if a.kind == "政策・公式発表"), None)
    for a in [broad, policy]:
        if a is not None and a not in selected and len(selected) < limit:
            selected.append(a)
    for a in ranked:
        if a not in selected and len(selected) < limit:
            if a.source == "Crossref" and sum(s.source == "Crossref" for s in selected) >= 3:
                continue
            selected.append(a)
    return sorted(selected, key=key, reverse=True)
