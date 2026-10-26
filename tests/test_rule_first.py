"""Rule version first, model supplement later, one notification."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests.feature_helpers import response
from tests.plain_scenario import build, make_day
from xiaomao.daily_jobs import run_daily
from xiaomao.store import open_db_readonly

# 21:40 Taipei: the 10-02 window is complete, so a scheduled run publishes it.
NOW = datetime(2026, 10, 2, 13, 40, tzinfo=timezone.utc)


class RuleFirstTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg, repos = build(root)
        make_day(self.cfg, repos)
        self.home = Path(self.cfg.home)
        self.report = self.home / "reports/daily/2026-10-02.txt"
        self.events = []

    def notifier(self, date, path):
        # What the user would see at notification time.
        self.events.append(("notify", Path(path).read_text()))
        return True

    def run_scheduled(self, client_factory):
        with patch("xiaomao.collect.utc_now", return_value=NOW.isoformat()):
            return run_daily(self.cfg, now=NOW, scheduled=True, client_factory=client_factory,
                             notifier=self.notifier)

    def notification_rows(self):
        with open_db_readonly(self.home / "xiaomao.sqlite") as conn:
            return [tuple(r) for r in conn.execute("SELECT window_key, state FROM daily_notifications")]

    def test_model_unavailable_still_publishes_and_notifies(self):
        paths = self.run_scheduled(lambda _cfg: None)
        self.assertEqual(paths, [self.report])
        self.assertEqual(len(self.events), 1)
        body = self.report.read_text()
        self.assertTrue(body.startswith("小猫晚报 · 10月2日（周五）\n"))
        self.assertNotIn("已补充解读", body)
        self.assertEqual([state for _, state in self.notification_rows()], ["sent"])

    def test_model_failure_keeps_rule_version(self):
        class Broken:
            def generate_json(self, **kwargs):
                raise RuntimeError("fixture failure")
        self.run_scheduled(lambda _cfg: Broken())
        self.assertEqual(len(self.events), 1)
        self.assertNotIn("已补充解读", self.report.read_text())

    def test_notification_precedes_model_and_report_is_updated_in_place(self):
        events = self.events

        class Model:
            def generate_json(self, **kwargs):
                events.append(("model", json.loads(kwargs["user"])["project_id"]))
                return response(json.loads(kwargs["user"]), "没有结果时可以一键清除筛选。")
        self.run_scheduled(lambda _cfg: Model())
        kinds = [kind for kind, _ in events]
        self.assertEqual(kinds[0], "notify")
        self.assertEqual(kinds.count("notify"), 1)
        self.assertIn("model", kinds)
        self.assertNotIn("已补充解读", events[0][1])   # the notified version is the rule one
        final = self.report.read_text()
        self.assertIn("已补充解读", final.splitlines()[0])
        self.assertIn("没有结果时可以一键清除筛选", final)
        # Same file updated; the rule version is kept in history, not deleted.
        history = list((self.report.parent / "history" / "2026-10-02").glob("*.txt"))
        self.assertTrue(any("已补充解读" not in h.read_text() for h in history))
        self.assertEqual(len(self.notification_rows()), 1)

    def test_rerun_never_notifies_again(self):
        self.run_scheduled(lambda _cfg: None)
        later = datetime(2026, 10, 2, 13, 55, tzinfo=timezone.utc)
        with patch("xiaomao.collect.utc_now", return_value=later.isoformat()):
            run_daily(self.cfg, date="2026-10-02", now=later, scheduled=True,
                      client_factory=lambda _cfg: None, notifier=self.notifier)
        self.assertEqual(len(self.events), 1)


if __name__ == "__main__":
    unittest.main()
