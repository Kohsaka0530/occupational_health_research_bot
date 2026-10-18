import tempfile
import contextlib
import io
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from research_bot.core import Article, rank
from research_bot.journals import CORE, eligible_journal, journal_group, searches
from research_bot.queue import daily_selection
from research_bot.cli import due,trial_active,main,ROOT
from research_bot.sources import europe_pmc
from research_bot.store import Store,fingerprints


TODAY = date(2026, 10, 18)


def paper(i, age=0, journal="Journal of Occupational Health", title=None):
    return rank(Article.create(title=title or f"Occupational health study {i}",
        url=f"https://example.org/{i}", doi=f"10.1234/{i}", source="Europe PMC",
        kind="学術論文", published=(TODAY-timedelta(days=age)).isoformat(),
        abstract="Study of workers and health.", journal=journal, evidence="Journal Article"))


class JournalPolicyTest(unittest.TestCase):
    def test_core_aliases_and_families_are_exact(self):
        for group, aliases in CORE.items():
            for alias in aliases:
                self.assertEqual(journal_group(alias), group)
        for title in ["The Lancet Psychiatry", "JAMA Network Open", "NEJM Evidence", "Lancet (London, England)"]:
            self.assertTrue(eligible_journal(title), title)
        self.assertFalse(eligible_journal("The Lancet Fake Journal"))

    def test_frontiers_and_bmc_no_longer_enter_daily_brief(self):
        for journal in ["Frontiers in Public Health", "Frontiers in Psychology", "BMC Public Health"]:
            a = paper(journal, journal=journal, title="Gig workers mental health and machine learning")
            self.assertEqual(a.score, 0)
            self.assertEqual(daily_selection([a], TODAY, 10)[0], [])

    def test_occupational_journal_supplies_context_without_keyword(self):
        a = paper("silica", title="Silica exposure and lung cancer", journal="Occup Environ Med")
        self.assertGreater(a.score, 0)
        self.assertEqual(len(daily_selection([a], TODAY, 10)[0]), 1)

    def test_general_journal_still_requires_work_relevance(self):
        a = paper("astronomy", journal="Nature Communications", title="Stellar classification")
        a.abstract = "A model of galaxies."
        self.assertEqual(rank(a).score, 0)

    def test_funding_and_single_adjustment_covariate_do_not_create_work_relevance(self):
        for abstract in [
            "A vaccine cohort study of health. Funding Research Council for Health, Working Life and Welfare.",
            "A brain health study. Models adjusted for age, education and unemployment. Brain markers predicted mortality.",
        ]:
            a = paper("clinical", journal="JAMA")
            a.title = "Clinical health outcomes"
            a.abstract = abstract
            self.assertEqual(rank(a).score, 0)

    def test_core_journals_each_receive_their_own_search(self):
        queries = []
        def search(query, config, max_pages):
            queries.append(query)
            return [], False
        with patch("research_bot.sources.europe_search", side_effect=search), patch("research_bot.sources.time.sleep"):
            europe_pmc(TODAY, TODAY, {"curated_journals": True, "max_pages": 2, "page_size": 50})
        self.assertEqual(len(queries), len(searches()))
        self.assertTrue(all("FIRST_PDATE:[2026-09-18 TO 2026-10-18]" in q for q in queries))
        self.assertIn("ISSN:1076-2752", queries[0])
        self.assertNotIn('"gig workers"', queries[0])
        self.assertTrue(all('"gig workers"' in q for q in queries[4:]))


class DailyQueueTest(unittest.TestCase):
    def test_fixed_end_stops_even_when_runner_history_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            config=path/'config.toml'
            config.write_text((ROOT/'config.toml').read_text())
            with patch('research_bot.cli.utcnow',return_value=datetime(2026,10,27,15,tzinfo=timezone.utc)), \
                 patch('research_bot.cli.mail_config') as mail_config, contextlib.redirect_stdout(io.StringIO()):
                result=main(['run','--send','--scheduled','--config',str(config),'--data-dir',str(path/'data'),'--output',str(path/'output')])
            self.assertEqual(result,0)
            mail_config.assert_not_called()

    def test_seeded_history_excludes_first_email_after_moving_to_new_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'state.sqlite3')
            try:
                a=paper('already-sent')
                store.seed({'reported_keys':list(fingerprints(a)), 'trial_started_on':'2026-10-18','last_sent':'2026-10-18T13:00:00+00:00'})
                store.add([a,paper('new-paper')])
                pending=store.candidates(datetime.now(timezone.utc)-timedelta(days=1))
                self.assertEqual([x.title for x in pending],[paper('new-paper').title])
                self.assertEqual(store.get('trial_started_on'),'2026-10-18')
            finally:store.close()

    def test_trial_runs_ten_calendar_days_and_stops_before_day_eleven(self):
        self.assertTrue(trial_active(None,datetime(2026,10,18,tzinfo=timezone.utc),10))
        self.assertTrue(trial_active('2026-10-19',datetime(2026,10,27,22,tzinfo=timezone.utc),10))
        self.assertFalse(trial_active('2026-10-19',datetime(2026,10,28,22,tzinfo=timezone.utc),10))

    def test_core_journals_are_represented_when_candidates_exist(self):
        crowded = [paper(i, title=f"Gig workers mental health and digital health {i}") for i in range(20)]
        core = [paper(group, journal=aliases[0]) for group, aliases in CORE.items()]
        selected, _ = daily_selection(crowded + core, TODAY, 10)
        self.assertEqual({journal_group(a.journal) for a in selected} & set(CORE), set(CORE))

    def test_overflow_is_retained_and_next_send_uses_remaining_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/"state.sqlite3")
            try:
                all_papers = [paper(i, age=2) for i in range(15)]
                store.add(all_papers)
                cutoff = datetime.now(timezone.utc)-timedelta(days=1)
                selected, q = daily_selection(store.candidates(cutoff), TODAY, 10)
                self.assertEqual(len(selected), 10)
                self.assertEqual(q["deferred"], 5)
                store.begin_delivery("day1", datetime.now(timezone.utc))
                store.mark_sent("day1", "provider", [a.id for a in selected], datetime.now(timezone.utc))
                remaining, q2 = daily_selection(store.candidates(cutoff), TODAY+timedelta(days=1), 10)
                self.assertEqual(len(remaining), 5)
                self.assertFalse({a.id for a in selected} & {a.id for a in remaining})
                self.assertEqual(q2["deferred"], 0)
            finally:
                store.close()

    def test_publication_age_not_index_or_first_seen_controls_deadline(self):
        old = paper("old", age=31)
        boundary = paper("boundary", age=30)
        future = paper("future", age=-1)
        undated = paper("undated")
        undated.published = "2026"
        result, q = daily_selection([old, boundary, future, undated], TODAY, 10)
        self.assertEqual(result, [boundary])
        self.assertEqual(q["expired"], 1)
        self.assertEqual(q["undated"], 1)

    def test_waiting_research_is_not_starved_by_new_high_topic_scores(self):
        waiting = paper("waiting", age=29)
        fresh = [paper(i, title=f"Gig workers mental health digital health {i}") for i in range(10)]
        selected, q = daily_selection([waiting]+fresh, TODAY, 10)
        self.assertIn(waiting, selected)
        self.assertEqual(q["deferred"], 1)

    def test_more_due_papers_than_capacity_are_explicit(self):
        selected, q = daily_selection([paper(i, age=30) for i in range(15)], TODAY, 10)
        self.assertEqual(len(selected), 10)
        self.assertEqual(q["near_deadline_deferred"], 5)

    def test_publication_recency_does_not_override_relevance(self):
        important = paper("important", age=7, title="Gig workers mental health digital health")
        fresh = paper("fresh", age=0)
        self.assertEqual(daily_selection([fresh, important], TODAY, 1)[0], [important])

    def test_daily_gate_is_jst_calendar_day(self):
        self.assertFalse(due("2026-10-17T22:03:00+00:00", datetime(2026,10,18,1,tzinfo=timezone.utc),24))
        self.assertTrue(due("2026-10-17T22:03:00+00:00", datetime(2026,10,18,22,tzinfo=timezone.utc),24))


if __name__ == "__main__":
    unittest.main()
