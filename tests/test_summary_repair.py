import json
import os
import unittest
from unittest.mock import patch

from research_bot.demo import demo_data
from research_bot.summarize import summarize,SummaryError


def response(payload):
    return json.dumps({"choices":[{"finish_reason":"stop","message":{"content":json.dumps(payload)}}]}).encode()


class SummaryRepairTest(unittest.TestCase):
    @patch.dict(os.environ,{"RESEARCH_LLM_API_KEY":"test-only"})
    def test_only_missing_source_is_requested_and_all_ids_are_validated(self):
        articles,payload=demo_data()
        missing=payload['summaries'].pop()
        repaired={'summaries':[missing],'research_ideas':[],'editor_note':'補完'}
        with patch('research_bot.summarize.request',side_effect=[response(payload),response(repaired)]) as request:
            result=summarize(articles,{},dict(model='test',max_output_tokens=1000))
        self.assertEqual({x['id'] for x in result['summaries']},{a.id for a in articles})
        repair_input=json.loads(request.call_args.kwargs['data']['messages'][1]['content'])
        self.assertEqual([a['id'] for a in repair_input['supplied_sources']],[missing['id']])

    @patch.dict(os.environ,{"RESEARCH_LLM_API_KEY":"test-only"})
    def test_failed_repair_does_not_weaken_required_source_checks(self):
        articles,payload=demo_data()
        payload['summaries'].pop()
        repair={'summaries':[],'research_ideas':[],'editor_note':'欠落'}
        with patch('research_bot.summarize.request',side_effect=[response(payload),response(repair)]):
            with self.assertRaises(SummaryError):summarize(articles,{},dict(model='test',max_output_tokens=1000))

    @patch.dict(os.environ, {"RESEARCH_LLM_API_KEY": "test-only"})
    def test_ambiguous_ids_regenerate_all_sources_without_relabelling(self):
        import copy
        for fault in ['unknown', 'duplicate', 'non_object', 'list_id', 'missing_id']:
            with self.subTest(fault=fault):
                articles, good = demo_data()
                bad = copy.deepcopy(good)
                if fault == 'unknown': bad['summaries'][0]['id'] = 'invented-id'
                if fault == 'duplicate': bad['summaries'][0]['id'] = articles[1].id
                if fault == 'non_object': bad['summaries'][0] = 'invalid'
                if fault == 'list_id': bad['summaries'][0]['id'] = []
                if fault == 'missing_id': del bad['summaries'][0]['id']
                bad['research_ideas'] = [{'source_ids': ['invented-id']}]
                with patch('research_bot.summarize.request', side_effect=[response(bad), response(good)]) as request:
                    result = summarize(articles, {}, dict(model='test', max_output_tokens=1000))
                self.assertEqual(result['summaries'][0]['id'], articles[0].id)
                self.assertEqual(result['research_ideas'], [])
                self.assertEqual(request.call_count, 2)
                repair = json.loads(request.call_args.kwargs['data']['messages'][1]['content'])
                self.assertEqual(repair['required_ids'], [a.id for a in articles])
                self.assertEqual([s['id'] for s in repair['supplied_sources']], [a.id for a in articles])

    @patch.dict(os.environ, {"RESEARCH_LLM_API_KEY": "test-only"})
    def test_regeneration_is_bounded_and_still_validates_every_field(self):
        import copy
        for fault in ['unknown', 'duplicate', 'missing', 'field', 'idea', 'truncated']:
            with self.subTest(fault=fault):
                articles, good = demo_data()
                bad = copy.deepcopy(good)
                bad['summaries'][0]['id'] = 'unknown'
                retry = copy.deepcopy(good)
                if fault == 'unknown': retry['summaries'][0]['id'] = 'unknown'
                if fault == 'duplicate': retry['summaries'].append(copy.deepcopy(retry['summaries'][0]))
                if fault == 'missing': retry['summaries'].pop()
                if fault == 'field': retry['summaries'][0]['methods'] = ''
                if fault == 'idea': retry['research_ideas'] = [{'source_ids': ['unknown']}]
                raw = response(retry)
                if fault == 'truncated':
                    raw = json.dumps({'choices': [{'finish_reason': 'length', 'message': {'content': json.dumps(retry)}}]}).encode()
                with patch('research_bot.summarize.request', side_effect=[response(bad), raw]) as request:
                    with self.assertRaises(SummaryError):
                        summarize(articles, {}, dict(model='test', max_output_tokens=1000))
                self.assertEqual(request.call_count, 2)

    def test_id_diagnostics_handle_unhashable_ids_and_distinguish_causes(self):
        from research_bot.summarize import validate
        articles, payload = demo_data()
        payload['summaries'][0]['id'] = []
        payload['summaries'].append(dict(payload['summaries'][1]))
        with self.assertRaisesRegex(SummaryError, '不正ID・形式1件、重複ID1件、欠落1件'):
            validate(payload, articles)

    @patch.dict(os.environ, {"RESEARCH_LLM_API_KEY": "test-only"})
    def test_ten_paper_response_is_validated_without_retry(self):
        import copy
        articles, payload = demo_data()
        while len(articles) < 10:
            article = copy.deepcopy(articles[0])
            article.id = f'test-paper-{len(articles)}'
            item = dict(payload['summaries'][0], id=article.id)
            articles.append(article)
            payload['summaries'].append(item)
        with patch('research_bot.summarize.request', return_value=response(payload)) as request:
            result = summarize(articles, {}, dict(model='test', max_output_tokens=1000))
        self.assertEqual(len(result['summaries']), 10)
        self.assertEqual(request.call_count, 1)
