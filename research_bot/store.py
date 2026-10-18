from __future__ import annotations

import json
import hashlib
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .core import Article, utcnow


def fingerprints(article: Article) -> set[str]:
    return {kind+":"+hashlib.sha256(value.encode()).hexdigest() for kind,value in
            [("url",article.url),("doi",article.doi),("title",article.title_key)] if value}


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS articles (
                id TEXT PRIMARY KEY, title_key TEXT NOT NULL, doi TEXT NOT NULL,
                payload TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                reported_at TEXT
            );
            CREATE INDEX IF NOT EXISTS article_titles ON articles(title_key);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reported_fingerprints (key TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS deliveries (
                digest_id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL,
                provider_id TEXT
            );
        ''')

    def close(self):
        self.db.close()

    def get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str):
        self.db.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.db.commit()

    def add(self, articles: list[Article]):
        now = utcnow().isoformat()
        with self.db:
            for a in articles:
                # Merge newly indexed copies without losing previous digest history.
                row = self.db.execute("SELECT id FROM articles WHERE id=? OR title_key=? OR (doi<>'' AND doi=?) OR json_extract(payload,'$.url')=? ORDER BY reported_at IS NULL LIMIT 1",
                                      (a.id, a.title_key, a.doi, a.url)).fetchone()
                if row:
                    self.db.execute("UPDATE articles SET last_seen=? WHERE id=?", (now, row[0]))
                    old = self.db.execute("SELECT payload FROM articles WHERE id=?", (row[0],)).fetchone()
                    previous = Article(**json.loads(old[0]))
                    a.id = row[0]
                    if len(previous.abstract) > len(a.abstract):
                        a.abstract = previous.abstract
                        a.documents = previous.documents
                        a.content_basis = previous.content_basis
                    # Retraction signals must override earlier, richer records.
                    a.retracted = a.retracted or previous.retracted
                    from .core import rank
                    rank(a)
                    self.db.execute("UPDATE articles SET payload=? WHERE id=?", (json.dumps(a.to_dict(), ensure_ascii=False), a.id))
                else:
                    self.db.execute("INSERT INTO articles VALUES (?,?,?,?,?,?,NULL)",
                                    (a.id, a.title_key, a.doi, json.dumps(a.to_dict(), ensure_ascii=False), now, now))

    def candidates(self, since: datetime) -> list[Article]:
        rows = self.db.execute("SELECT payload FROM articles WHERE reported_at IS NULL AND first_seen>=?",
                               (since.isoformat(),)).fetchall()
        reported={row[0] for row in self.db.execute('SELECT key FROM reported_fingerprints')}
        articles=[Article(**json.loads(row[0])) for row in rows]
        return [a for a in articles if not fingerprints(a)&reported]

    def seed(self, payload: dict):
        """Import only hashes and dates; never import keys, addresses or messages."""
        import re
        keys=payload.get('reported_keys',[])
        if not isinstance(keys,list) or any(not isinstance(k,str) or not re.fullmatch(r'(?:url|doi|title):[0-9a-f]{64}',k) for k in keys):
            raise ValueError('初期送信履歴の形式が不正です')
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO reported_fingerprints VALUES (?)',[(k,) for k in keys])
            for key in ['trial_started_on','last_sent']:
                if payload.get(key):
                    # Validate dates without printing any input values.
                    datetime.fromisoformat(payload[key])
                    self.db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)',(key,payload[key]))

    def pending_delivery(self) -> bool:
        return self.db.execute("SELECT 1 FROM deliveries WHERE status='pending' LIMIT 1").fetchone() is not None

    def observations(self, now: datetime) -> dict:
        counts: dict[str, dict[str, int]] = {}
        for payload, seen in self.db.execute("SELECT payload, first_seen FROM articles WHERE first_seen>=?",
                                            ((now - timedelta(days=90)).isoformat(),)):
            from .core import rank
            item = rank(Article(**json.loads(payload)))
            if item.score <= 0:
                continue
            week = datetime.fromisoformat(seen).strftime("%G-W%V")
            for tag in item.tags:
                counts.setdefault(week, {}).setdefault(tag, 0)
                counts[week][tag] += 1
        return {"weekly_topic_counts": counts,
                "caution": "このエージェントが初めて取得した時点の件数。収集範囲・索引更新の影響があり、分野全体の増加や研究の新規性は示しません。"}

    def mark_preview(self, ids: list[str], now: datetime):
        # A reviewable fully summarized brief counts as reported; source-only previews do not.
        with self.db:
            self.db.executemany("UPDATE articles SET reported_at=? WHERE id=?", [(now.isoformat(), a) for a in ids])

    def begin_delivery(self, digest_id: str, now: datetime):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO deliveries VALUES (?, 'pending', ?, NULL)", (digest_id, now.isoformat()))

    def delivery_status(self, digest_id: str) -> str | None:
        row = self.db.execute("SELECT status FROM deliveries WHERE digest_id=?", (digest_id,)).fetchone()
        return row[0] if row else None

    def mark_sent(self, digest_id: str, provider_id: str, ids: list[str], now: datetime):
        with self.db:
            for identifier in ids:
                row=self.db.execute('SELECT payload FROM articles WHERE id=?',(identifier,)).fetchone()
                if row:
                    self.db.executemany('INSERT OR IGNORE INTO reported_fingerprints VALUES (?)',
                                        [(k,) for k in fingerprints(Article(**json.loads(row[0])))])
            self.db.execute("UPDATE deliveries SET status='sent', provider_id=? WHERE digest_id=?", (provider_id, digest_id))
            self.db.executemany("""UPDATE articles SET reported_at=? WHERE id=? OR
                json_extract(payload,'$.url')=(SELECT json_extract(payload,'$.url') FROM articles WHERE id=?)""",
                [(now.isoformat(), a, a) for a in ids])
            self.db.execute("INSERT INTO meta VALUES ('last_sent',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (now.isoformat(),))
            from zoneinfo import ZoneInfo
            self.db.execute("INSERT OR IGNORE INTO meta VALUES ('trial_started_on',?)",
                            (now.astimezone(ZoneInfo('Asia/Tokyo')).date().isoformat(),))
