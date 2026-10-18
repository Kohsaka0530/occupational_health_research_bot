from __future__ import annotations

import html
import json
import math
import re
from pathlib import Path

from .core import safe_url


LABELS = {"purpose": "目的", "methods": "方法", "findings": "主な結果", "limitations": "限界", "implications": "産業保健への示唆"}
DOMESTIC_LABELS = {"overview":"概要", "implications":"産業保健への意味", "status_note":"段階・注意点"}


def domestic_html(report: dict) -> list[str]:
    items = report.get("policy_updates", [])
    if not items and "domestic_summary" not in report:
        return []
    summaries = {s["id"]:s for s in (report.get("domestic_summary") or {}).get("items",[])}
    parts = ['<section id="domestic"><h2>国内動向 — 指針・法令・専門家会議・社会ニュース</h2>',
             '<p>論文10本とは別枠で最大5件。公表・会議開催の段階と、実務への意味を確認します。</p>']
    if len(items)<5:
        parts.append('<p class="meta">今回の未配信候補は'+str(len(items))+'件です。無関係な情報や既報で5件を埋めません。取得状況も併せて確認してください。</p>')
    for i,a in enumerate(items,1):
        s=summaries.get(a["id"])
        parts.extend(['<article><span class="badge">'+escape(a.get("category") or a["kind"])+'</span>',
                      '<h3>'+str(i)+'. '+escape(s["title_ja"] if s else a["title"])+'</h3>',
                      '<p class="meta">'+escape(a["source"])+ ' ／ '+escape(a["published"])+
                      ' ／ '+escape(a.get("published_basis") or "掲載日")+'<br>根拠：'+escape(a.get("content_basis") or "フィード概要")+'</p>'])
        if s:
            for k,label in DOMESTIC_LABELS.items():
                parts.append('<p><strong>'+label+'：</strong>'+escape(s[k])+'</p>')
        else:
            parts.append('<p class="meta">要約未生成。取得本文・概要の抜粋：</p><p>'+escape(a["abstract"][:1200] or "本文・資料未取得")+'</p>')
        url=safe_url(a["url"])
        if url:
            parts.append('<p><a href="'+escape(url)+'" target="_blank" rel="noopener noreferrer">原文・会議ページを読む ↗</a></p>')
        for doc in a.get("documents",[]):
            url=safe_url(doc.get("url",""))
            if url:
                parts.append('<p class="meta">資料：<a href="'+escape(url)+'">'+escape(doc.get("label","資料"))+'</a> ／ '+escape(doc.get("status","未確認"))+'</p>')
        parts.append('</article>')
    parts.append('</section>')
    return parts


def domestic_text(report: dict) -> list[str]:
    items=report.get("policy_updates",[])
    if not items and "domestic_summary" not in report:
        return []
    summaries={s["id"]:s for s in (report.get("domestic_summary") or {}).get("items",[])}
    parts=["", "国内動向（論文10本とは別枠・最大5件）", ""]
    if len(items)<5:
        parts.append(f'今回は未配信候補{len(items)}件。取得状況を併せて確認してください。')
    for i,a in enumerate(items,1):
        s=summaries.get(a["id"])
        parts.extend([f'{i}. '+(s["title_ja"] if s else a["title"]),
                      f'{a.get("category") or a["kind"]} / {a["source"]} / {a["published"]} / {a.get("published_basis", "")}',
                      '根拠：'+a.get("content_basis", "フィード概要")])
        if s:
            parts.extend(label+'：'+s[k] for k,label in DOMESTIC_LABELS.items())
        else:
            parts.append('要約未生成。取得資料の抜粋：'+(a["abstract"][:1200] or "本文未取得"))
        parts.extend([safe_url(a["url"]),""])
        for doc in a.get("documents",[]):
            parts.extend([doc.get("label","資料")+' / '+doc.get("status","未確認"),safe_url(doc.get("url",""))])
    return parts


def escape(value) -> str:
    return html.escape(str(value), quote=True)


def render_html(report: dict) -> str:
    articles = report["articles"]
    summaries = {s["id"]: s for s in (report.get("summary") or {}).get("summaries", [])}
    demo = report.get("demo", False)
    source_only = not report.get("summary") and not report.get("domestic_summary")
    banner = "動作確認用の架空データです。研究や申請書の根拠に使えません。" if demo else (
        "資料一覧モード：AI要約は未生成です。以下は取得資料の原文抜粋です。" if source_only else
        "論文抄録・国内資料の取得抜粋に基づくAI要約です。重要な判断や引用の前に原文をご確認ください。")
    if report.get("summary_origin"):
        banner += " " + report["summary_origin"]
    body = [f'<div class="notice">{escape(banner)}</div>']
    body.append(f'<p class="meta">作成：{escape(report["created_at_jst"])} ／ 取得対象：{escape(report["since"])} 〜 {escape(report["until"])}</p>')
    queue = report.get("queue")
    if queue:
        body.append('<div class="notice">本日の論文：' + str(queue["selected"]) + '本 ／ 次回以降の候補：' + str(queue["deferred"]) + '本。公表日から最大' + str(queue["max_lag_days"]) + '日以内の未配信論文を、重要度と待機日数で選びます。</div>')
        if queue["near_deadline_deferred"] or queue["expired"]:
            body.append('<p class="meta">期限間近の繰越：' + str(queue["near_deadline_deferred"]) + '本 ／ 30日超過の未配信資料：' + str(queue["expired"]) + '本。1日10本の上限により全件を配信できない場合があります。</p>')
    body.append('<p class="meta">対象誌の公表日を基準に過去30日間を再確認します。収集日と公表日は異なります。全分野・全媒体の網羅は保証しません。</p>')
    failures = [s for s in report["sources"] if s["status"] != "ok"]
    if failures:
        body.append('<div class="notice">収集に未完了の情報源があります：' + escape("、".join(s["name"] for s in failures)) + '。未取得を「新着なし」とは扱いません。</div>')
    if not articles:
        body.append('<p>今回の収集範囲に未配信の該当情報はありません。</p>')
    else:
        body.append('<nav><strong>最初の5分で確認</strong><ol>')
        for i, a in enumerate(articles[:3], 1):
            title = summaries.get(a["id"], {}).get("title_ja", a["title"])
            body.append(f'<li><a href="#item-{i}">{escape(title)}</a></li>')
        body.append('</ol></nav>')
    for i, a in enumerate(articles, 1):
        s = summaries.get(a["id"])
        title = s["title_ja"] if s else a["title"]
        basis = "公式フィードの概要" if a["kind"] == "政策・公式発表" else "取得抄録"
        if not a["abstract"]:
            basis = "題名・書誌情報のみ：抄録未取得"
        body.append(f'<article id="item-{i}"><span class="badge">{escape(a["kind"])}</span> <span class="tags">{escape(" / ".join(a["tags"]))}</span>')
        body.append(f'<h2>{i}. {escape(title)}</h2>')
        if s:
            body.append(f'<p class="original">{escape(a["title"])}</p>')
        body.append(f'<p class="meta">{escape(a["journal"] or a["source"])} ／ 公表：{escape(a["published"] or "日付未取得")}<br>取得元：{escape(a["source"])} ／ 要約の根拠：{escape(basis)} ／ 全文未確認</p>')
        if a["kind"] == "学術論文":
            body.append('<p class="meta">掲載種別は書誌情報による分類です。査読状況は個別確認していません。</p>')
        if a["kind"] == "プレプリント":
            body.append('<p class="notice">プレプリント：査読前の可能性があり、結論が変わることがあります。</p>')
        if s:
            for key, label in LABELS.items():
                body.append(f'<p><strong>{label}：</strong>{escape(s[key])}</p>')
        else:
            body.append('<p>' + escape(a["abstract"][:4500] or "抄録・概要未取得。原文をご確認ください。") + '</p>')
        url = safe_url(a["url"])
        if url:
            body.append(f'<p><a class="source" href="{escape(url)}" target="_blank" rel="noopener noreferrer">原文・論文情報を読む ↗</a></p>')
            body.append(f'<p class="meta">{escape(url)}</p>')
        body.append('</article>')
    ideas = (report.get("summary") or {}).get("research_ideas", [])
    if ideas:
        by_id = {a["id"]: (i, a) for i, a in enumerate(articles, 1)}
        body.append('<section class="ideas"><h2>研究の芽 — AIの仮説</h2><p>新規性や研究費の採択を保証するものではありません。</p>')
        for idea in ideas:
            body.append(f'<h3>{escape(idea["title"])}</h3>')
            for k, label in [("basis", "資料で確認された事実"), ("hypothesis", "仮説"), ("design", "研究デザイン候補"), ("caveat", "追加確認・限界")]:
                body.append(f'<p><strong>{label}：</strong>{escape(idea[k])}</p>')
            links = [f'<a href="#item-{by_id[i][0]}">文献{by_id[i][0]}</a>' for i in idea["source_ids"]]
            body.append('<p>根拠：' + ' / '.join(links) + '</p>')
        body.append('</section>')
    note = (report.get("summary") or {}).get("editor_note", "")
    if note:
        body.append('<p>' + escape(note) + '</p>')
    body.append('<details><summary>収集状況と履歴の読み方</summary><ul>')
    for status in report["sources"]:
        status_text = f'{status["name"]}: {status["status"]} / {status.get("count", 0)}件取得'
        if status.get("capped"):
            status_text += '（検索件数の上限到達・全件未取得）'
        if status.get("error"):
            status_text += ' / ' + status["error"]
        body.append('<li>' + escape(status_text) + '</li>')
    body.append('</ul><p>厚労省新着と指定の審議会一覧・労働基準局の会議一覧、NHK RSSを巡回します。巡回・資料抽出には上限があり、全件を網羅していません。</p>')
    body.append('<p>' + escape(report["observations"]["caution"]) + '</p>')
    body.append('<pre>' + escape(json.dumps(report["observations"]["weekly_topic_counts"], ensure_ascii=False, indent=2)) + '</pre></details>')
    body.extend(domestic_html(report))
    content = "\n".join(body)
    chars = len(re.sub('<[^>]+>', '', content))
    estimate = max(1, math.ceil(chars / 450))
    return f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; base-uri 'none'; form-action 'none'">
<title>産業保健リサーチブリーフ</title><style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Noto Sans JP",sans-serif;color:#20333d;background:#f2f5f4;margin:0;line-height:1.85}}
main{{max-width:850px;margin:auto;padding:36px 22px}}header{{border-top:6px solid #167466;padding:18px 0}}h1{{font-size:29px;line-height:1.5}}h2{{font-size:21px;line-height:1.65}}h3{{font-size:18px}}
article,nav,.ideas{{background:white;padding:25px;margin:22px 0;border-radius:10px;border:1px solid #d9e3df}}a{{color:#076558;overflow-wrap:anywhere}}.meta,.original{{color:#566970;font-size:13px}}.badge{{background:#e4f0ec;padding:4px 9px;border-radius:5px;font-size:12px}}.tags{{font-size:12px}}.notice{{background:#fff3d6;padding:15px;border-left:4px solid #b28018;margin:16px 0}}.source{{font-weight:700}}pre{{white-space:pre-wrap;font-size:12px}}details{{font-size:13px}}@media(max-width:600px){{main{{padding:18px 12px}}article,nav,.ideas{{padding:18px}}}}
</style></head><body><main><header><p>OCCUPATIONAL HEALTH · RESEARCH RADAR</p><h1>産業保健リサーチブリーフ</h1><p>仕事と健康の変化を、次の研究の問いへ。</p><p class="meta">論文{len(articles)}本＋国内動向{len(report.get("policy_updates", []))}件 ／ 本文の読了目安 約{estimate}分（原文を読む時間は含みません）</p></header>{content}</main></body></html>'''


def render_text(report: dict) -> str:
    summaries = {s["id"]: s for s in (report.get("summary") or {}).get("summaries", [])}
    lines = ["産業保健リサーチブリーフ", report["created_at_jst"],
             "抄録・公式フィードの概要に基づくAI要約。全文未確認。", ""]
    for i, a in enumerate(report["articles"], 1):
        s = summaries.get(a["id"])
        lines.extend([f'{i}. {s["title_ja"] if s else a["title"]}',
                      a["title"], f'{a["kind"]} / {a["journal"] or a["source"]} / 公表：{a["published"]}'])
        if s:
            lines.extend(label + "：" + s[k] for k, label in LABELS.items())
        else:
            lines.append(a["abstract"] or "抄録・概要未取得")
        lines.extend([safe_url(a["url"]), ""])
    for idea in (report.get("summary") or {}).get("research_ideas", []):
        lines.extend(["研究の芽（AIの仮説）：" + idea["title"], "根拠：" + idea["basis"],
                      "仮説：" + idea["hypothesis"], "研究デザイン：" + idea["design"], "限界：" + idea["caveat"]])
        lines.extend(safe_url(a["url"]) for a in report["articles"] if a["id"] in idea["source_ids"])
        lines.append("")
    lines.append((report.get("summary") or {}).get("editor_note", ""))
    lines.append(report["observations"]["caution"])
    for s in report["sources"]:
        lines.append(f'収集：{s["name"]} {s["status"]} {s.get("count", 0)}件' + ("（上限到達・全件未取得）" if s.get("capped") else ""))
    lines.extend(domestic_text(report))
    return "\n".join(lines)


def write_report(report: dict, output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    stamp = report["created_at"].replace(":", "").replace("+", "_").replace(".", "")
    base = output / ("brief-" + stamp)
    for path, content in [(base.with_suffix(".json"), json.dumps(report, ensure_ascii=False, indent=2)),
                          (base.with_suffix(".html"), render_html(report)),
                          (output / "latest.json", json.dumps(report, ensure_ascii=False, indent=2)),
                          (output / "latest.html", render_html(report)),
                          (output / "latest.md", render_markdown(report))]:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(content, encoding="utf-8")
        temp.replace(path)
    return base.with_suffix(".html")


def render_markdown(report: dict) -> str:
    def md(text):
        return re.sub(r'([\\`*_\[\]{}])', r'\\\1', escape(text)).replace("\n", " ")
    summaries = {s["id"]: s for s in (report.get("summary") or {}).get("summaries", [])}
    lines = ["# 産業保健リサーチブリーフ", "", report["created_at_jst"], ""]
    if report.get("demo"):
        lines.extend(["**架空データの表示見本です。**", ""])
    if report.get("summary_origin"):
        lines.extend([md(report["summary_origin"]), ""])
    if not report.get("summary") and not report.get("domestic_summary"):
        lines.extend(["**資料一覧：AI要約は未生成です。**", ""])
    lines.extend(["取得抄録・公式フィードの概要に基づく情報です。全文未確認。公表日は取得日と異なります。", ""])
    for i, a in enumerate(report["articles"], 1):
        s = summaries.get(a["id"])
        lines.extend([f'## {i}. {md(s["title_ja"] if s else a["title"])}', "",
                      md(a["title"]), "", md(a["journal"] or a["source"]) + " ／ 公表：" + md(a["published"]), "",
                      "取得元：" + md(a["source"]) + " ／ " + md(a["kind"]) + " ／ 全文未確認・査読状況は個別未確認", ""])
        if s:
            for key, label in LABELS.items():
                lines.extend([f'**{label}：** {md(s[key])}', ""])
        else:
            lines.extend([md(a["abstract"] or "抄録・概要未取得"), ""])
        url = safe_url(a["url"])
        if url:
            lines.extend(["[原文・論文情報を読む](" + url.replace("(", "%28").replace(")", "%29") + ")", ""])
    for idea in (report.get("summary") or {}).get("research_ideas", []):
        lines.extend(["## 研究の芽（AIの仮説）：" + md(idea["title"]), ""])
        for key, label in [("basis", "確認された事実"), ("hypothesis", "仮説"), ("design", "研究デザイン候補"), ("caveat", "限界・追加確認")]:
            lines.extend([f'**{label}：** {md(idea[key])}', ""])
        for i, a in enumerate(report["articles"], 1):
            if a["id"] in idea["source_ids"] and safe_url(a["url"]):
                lines.extend([f'根拠：文献{i} — ' + a["url"], ""])
    lines.extend(["## 収集範囲", "", md((report.get("summary") or {}).get("editor_note", "")), ""])
    if report.get("queue"):
        q = report["queue"]
        lines.extend([f'本日の論文{q["selected"]}本 / 繰越候補{q["deferred"]}本 / 公表から最大{q["max_lag_days"]}日 / 期限超過{q["expired"]}本', ""])
    for s in report["sources"]:
        lines.extend([md(f'{s["name"]}: {s["status"]} / {s.get("count", 0)}件取得') + ("（上限到達・全件未取得）" if s.get("capped") else ""), ""])
    lines.extend([md(report["observations"]["caution"]), ""])
    if report.get("policy_updates") or "domestic_summary" in report:
        lines.extend(["## 国内動向（論文10本とは別枠・最大5件）", ""])
        for line in domestic_text(report)[3:]:
            if safe_url(line):
                lines.extend(["[原文・資料を読む]("+line.replace("(","%28").replace(")","%29")+")", ""])
            else:
                lines.extend([md(line),""])
    return "\n".join(lines)
