import json
import tempfile
import unittest
from pathlib import Path

from xiaomao.feature_evidence import capture
from xiaomao.feature_daily import model_packet, project_units, validate_records
from tests.helpers import website_fixture_config


def project_with(files):
    return {'project_id': 'website', 'display_name': '日报工具', 'repo_id': 'fixture:one',
            'commits': [{'sha': '1' * 40, 'author': 'fixture', 'parents': [],
                         'evidence_id': 'commit:fixture:one:' + '1' * 40,
                         'files': files, 'effective_at': '2026-10-05T02:00:00+00:00'}],
            'activity': [], 'ongoing': [], 'feature_contexts': {}, 'limitations': [],
            'gaps': [], 'changed': True}


def packet_for(project):
    return model_packet({'window': {'date': '2026-10-05'}}, project)


def chosen_reply(request):
    unit = request['units'][0]
    quote = next(q for q in request['quote_catalog']
                 if q['unit_id'] == unit['unit_id'] and q['side'] == 'after')
    return {'records': [{'feature_name': '日报汇总', 'change_type': 'addition',
                         'before': '', 'after': '增加了汇总入口。', 'impact': '',
                         'unit_ids': [unit['unit_id']], 'support': [{'quote_id': quote['quote_id']}]}]}


class InferenceProtocolTests(unittest.TestCase):
    def small_file(self):
        return dict(capture('', "def report():\n    return 'all day'\n", 'daily.py'), path='daily.py')

    def test_one_truncated_function_does_not_taint_complete_delta(self):
        source = "def report():\n    return 'all day'\n\ndef long_task():\n    return '" + 'x' * 1000 + "'\n"
        file = dict(capture('', source, 'daily.py'), path='daily.py')
        file['units_truncated'] = True
        units = project_units(project_with([file]))
        complete = next(u for u in units if u['symbol'] == 'report')
        self.assertFalse(complete['truncated'])
        self.assertTrue(any(u['truncated'] for u in units if u['symbol'] == 'long_task'))

    def test_numbered_quote_is_resolved_from_original_source(self):
        from xiaomao.feature_daily import prepare_inference, inference_schema, expand_inference
        project = project_with([self.small_file()])
        packet = packet_for(project)
        request, bindings = prepare_inference(packet)
        reply = chosen_reply(request)
        schema = inference_schema(request)
        self.assertIn('quote_id', schema['properties']['records']['items']['properties']['support']['items']['properties'])
        expanded = expand_inference(reply, bindings)
        self.assertEqual(expanded['records'][0]['unit_ids'], [packet['units'][0]['unit_id']])
        self.assertEqual(validate_records(project, packet, expanded)['accepted'], 1)
        reply['records'][0]['support'][0]['quote_id'] = 'invented'
        self.assertEqual(validate_records(project, packet, expand_inference(reply, bindings))['accepted'], 0)

    def test_large_project_keeps_bounded_input_and_all_uninterpreted_evidence(self):
        from xiaomao.feature_daily import prepare_inference, expand_inference
        file = self.small_file()
        project = project_with([file])
        packet = packet_for(project)
        original = packet['units'][0]
        packet['units'] = [dict(original, unit_id=f'original-{i}', path=f'feature{i}.py') for i in range(150)]
        packet['feature_contexts'] = {'large': {'purpose': '项目背景', 'sources': [{'excerpt': 'x' * 100000}]}}
        request, bindings = prepare_inference(packet, max_chars=5000)
        self.assertLessEqual(len(json.dumps(request, ensure_ascii=False, sort_keys=True)), 5000)
        self.assertGreater(len(request['units']), 0)
        self.assertLess(len(request['units']), len(packet['units']))
        result = validate_records(project, packet, expand_inference(chosen_reply(request), bindings))
        self.assertEqual(result['accepted'], 1)
        self.assertEqual(result['uninterpreted_units'], 149)

    def test_production_job_uses_numbered_quote_contract(self):
        from xiaomao.daily_jobs import summarize_projects
        project = project_with([self.small_file()])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = website_fixture_config(Path(tmp) / 'home', Path(tmp) / 'repo')
            class Client:
                def generate_json(self, **kwargs):
                    request = json.loads(kwargs['user'])
                    return {'json': chosen_reply(request)}
            notes = summarize_projects(cfg, {'scope': [], 'window': {'date': '2026-10-05'},
                                             'projects': [project]}, Client())
            self.assertEqual(notes[project['repo_id']]['accepted'], 1)
