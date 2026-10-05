import copy
import unittest

from tests.test_inference_protocol import project_with
from xiaomao.feature_evidence import capture
from xiaomao.feature_daily import model_packet, validate_records
from xiaomao.feature_render import render_summary, render_details


def fixture(count=1):
    files = [dict(capture("def report():\n    return 'dirty only'\n",
                          "def report():\n    return 'window commits and changes'\n", f"report{i}.py"),
                  path=f"report{i}.py") for i in range(count)]
    project = project_with(files)
    project['trees'] = []
    project['commits'][0].update(author='private-author', subject='Daily improvement', kind='commit',
                                committed_at='2026-10-05T02:00:00+00:00',
                                first_seen='2026-10-05T02:00:05+00:00', tree_ids=['fixture-main'])
    bundle = {'window': {'date': '2026-10-05', 'timezone': 'Asia/Taipei',
                        'start': '2026-10-04T13:30:00+00:00', 'end': '2026-10-05T13:30:00+00:00'},
              'projects': [project], 'coverage': 'partial', 'preview': True,
              'generated_at': '2026-10-05T04:00:00+00:00', 'candidates': [], 'inventory_errors': []}
    return bundle, project


def interpreted(bundle, project):
    packet = model_packet(bundle, project)
    unit = packet['units'][0]
    row = {'feature_name': '每日工作窗口汇总', 'change_type': 'behavior',
           'before': '原先仅显示最后一次工作区状态。',
           'after': '按截止窗口合并提交与工作区变化，已提交后恢复干净的工作也会进入总结。',
           'impact': '晚间能看见当天已经提交的成果。', 'unit_ids': [unit['unit_id']],
           'support': [{'unit_id': unit['unit_id'], 'side': side, 'quote': unit[side]}
                       for side in ('before', 'after')]}
    return validate_records(project, packet, {'records': [row]})


class DailyReaderExperienceTests(unittest.TestCase):
    def test_author_identity_is_absent_from_body_and_evidence(self):
        bundle, project = fixture()
        notes = {project['repo_id']: interpreted(bundle, project)}
        for text in (render_summary(bundle, notes), render_details(bundle, notes)):
            self.assertNotIn('private-author', text)
            self.assertNotIn('提交作者', text)
            self.assertNotIn('"authors"', text)
            self.assertNotIn('"author"', text)

    def test_body_explains_change_impact_and_delivery(self):
        bundle, project = fixture()
        body = render_summary(bundle, {project['repo_id']: interpreted(bundle, project)})
        self.assertIn('变化：按截止窗口合并提交与工作区变化', body)
        self.assertIn('使用影响：晚间能看见当天已经提交的成果。', body)
        self.assertIn('交付：已提交', body)
        self.assertNotIn('进展：实现有推进', body)

    def test_unassigned_changes_are_one_uncertainty_entry(self):
        bundle, project = fixture(20)
        body = render_summary(bundle, {})
        section = body.split('各项目功能变化\n--------', 1)[1].split('仍在推进的功能', 1)[0]
        self.assertEqual(sum(line.startswith('- ') for line in section.splitlines()), 1)
        self.assertNotIn('相关功能', body)
        self.assertIn('具体行为影响尚未确认', section)
        self.assertIn('交付：已提交', section)
        self.assertEqual(len(model_packet(bundle, project)['units']), 20)

    def test_interpreted_capabilities_precede_unresolved_core_records(self):
        bundle, project = fixture(2)
        note = interpreted(bundle, project)
        note['records'].append(dict(copy.deepcopy(note['records'][0]),
            record_id='core-unresolved', feature_name='未解释的核心模块',
            interpretation='rule', priority=3, before='', after='实现有调整，具体功能影响待确认。'))
        body = render_summary(bundle, {project['repo_id']: note})
        overview = body.split('今日总览\n--------', 1)[1].split('各项目功能变化', 1)[0]
        self.assertLess(overview.index('每日工作窗口汇总'), overview.index('未解释的核心模块'))

    def test_model_does_not_receive_author_metadata(self):
        bundle, project = fixture()
        packet = model_packet(bundle, project)
        self.assertTrue(all('author' not in u for u in packet['units']))

    def test_model_vague_progress_is_not_accepted_as_an_explanation(self):
        bundle, project = fixture()
        packet = model_packet(bundle, project)
        unit = packet['units'][0]
        row = {'feature_name': '相关功能', 'change_type': 'behavior', 'before': '',
               'after': '实现有推进。', 'impact': '', 'unit_ids': [unit['unit_id']],
               'support': [{'unit_id': unit['unit_id'], 'side': 'after', 'quote': unit['after']}]}
        self.assertEqual(validate_records(project, packet, {'records': [row]})['accepted'], 0)

    def test_model_cannot_turn_exclusions_into_wider_observation(self):
        bundle, project = fixture()
        file = dict(capture('_THIRD_PARTY_COMPONENTS = {"stats"}\n',
                            '_THIRD_PARTY_COMPONENTS = {"stats", "token-monitor"}\n', 'scope.py'), path='scope.py')
        project['commits'][0]['files'] = [file]
        packet = model_packet(bundle, project)
        unit = packet['units'][0]
        row = {'feature_name': '个人项目观察范围', 'change_type': 'behavior', 'before': '',
               'after': '新增第三方目录排除项。', 'impact': '扩大可观察项目范围。',
               'unit_ids': [unit['unit_id']], 'support': [
                   {'unit_id': unit['unit_id'], 'side': 'after', 'quote': unit['after']}]}
        self.assertEqual(validate_records(project, packet, {'records': [row]})['accepted'], 0)

    def test_model_cannot_substitute_quality_guarantees_for_usage_effect(self):
        bundle, project = fixture()
        packet = model_packet(bundle, project)
        row = interpreted(bundle, project)['records'][0]
        row['impact'] = '保障观察数据完整性。'
        self.assertEqual(validate_records(project, packet, {'records': [row]})['accepted'], 0)

    def test_scope_interpretation_cannot_call_an_exclusion_a_whitelist(self):
        bundle, project = fixture()
        packet = model_packet(bundle, project)
        row = interpreted(bundle, project)['records'][0]
        packet['units'][0]['matched_features'] = [{'id': 'scope', 'name': '个人项目观察范围保护', 'priority': 3}]
        row['feature_name'] = '个人项目观察范围保护'
        row['after'] = '扩展第三方组件白名单。'
        row['impact'] = '支持更多第三方项目纳入观察范围。'
        self.assertEqual(validate_records(project, packet, {'records': [row]})['accepted'], 0)

    def test_behavior_with_two_sided_evidence_requires_prior_explanation(self):
        bundle, project = fixture()
        packet = model_packet(bundle, project)
        row = interpreted(bundle, project)['records'][0]
        row['before'] = ''
        self.assertEqual(validate_records(project, packet, {'records': [row]})['accepted'], 0)
