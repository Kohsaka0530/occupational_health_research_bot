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
