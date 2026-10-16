import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.test_daily_reader_experience import fixture
from xiaomao.config import default_config
from xiaomao.daily_jobs import summarize_projects
from xiaomao.store import open_db


def item(request, unit, *, vague=False):
    quotes = [q for q in request['quote_catalog'] if q['unit_id'] == unit['unit_id']]
    return {'feature_name': '相关功能' if vague else '每日工作窗口汇总',
            'change_type': 'behavior', 'before_explanation': '原先仅显示最后工作状态。',
            'after_explanation': '实现有推进。' if vague else '窗口内已提交的修改也进入日报。',
            'impact_explanation': '阅读晚间日报时可查看已提交的成果。',
            'unit_ids': [unit['unit_id']], 'support': [{'quote_id': q['quote_id']} for q in quotes]}


class DailyModelRepairTests(unittest.TestCase):
    def setup_fixture(self):
        home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        cfg = default_config(home)
        with open_db(home / 'xiaomao.sqlite'):
            pass
        bundle, project = fixture(2)
        bundle['scope'] = []
        return cfg, bundle, project

    def test_one_bounded_repair_preserves_accepted_items_and_uses_same_sources(self):
        cfg, bundle, project = self.setup_fixture()
        calls = []
        class Client:
            def generate_json(self, **kwargs):
                with sqlite3.connect(Path(cfg.home) / 'xiaomao.sqlite', timeout=0) as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    conn.rollback()
                request = json.loads(kwargs['user'])
                calls.append(request)
                if len(calls) == 1:
                    return {'json': {'records': [item(request, request['units'][0]),
                                                 item(request, request['units'][1], vague=True)]}}
                return {'json': {'records': [item(request, request['units'][0])]}}
        note = summarize_projects(cfg, bundle, Client())[project['repo_id']]
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(calls[1]['units']), 1)
        self.assertEqual(calls[1]['units'][0], calls[0]['units'][1])
        self.assertEqual(note['accepted'], 2)
        self.assertTrue(note['repair_attempted'])

    def test_failed_repair_retains_first_valid_explanation_and_does_not_loop(self):
        cfg, bundle, project = self.setup_fixture()
        calls = []
        class Client:
            def generate_json(self, **kwargs):
                request = json.loads(kwargs['user'])
                calls.append(request)
                if len(calls) == 1:
                    return {'json': {'records': [item(request, request['units'][0]),
                                                 item(request, request['units'][1], vague=True)]}}
                raise RuntimeError('synthetic model failure')
        note = summarize_projects(cfg, bundle, Client())[project['repo_id']]
        self.assertEqual(len(calls), 2)
        self.assertEqual(note['accepted'], 1)
        self.assertTrue(note['error'])
