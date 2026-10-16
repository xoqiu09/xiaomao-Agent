import tempfile
import unittest
from pathlib import Path

from tests.daily_scenario import generate


class DailyScenarioTests(unittest.TestCase):
    def test_annotated_multi_repo_daily(self):
        with tempfile.TemporaryDirectory() as root:
            _, _, verdict = generate(Path(root))
        self.assertEqual(verdict["status"], "PASS")
