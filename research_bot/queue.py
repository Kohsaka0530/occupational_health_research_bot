"""A publication-date bounded, aging priority queue for daily briefs."""
from datetime import date

from .core import Article
from .journals import eligible_journal, journal_key, journal_group, CORE


def publication_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if len(value) >= 10 else None
    except ValueError:
        return None


def daily_selection(articles: list[Article], today: date, limit: int, max_lag: int = 30):
    eligible, expired, undated = [], 0, 0
    for article in articles:
        if article.kind == "政策・公式発表" or article.retracted or article.score <= 0:
            continue
        if not eligible_journal(article.journal) or article.kind == "プレプリント":
            continue
        published = publication_date(article.published)
        if published is None:
            undated += 1
            continue
        age = (today - published).days
        if age > max_lag:
            expired += 1
        elif age >= 0:
            eligible.append(article)

    def age(a):
        return (today - publication_date(a.published)).days

    def priority(a):
        evidence = a.evidence.lower()
        bonus = 3 if any(k in evidence for k in ["randomized", "systematic review", "meta-analysis"]) else 0
        # Recency alone never wins; waiting increases priority and imminent
        # deadlines take precedence. A limit cannot guarantee no overflow.
        return a.score + age(a) * 0.6 + bonus + (40 if age(a) >= max_lag - 2 else 0)

    selected = []
    pool = eligible.copy()
    def take(a):
        selected.append(a)
        pool.remove(a)
    # Meet deadlines first. Then represent each core occupational journal if
    # it has an eligible unsent study; never manufacture a quota when empty.
    urgent = sorted([a for a in pool if age(a) >= max_lag - 2], key=priority, reverse=True)
    for a in urgent[:limit]:
        take(a)
    for group in CORE:
        if len(selected) >= limit:
            break
        if any(journal_group(a.journal) == group for a in selected):
            continue
        options = [a for a in pool if journal_group(a.journal) == group]
        if options:
            take(max(options, key=priority))
    while pool and len(selected) < limit:
        def key(a):
            same_journal = sum(journal_key(b.journal) == journal_key(a.journal) for b in selected)
            return (priority(a) - 3 * same_journal, bool(a.abstract), a.id)
        candidate = max(pool, key=key)
        take(candidate)
    return selected, {
        "eligible": len(eligible), "selected": len(selected), "deferred": len(pool),
        "expired": expired, "undated": undated, "max_lag_days": max_lag,
        "near_deadline_deferred": sum(age(a) >= max_lag - 2 for a in pool),
        "rule": "公表日から最大30日。重要度・研究種別・待機日数で選定し、同一誌への集中を緩和。件数超過時の全件配信は保証しません。",
    }
