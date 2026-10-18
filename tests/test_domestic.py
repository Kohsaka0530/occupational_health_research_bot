import json
import os
import tempfile
import unittest
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch

from research_bot.core import Article,deduplicate
from research_bot.store import Store
from research_bot.domestic import Page, classify, enrich, japanese_date, meeting_rows, rank_domestic, select_domestic
from research_bot.mail import MailError, create_outbox
from research_bot.render import render_html, render_markdown, render_text
from research_bot.summarize import SummaryError, validate_domestic
from test_agent import report


def item(i, category="専門家会議", title=None):
    return rank_domestic(Article.create(title=title or f"第{i}回 職場の安全衛生に関する検討会（資料）",
        url=f"https://www.mhlw.go.jp/stf/test_{i}.html", source="厚生労働省",
        kind="政策・公式発表", category=category, published="2026-10-14",
        abstract="労働安全衛生に関する議題の資料です。案を検討するもので、決定ではありません。",
        content_basis="公式会議資料の抜粋", published_basis="掲載日"))


def summaries(items):
    return {"items":[dict(id=a.id,title_ja=a.title,overview="取得資料では指針案が議題となっています。",
                        implications="対象と適用条件を原文で確認する必要があります。",
                        status_note="検討段階。施行日は確認できません。") for a in items]}


class DomesticSourcesTest(unittest.TestCase):
    def test_page_excludes_navigation_and_keeps_material_links(self):
        page=Page('<header>職場の健康という共通メニュー</header><main><h1>会議資料</h1><p>労働安全の議題</p><a href="/x.pdf">資料1</a></main><footer>職員採用</footer>')
        self.assertIn("労働安全",page.body)
        self.assertNotIn("共通メニュー",page.body)
        self.assertNotIn("職員採用",page.body)
        self.assertEqual(page.links,[("/x.pdf","資料1")])

    def test_date_handles_era_and_fullwidth(self):
        self.assertEqual(japanese_date("２０２６年９月２８日"),date(2026,9,28))
        self.assertEqual(japanese_date("令和８年10月13日"),date(2026,10,13))
        self.assertEqual(japanese_date("令和元年5月1日"),date(2019,5,1))
        self.assertIsNone(japanese_date("公開日不明"))

    def test_meeting_materials_not_announcement_or_future_meeting(self):
        markup='''<main><table>
        <tr><td>第1回</td><td>2026年10月14日</td><td>労働安全衛生の論点</td><td><a href="/minutes.html">議事録</a><a href="/materials.html">資料</a></td></tr>
        <tr><td>第2回</td><td>2026年10月15日</td><td><a href="/notice.html">開催案内</a></td></tr>
        <tr><td>第3回</td><td>2026年10月30日</td><td><a href="/future.html">資料</a></td></tr>
        </table></main>'''
        records=meeting_rows(Page(markup),"労働安全衛生検討会","https://www.mhlw.go.jp/stf/list.html",date(2026,9,18),date(2026,10,18))
        self.assertEqual(len(records),1)
        self.assertTrue(records[0].url.endswith("/minutes.html"))
        self.assertEqual(records[0].event_date,"2026-10-14")
        self.assertIn("公開日とは異な",records[0].published_basis)

    def test_expert_committee_is_not_mislabelled_as_social_news(self):
        self.assertEqual(classify("石綿認定審査会専門委員会 開催案内"),"専門家会議")
        self.assertEqual(classify("労働安全衛生法の一部を改正する法律"),"法令改正")
        self.assertEqual(classify("ストレスチェック実施マニュアル"),"指針・ガイドライン")

    def test_enrichment_reads_different_agenda_material_groups_and_records_failed_pdf(self):
        page=Page('''<main><p>労働安全衛生に関する諮問と報告</p>
        <a href="/1-1.pdf">資料１－１ 改正条文</a><a href="/1-2.pdf">資料１－２ 改正概要</a>
        <a href="/2.pdf">資料２ 指針改正</a></main>''')
        calls=[]
        def extract(payload):
            calls.append(payload)
            return "取得した会議資料の文字列。検討段階であり施行の決定ではありません。"*3
        with patch("research_bot.domestic.load_page",return_value=page), \
             patch("research_bot.domestic.request",side_effect=lambda url,**kw:url.encode()), \
             patch("research_bot.domestic.extract_pdf",side_effect=extract):
            result=enrich(item(1))
        self.assertEqual(len(result.documents),2)
        self.assertTrue(any(d["url"].endswith("/2.pdf") for d in result.documents))
        self.assertIn("会議資料PDF",result.content_basis)
        self.assertIn("取得した会議資料",result.abstract)


class DomesticBriefTest(unittest.TestCase):
    def test_same_official_page_from_feed_and_archive_is_one_item(self):
        first=item(1)
        second=Article.create(title="別表記の安全衛生会議",url=first.url,source="厚生労働省・審議会",
             kind="政策・公式発表",category="専門家会議",published="2026-10-14",abstract=first.abstract)
        self.assertEqual(len(deduplicate([first,second])),1)
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'state.sqlite3')
            try:
                store.add([first,second])
                cutoff=datetime.now(timezone.utc)-timedelta(days=1)
                self.assertEqual(len(store.candidates(cutoff)),1)
                store.begin_delivery('delivery',datetime.now(timezone.utc))
                store.mark_sent('delivery','provider',[first.id],datetime.now(timezone.utc))
                store.add([second])
                self.assertEqual(store.candidates(cutoff),[])
            finally:store.close()

    def test_five_items_diverse_categories_without_reusing_announcements(self):
        candidates=[item(i) for i in range(8)]
        candidates.extend([item(20,"法令改正"),item(21,"指針・ガイドライン"),item(22,"社会ニュース"),item(23,title="職場安全検討会 開催案内")])
        picked=select_domestic(candidates,date(2026,10,18),5)
        self.assertEqual(len(picked),5)
        self.assertEqual({a.category for a in picked},{"法令改正","指針・ガイドライン","社会ニュース","専門家会議"})
        self.assertFalse(any("開催案内" in a.title for a in picked))
        self.assertEqual(len(select_domestic(candidates[:2],date(2026,10,18),5)),2)

    def test_summary_ids_and_missing_body_are_checked(self):
        a=item(1)
        payload=summaries([a])
        a.abstract=""
        validated=validate_domestic(payload,[a])
        self.assertIn("本文・会議資料未取得",validated["items"][0]["overview"])
        payload["items"][0]["id"]="wrong-id"
        with self.assertRaises(SummaryError):validate_domestic(payload,[a])
        with self.assertRaises(SummaryError):validate_domestic(None,[a])

    def test_all_renderers_append_five_overviews_and_source_links(self):
        r=report()
        items=[item(i) for i in range(5)]
        r['policy_updates']=[a.to_dict() for a in items]
        r['domestic_summary']=summaries(items)
        for render in [render_html,render_markdown,render_text]:
            content=render(r)
            self.assertIn("国内動向",content)
            self.assertEqual(content.count("取得資料では指針案が議題"),5)
            self.assertLess(content.index(r['articles'][-1]['url']),content.rindex("国内動向"))
            for a in items:self.assertIn(a.url,content)

    @patch.dict(os.environ,{'RESEARCH_MAIL_API_KEY':'test','MAIL_FROM':'sender@example.org','DIGEST_TO':'recipient@example.org'})
    def test_sending_requires_domestic_summaries_and_records_both_sections(self):
        r=report()
        items=[item(i) for i in range(5)]
        r['policy_updates']=[a.to_dict() for a in items]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(MailError):create_outbox(r,Path(tmp))
            r['domestic_summary']=summaries(items)
            box=create_outbox(r,Path(tmp))
        self.assertEqual(len(box['article_ids']),len(r['articles'])+5)
        self.assertIn("国内動向",box['message']['html'])

    @patch.dict(os.environ,{'RESEARCH_MAIL_API_KEY':'test','MAIL_FROM':'sender@example.org','DIGEST_TO':'recipient@example.org'})
    def test_domestic_only_brief_is_sendable_when_there_are_no_papers(self):
        r=report()
        items=[item(1)]
        r.update(articles=[],summary=None,policy_updates=[a.to_dict() for a in items],domestic_summary=summaries(items))
        with tempfile.TemporaryDirectory() as tmp:
            box=create_outbox(r,Path(tmp))
        self.assertEqual(box['article_ids'],[items[0].id])
        self.assertNotIn('AI要約は未生成',box['message']['html'])


if __name__=='__main__':unittest.main()
