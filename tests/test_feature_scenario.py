import tempfile
import unittest
from pathlib import Path

from tests.feature_scenario import generate


class FunctionalScenarioTests(unittest.TestCase):
    def test_annotated_functional_daily(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = generate(Path(tmp))
        self.assertEqual(result["verdict"]["status"], "PASS")
        self.assertEqual(result["verdict"]["real_model_quality"], "NOT_RUN")
