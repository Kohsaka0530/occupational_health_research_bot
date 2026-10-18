from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import sys
import tomllib
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .core import deduplicate, rank, select, utcnow
from .demo import demo_data
from .mail import MailError, create_outbox, deliver, mail_config, resume_outbox
from .net import FetchError
from .render import write_report
from .sources import COLLECTORS
from .store import Store
from .summarize import SummaryError, summarize, summarize_domestic
from .queue import daily_selection
from .domestic import select_domestic

ROOT = Path(__file__).resolve().parent.parent
JST = ZoneInfo("Asia/Tokyo")


def due(last_sent: str | None, now: datetime, interval_hours: int) -> bool:
    if not last_sent:
        return True
    # Calendar days avoid a missed delivery due to a few minutes' scheduler jitter.
    days = max(1, (interval_hours + 23) // 24)
    return (now.astimezone(JST).date() - datetime.fromisoformat(last_sent).astimezone(JST).date()).days >= days


def trial_active(started_on: str | None, now: datetime, days: int) -> bool:
    if not days or not started_on:
        return True
    from datetime import date
    return (now.astimezone(JST).date()-date.fromisoformat(started_on)).days < days


@contextmanager
def run_lock(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "run.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("別の実行が進行中です") from None
        yield


def doctor() -> int:
    print("Python:", sys.version.split()[0])
    print("pdftotext: " + ("利用可能" if shutil.which("pdftotext") else "未導入（会議資料PDFの文字抽出にはpoppler-utilsが必要）"))
    for name in ["RESEARCH_LLM_API_KEY", "OPENAI_API_KEY", "RESEARCH_MAIL_API_KEY", "RESEARCH_LLM_MODEL", "DIGEST_TO", "MAIL_FROM"]:
        print(name + ": " + ("設定あり" if os.environ.get(name) else "未設定"))
    print("秘密値は表示していません。接続・認証の成功は run で検証します。")
    return 0


def execute(args) -> int:
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    limit = config["digest"]["max_items"]
    backlog_days = config["digest"].get("backlog_days", 30)
    trial_days = config["digest"].get("trial_days",0)
    if not isinstance(trial_days,int) or trial_days<0:
        raise RuntimeError("trial_days は0以上の整数で指定してください")
    if not 1 <= backlog_days <= 30:
        raise RuntimeError("backlog_days は1〜30にしてください")
    if not 1 <= limit <= 15:
        raise RuntimeError("max_items は1〜15にしてください（分量・API費用の上限）")
    sc = config["sources"]
    if not 1 <= sc.get("domestic_max_items",5) <= 5 or not 1 <= sc.get("meeting_indexes",12) <= 30 or not 1 <= sc.get("domestic_max_pages",12) <= 20:
        raise RuntimeError("国内動向は最大5件、会議一覧巡回は1〜30件、詳細取得は1〜20件にしてください")
    if not 1 <= sc["max_pages"] <= 5 or not 1 <= sc["page_size"] <= 100:
        raise RuntimeError("max_pages は1〜5、page_size は1〜100にしてください")
    now = utcnow()
    data_dir = args.data_dir / "demo" if args.demo else args.data_dir
    output_dir = args.output / "demo" if args.demo else args.output
    with run_lock(data_dir):
        store = Store(data_dir / "state.sqlite3")
        try:
            if args.send:
                if not trial_active(store.get("trial_started_on"),now,trial_days):
                    print(f"{trial_days}日間の試用期間が終了しました。メール送信を停止しています。")
                    return 0
                mail_config()
                if resume_outbox(store, data_dir):
                    print("保留していたメールの送信受付を確認しました。新規送信は次回に行います。")
                    return 0
            if (args.scheduled or (args.send and trial_days)) and not due(store.get("last_sent"), now, config["digest"]["interval_hours"]):
                print("本日の配信は完了しています（日本時間の日付で判定）。")
                return 0
            until = now.astimezone(JST).date()
            since = until - timedelta(days=config["digest"]["initial_days"])
            statuses = []
            summary = None
            queue_status = None
            policy_updates = []
            if args.demo:
                articles, summary = demo_data()
                statuses = [{"name": "架空データ（実情報未取得）", "status": "ok", "count": len(articles)}]
            else:
                enabled = [name for name in COLLECTORS if sc.get(name)]
                if not enabled:
                    raise RuntimeError("少なくとも1つの情報源を有効にしてください")
                for name in enabled:
                    last = store.get("last_fetch_" + name)
                    source_since = (datetime.fromisoformat(last).date() - timedelta(days=config["digest"]["overlap_days"])) if last else since
                    since = min(since, source_since)
                    print(f"収集: {name} ({source_since} 〜 {until})", flush=True)
                    try:
                        fetched, capped = COLLECTORS[name](source_since, until, sc)
                        store.add(deduplicate([rank(a) for a in fetched]))
                        statuses.append({"name": name, "status": "ok", "count": len(fetched), "capped": capped})
                        # Failed calls never advance their individual watermark.
                        store.set("last_fetch_" + name, now.isoformat())
                    except (FetchError, ValueError, KeyError, TypeError) as exc:
                        statuses.append({"name": name, "status": "error", "count": 0, "error": str(exc)})
                        print(f"取得失敗: {name}: {exc}", file=sys.stderr)
                candidates = [rank(a) for a in store.candidates(now - timedelta(days=90))]
                if sc.get("curated_journals"):
                    articles, queue_status = daily_selection(candidates, until, limit, backlog_days)
                else:
                    articles = select([a for a in candidates if not a.category], limit)
                policy_updates = select_domestic(candidates, until, sc.get("domestic_max_items",5))
            observations = store.observations(now)
            report = {"schema_version": 1, "created_at": now.isoformat(),
                      "created_at_jst": now.astimezone(JST).strftime("%Y-%m-%d %H:%M JST"),
                      "since": since.isoformat(), "until": until.isoformat(), "demo": args.demo,
                      "articles": [a.to_dict() for a in articles], "summary": summary,
                      "sources": statuses, "observations": observations,
                      "queue": queue_status, "policy_updates": [a.to_dict() for a in policy_updates],
                      "domestic_summary": None}
            failed = any(s["status"] == "error" for s in statuses)
            summary_error = None
            if not args.demo and not args.no_ai and articles:
                try:
                    report["summary"] = summarize(articles, observations, config["llm"])
                except SummaryError as exc:
                    summary_error = str(exc)
                    report["summary_error"] = summary_error
            if not args.demo and not args.no_ai and policy_updates:
                try:
                    report["domestic_summary"] = summarize_domestic(policy_updates, config["llm"])
                except SummaryError as exc:
                    summary_error = str(exc)
                    report["domestic_summary_error"] = summary_error
            path = write_report(report, output_dir)
            print(f"生成: {path} ({len(articles)}件)")
            if summary_error:
                print(summary_error, file=sys.stderr)
            if failed or summary_error:
                print("未完了の処理があります。資料一覧と収集状況を保存しました。メールは送信していません。", file=sys.stderr)
                return 2
            if args.send and (articles or policy_updates):
                outbox = create_outbox(report, data_dir)
                deliver(outbox, store, data_dir)
                print("メールサービスの送信受付を確認しました（受信箱への到達は別途確認してください）。")
            elif args.send:
                print("新着がないためメールは送信しません。")
            elif not args.demo:
                print("プレビューのみ。メールは送信していません。")
            return 0
        finally:
            store.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="産業保健リサーチブリーフ — 出典付き日本語要約")
    parser.add_argument("command", choices=["run", "doctor"], nargs="?", default="run")
    parser.add_argument("--config", type=Path, default=ROOT / "config.toml")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output", type=Path, default=ROOT / "output")
    parser.add_argument("--demo", action="store_true", help="架空データによるオフライン表示見本（送信不可）")
    parser.add_argument("--no-ai", action="store_true", help="要約せず、取得した原文資料を表示")
    parser.add_argument("--send", action="store_true", help="設定した宛先に送信（初期値は送信しない）")
    parser.add_argument("--scheduled", action="store_true", help="配信間隔を日本時間の日付で判定（初期値は毎日）")
    args = parser.parse_args(argv)
    if args.send and (args.demo or args.no_ai):
        parser.error("--demo / --no-ai は --send と併用できません")
    if args.scheduled and not args.send:
        parser.error("--scheduled は --send と併用してください")
    try:
        return doctor() if args.command == "doctor" else execute(args)
    except (RuntimeError, OSError, ValueError) as exc:
        print("エラー: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
