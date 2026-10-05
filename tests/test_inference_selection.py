import copy
import unittest

from tests.test_daily_reader_experience import fixture
from xiaomao.feature_daily import model_packet,prepare_inference


class InferenceSelectionTests(unittest.TestCase):
    def test_capability_input_is_small_balanced_and_prefers_newer_evidence(self):
        bundle, project = fixture()
        packet = model_packet(bundle, project)
        base = packet['units'][0]
        units = []
        for feature in ('日报阅读', '文档交接'):
            for i in range(12):
                unit = dict(copy.deepcopy(base), unit_id=f'{feature}-{i}', at=f'2026-10-05T{i:02d}:00:00+00:00',
                            matched_features=[{'id':feature,'name':feature,'priority':3}])
                units.append(unit)
        packet['units'] = units
        request, bindings = prepare_inference(packet)
        originals = list(bindings['units'].values())
        self.assertLessEqual(len(request['units']), 6)
        for feature in ('日报阅读', '文档交接'):
            self.assertIn(f'{feature}-11', originals)
            self.assertNotIn(f'{feature}-0', originals)
        self.assertEqual(len(packet['units']), 24)

    def test_core_capability_count_and_total_input_are_bounded(self):
        bundle, project = fixture()
        packet = model_packet(bundle, project)
        base = packet['units'][0]
        packet['units'] = [dict(copy.deepcopy(base), unit_id=f'unit-{i}',
            matched_features=[{'id':str(i),'name':f'功能{i}','priority':3}]) for i in range(20)]
        request, _ = prepare_inference(packet)
        self.assertLessEqual(len({u['matched_features'][0]['id'] for u in request['units']}), 5)
        self.assertEqual(len(packet['units']), 20)
