from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from .core import utcnow
from .net import FetchError, request
from .render import render_html, render_text
from .store import Store


class MailError(RuntimeError):
    pass


def mail_config() -> tuple[str, str, str]:
    key = os.environ.get("RESEARCH_MAIL_API_KEY", "")
    sender = os.environ.get("MAIL_FROM", "")
    recipient = os.environ.get("DIGEST_TO", "")
    if not all([key, sender, recipient]):
        raise MailError("送信には RESEARCH_MAIL_API_KEY / MAIL_FROM / DIGEST_TO が必要です")
    if any("\n" in v or "\r" in v for v in [sender, recipient]) or "@" not in sender or "@" not in recipient:
        raise MailError("メールアドレスの形式を確認してください")
    return key, sender, recipient


def create_outbox(report: dict, data_dir: Path) -> dict:
    if report.get("demo") or (report["articles"] and not report.get("summary")) or not (report["articles"] or report.get("policy_updates")):
        raise MailError("デモ・資料一覧・空のブリーフは配信しません")
    if any(s["status"] == "error" for s in report["sources"]):
        raise MailError("必須情報源に取得失敗があります。復旧後に再実行してください")
    if report.get("policy_updates"):
        from .summarize import validate_domestic, SummaryError
        from .core import Article
        try:
            validate_domestic(report.get("domestic_summary"),[Article(**a) for a in report["policy_updates"]])
        except SummaryError:
            raise MailError("国内動向の要約が未生成または出典対応が不正です。メールは送信しません") from None
    _, sender, recipient = mail_config()
    content = {"from": sender, "to": [recipient],
               "subject": "産業保健リサーチブリーフ｜" + report["created_at_jst"][:10],
               "html": render_html(report), "text": render_text(report)}
    digest_id = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    outbox = {"digest_id": digest_id, "created_at": utcnow().isoformat(),
              "article_ids": [a["id"] for a in report["articles"] + report.get("policy_updates", [])], "message": content}
    path = data_dir / "outbox.json"
    # All mail attempts reuse this exact payload and idempotency key.
    fd = os.open(str(path) + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(outbox, f, ensure_ascii=False)
    Path(str(path) + ".tmp").replace(path)
    return outbox


def deliver(outbox: dict, store: Store, data_dir: Path):
    key, _, _ = mail_config()
    digest_id = outbox["digest_id"]
    path = data_dir / "outbox.json"
    if store.delivery_status(digest_id) == "sent":
        path.unlink(missing_ok=True)
        return
    if utcnow() - datetime.fromisoformat(outbox["created_at"]) >= timedelta(hours=23):
        raise MailError("未確定の送信が23時間以上残っています。Resendの送信履歴を確認してください。自動再送を停止しました")
    store.begin_delivery(digest_id, utcnow())
    try:
        response = json.loads(request("https://api.resend.com/emails", data=outbox["message"],
                           headers={"Authorization": "Bearer " + key, "Idempotency-Key": "research-brief-" + digest_id},
                           attempts=1))
        if not isinstance(response.get("id"), str) or not response["id"]:
            raise MailError("メールAPIの送信受付を確認できません")
    except (FetchError, ValueError, AttributeError) as exc:
        raise MailError(f"送信受付未確認。outboxを保持しました。次回は同じキーで再試行します ({type(exc).__name__})") from None
    store.mark_sent(digest_id, response["id"], outbox["article_ids"], utcnow())
    path.unlink(missing_ok=True)


def resume_outbox(store: Store, data_dir: Path) -> bool:
    path = data_dir / "outbox.json"
    if not path.exists():
        return False
    deliver(json.loads(path.read_text(encoding="utf-8")), store, data_dir)
    return True
