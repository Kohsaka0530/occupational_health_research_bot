import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from research_bot.cli import ROOT, due, main
from research_bot.core import Article, deduplicate, plain, rank, select
from research_bot.demo import demo_data
from research_bot.mail import MailError, create_outbox, deliver
from research_bot.net import FetchError
from research_bot.render import render_html
from research_bot.sources import crossref_article, europe_article, europe_pmc, parse_feed
from research_bot.store import Store
from research_bot.summarize import SummaryError, validate


def article(**kwargs):
    defaults = dict(title="Occupational health and worker safety", url="https://example.org/paper",
                    source="test", kind="学術論文", published="2026-10-17", abstract="Workers report health and safety concerns.")
    defaults.update(kwargs)
    return rank(Article.create(**defaults))


def report():
    articles, summary = demo_data()
    return dict(created_at="2026-10-18T00:00:00+00:00", created_at_jst="2026-10-18 09:00 JST",
                since="2026-10-04", until="2026-10-18", demo=False,
                articles=[a.to_dict() for a in articles], summary=summary,
                sources=[dict(name="test", status="ok", count=4)],
                observations=dict(weekly_topic_counts={}, caution="テスト"))


class SourcesTest(unittest.TestCase):
    def test_pubmed_abstract_publication_and_preprint(self):
        a = europe_article(dict(title="Occupational heat exposure", pmid="1234", id="1234", source="MED",
             doi="10.1/HEAT", firstPublicationDate="2021-01-01", abstractText="<h4>Results</h4><p>Workers exposed.</p>",
             pubTypeList={"pubType": ["Cohort Study"]}, journalInfo={"journal": {"title": "Example journal"}}))
        self.assertEqual(a.published, "2021-01-01")
        self.assertEqual(a.url, "https://pubmed.ncbi.nlm.nih.gov/1234/")
        self.assertIn("Workers exposed.", a.abstract)
        self.assertNotIn("<p>", a.abstract)
        preprint = europe_article(dict(title="Worker health", id="PPR123", source="PPR"))
        self.assertEqual(preprint.kind, "プレプリント")

    def test_retracted_publication_is_not_selected(self):
        a = europe_article(dict(title="Occupational health workers", id="1", pubTypeList={"pubType": ["Retracted Publication"]}))
        self.assertEqual(select([rank(a)], 10), [])

    def test_crossref_abstract_is_optional_and_doi_safe(self):
        a = crossref_article({"title": ["Gig worker health"], "DOI": "10.1234/AbC", "published": {"date-parts": [[2025, 3, 1]]}, "type": "journal-article"})
        self.assertEqual(a.doi, "10.1234/abc")
        self.assertEqual(a.abstract, "")
        self.assertEqual(a.published, "2025-03-01")

    def test_rdf_date_window_and_official_domains(self):
        feed = b'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
          <item><title>Worker health safety update</title><link>https://www.mhlw.go.jp/new</link><dc:date>2026-10-17T09:00:00+09:00</dc:date></item>
          <item><title>Old</title><link>https://www.mhlw.go.jp/old</link><dc:date>2020-01-01</dc:date></item>
          <item><title>Spoofed</title><link>https://www.mhlw.go.jp.evil.example/</link><dc:date>2026-10-17</dc:date></item>
          </rdf:RDF>'''
        entries = parse_feed(feed, date(2026, 10, 4), date(2026, 10, 18))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].kind, "政策・公式発表")
        with self.assertRaises(FetchError):
            parse_feed(b"<html>not a feed</html>", date(2026, 1, 1), date(2026, 10, 18))

    def test_page_limit_reported_not_claimed_as_complete(self):
        payload = dict(hitCount=1000, nextCursorMark="second", resultList={"result": [dict(id="1", title="Worker health")]})
        with patch("research_bot.sources.get_json", return_value=payload):
            items, capped = europe_pmc(date(2026, 10, 1), date(2026, 10, 18), {"max_pages": 1, "page_size": 50})
        self.assertEqual(len(items), 1)
        self.assertTrue(capped)


class SelectionTest(unittest.TestCase):
    def test_crossref_registration_alone_does_not_qualify_for_main_brief(self):
        unknown = article(source="Crossref", journal="Unassessed Journal", title="Gig workers mental health")
        known = article(source="Crossref", journal="Work & Stress", title="Gig workers mental health")
        self.assertEqual(unknown.score, 0)
        self.assertGreater(known.score, 0)

    def test_minister_award_and_collaboration_are_not_occupational_health(self):
        award = article(title="全国健康福祉祭の美術展における厚生労働大臣賞について", abstract="")
        collaboration = article(title="Artificial intelligence in global health", abstract="International collaboration and networking improve health.")
        freelancer = article(title="個人事業者等の健康管理に関するガイドライン", abstract="")
        self.assertEqual(award.score, 0)
        self.assertEqual(collaboration.score, 0)
        self.assertGreater(freelancer.score, 0)

    def test_requires_both_work_and_health(self):
        irrelevant = article(title="Machine learning for galaxy classification", abstract="Digital techniques for astronomy")
        unrelated_health = article(title="Mental health in children", abstract="Depression and anxiety in schools")
        gig = article(title="Health and safety among gig workers", abstract="Delivery workers report injury")
        self.assertEqual(irrelevant.score, 0)
        self.assertEqual(unrelated_health.score, 0)
        self.assertIn("新しい働き方", gig.tags)
        self.assertEqual(select([irrelevant, gig, unrelated_health], 3), [gig])

    def test_doi_and_title_dedup_prefers_abstract(self):
        a = article(doi="10.12/ABC", abstract="")
        b = article(doi="https://doi.org/10.12/abc", abstract="Long abstract on worker health")
        c = article(url="https://elsewhere.example/a", abstract="")
        result = deduplicate([a, b, c])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].abstract, b.abstract)

    def test_retraction_signal_survives_deduplication(self):
        rich = article(doi="10.1/withdrawn")
        retraction = article(doi="10.1/withdrawn", abstract="", retracted=True)
        items = deduplicate([rich, retraction])
        self.assertEqual(select(items, 10), [])

    def test_broad_hazard_and_policy_have_room(self):
        items = [article(title=f"Gig workers mental health {i}", doi=f"10.1/{i}") for i in range(10)]
        broad = article(title="Occupational chemical exposure and worker safety", doi="10.2/broad")
        policy = article(title="中小企業の労働安全衛生と健康経営", kind="政策・公式発表", doi="10.2/policy")
        selected = select(items + [broad, policy], 5)
        self.assertIn(broad, selected)
        self.assertIn(policy, selected)
        self.assertEqual(len(selected), 5)


class SummaryAndRenderTest(unittest.TestCase):
    def test_summary_ids_must_match_and_no_abstract_cannot_invent_results(self):
        articles, summary = demo_data()
        summary["summaries"][-1]["findings"] = "Invented effect size 40%"
        validated = validate(summary, articles)
        self.assertNotIn("40%", validated["summaries"][-1]["findings"])
        summary["summaries"][0]["id"] = "made-up-citation"
        with self.assertRaises(SummaryError):
            validate(summary, articles)

    def test_research_idea_cannot_cite_unknown_source(self):
        articles, summary = demo_data()
        summary["research_ideas"] = [dict(title="idea", basis="basis", hypothesis="hypothesis", design="design", caveat="caveat", source_ids=["unknown"])]
        with self.assertRaises(SummaryError):
            validate(summary, articles)

    def test_html_escapes_sources_and_disallows_script_urls(self):
        r = report()
        r["articles"][0]["title"] = '<script>alert("x")</script>'
        r["articles"][0]["url"] = "javascript:alert(1)"
        rendered = render_html(r)
        self.assertNotIn('<script>alert', rendered)
        self.assertNotIn('href="javascript:', rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("全文未確認", rendered)

    def test_source_only_and_partial_results_are_explicit(self):
        r = report()
        r["summary"] = None
        r["sources"].append(dict(name="unavailable", status="error", error="connection failed"))
        rendered = render_html(r)
        self.assertIn("AI要約は未生成", rendered)
        self.assertIn("未取得を「新着なし」とは扱いません", rendered)


class PersistenceAndDeliveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.store = Store(self.path / "state.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_sent_duplicate_new_source_is_not_new_candidate(self):
        a = article(doi="10.1/same")
        self.store.add([a])
        self.store.begin_delivery("digest", datetime.now(timezone.utc))
        self.store.mark_sent("digest", "mail-id", [a.id], datetime.now(timezone.utc))
        b = article(doi="10.1/same", source="another", abstract="Much longer newly indexed abstract with a health finding among workers")
        self.store.add([b])
        self.assertEqual(self.store.candidates(datetime.now(timezone.utc) - timedelta(days=1)), [])

    def test_later_retraction_removes_previous_candidate(self):
        a = article(doi="10.1/retracted")
        self.store.add([a])
        b = article(doi="10.1/retracted", abstract="", retracted=True)
        self.store.add([b])
        self.assertEqual(select(self.store.candidates(datetime.now(timezone.utc) - timedelta(days=1)), 10), [])

    def test_two_day_gate_uses_jst_date_not_scheduler_minutes(self):
        self.assertFalse(due("2026-10-17T22:03:00+00:00", datetime(2026, 10, 18, 22, 0, tzinfo=timezone.utc), 48))
        self.assertTrue(due("2026-10-17T22:03:00+00:00", datetime(2026, 10, 19, 22, 0, tzinfo=timezone.utc), 48))

    @patch.dict(os.environ, {"RESEARCH_MAIL_API_KEY": "test-not-a-real-key", "MAIL_FROM": "sender@example.org", "DIGEST_TO": "recipient@example.org"})
    def test_failed_send_keeps_same_outbox_and_idempotency_key(self):
        r = report()
        outbox = create_outbox(r, self.path)
        self.store.add([Article(**a) for a in r["articles"]])
        calls = []
        def fake_request(url, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise FetchError("simulated timeout after server acceptance")
            return b'{"id":"test-mail-id"}'
        with patch("research_bot.mail.request", side_effect=fake_request):
            with self.assertRaises(MailError):
                deliver(outbox, self.store, self.path)
            self.assertTrue((self.path / "outbox.json").exists())
            deliver(json.loads((self.path / "outbox.json").read_text()), self.store, self.path)
        self.assertEqual(calls[0]["headers"]["Idempotency-Key"], calls[1]["headers"]["Idempotency-Key"])
        self.assertEqual(calls[0]["data"], calls[1]["data"])
        self.assertEqual(self.store.delivery_status(outbox["digest_id"]), "sent")
        self.assertFalse((self.path / "outbox.json").exists())
        self.assertEqual(self.store.candidates(datetime.now(timezone.utc) - timedelta(days=1)), [])

    @patch.dict(os.environ, {"RESEARCH_MAIL_API_KEY": "test-key", "MAIL_FROM": "sender@example.org", "DIGEST_TO": "recipient@example.org"})
    def test_demo_incomplete_or_failed_source_cannot_be_sent(self):
        for change in [{"demo": True}, {"summary": None}, {"sources": [dict(name="broken", status="error")]}]:
            r = report()
            r.update(change)
            with self.assertRaises(MailError):
                create_outbox(r, self.path)

    @patch.dict(os.environ, {"RESEARCH_MAIL_API_KEY": "test-key", "MAIL_FROM": "sender@example.org", "DIGEST_TO": "recipient@example.org"})
    def test_expired_uncertain_send_needs_receipt_check(self):
        outbox = create_outbox(report(), self.path)
        outbox["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        with patch("research_bot.mail.request") as request:
            with self.assertRaises(MailError):
                deliver(outbox, self.store, self.path)
            request.assert_not_called()


class EndToEndTest(unittest.TestCase):
    def test_real_pipeline_with_stubbed_external_sources_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            a = article(journal="Journal of Occupational Health")
            _, sample = demo_data()
            s = sample["summaries"][0].copy()
            s["id"] = a.id
            response = dict(summaries=[s], research_ideas=[], editor_note="テスト")
            config = p / "config.toml"
            config.write_text((ROOT / "config.toml").read_text().replace("crossref = true", "crossref = false").replace("mhlw = true", "mhlw = false").replace("mhlw_meetings = true", "mhlw_meetings = false").replace("domestic_news = true", "domestic_news = false"))
            with patch.dict("research_bot.cli.COLLECTORS", {"europe_pmc": lambda *args: ([a], False)}), \
                 patch("research_bot.cli.summarize", return_value=response), contextlib.redirect_stdout(io.StringIO()):
                code = main(["run", "--config", str(config), "--data-dir", str(p / "data"), "--output", str(p / "out")])
            self.assertEqual(code, 0)
            result = json.loads((p / "out/latest.json").read_text())
            self.assertEqual(result["articles"][0]["url"], a.url)
            self.assertEqual(result["summary"]["summaries"][0]["id"], a.id)
            self.assertTrue((p / "out/latest.html").exists())
            self.assertFalse((p / "data/outbox.json").exists())

    def test_failed_source_has_nonzero_exit_and_does_not_advance_watermark(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            def fail(*args):
                raise FetchError("test unavailable")
            from research_bot.sources import COLLECTORS
            with patch.dict("research_bot.cli.COLLECTORS", {name: fail for name in COLLECTORS}), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = main(["run", "--no-ai", "--data-dir", str(p / "data"), "--output", str(p / "out")])
            self.assertEqual(code, 2)
            result = json.loads((p / "out/latest.json").read_text())
            self.assertTrue(all(s["status"] == "error" for s in result["sources"]))
            store = Store(p / "data/state.sqlite3")
            try:
                self.assertIsNone(store.get("last_fetch_europe_pmc"))
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
