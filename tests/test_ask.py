"""xiaomao ask: answers only from stored daily records, with cited days."""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from tests.plain_scenario import NOW, build, make_day
from xiaomao.ask import ask_command, build_packet, recent_bundles, validated
from xiaomao.daily_jobs import run_daily
from xiaomao.store import open_db, open_db_readonly, set_meta


class FakeClient:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        return {"json": self.reply}


class AskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.cfg, cls.repos = build(Path(cls._tmp.name))
        make_day(cls.cfg, cls.repos)
        with patch("xiaomao.collect.utc_now", return_value=NOW.isoformat()):
            run_daily(cls.cfg, date="2026-10-02", now=NOW)
        cls.home = Path(cls.cfg.home)
        cls.db = cls.home / "xiaomao.sqlite"

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def ask(self, question="xiuqiu-site 做了什么", reply=None, **opts):
        ns = Namespace(question=question, days=opts.pop("days", 7), project=opts.pop("project", None),
                       model=opts.pop("model", None))
        client = FakeClient(reply if reply is not None else
                            {"answer": "xiuqiu-site 加了空结果提示，还在本机。", "sources": ["2026-10-02"],
                             "not_in_records": False})
        out, err = io.StringIO(), io.StringIO()
        code = ask_command(ns, self.home, client_factory=lambda cfg: client, today="2026-10-03",
                           out=out, err=err, **opts)
        return code, out.getvalue(), err.getvalue(), client

    def packet(self, project=None):
        with open_db_readonly(self.db) as conn:
            return build_packet(recent_bundles(conn, days=7, until="2026-10-03"), project)

    def test_packet_holds_only_stored_report_facts(self):
        days = self.packet()
        self.assertEqual([d["date"] for d in days], ["2026-10-02"])
        text = json.dumps(days, ensure_ascii=False)
        self.assertIn("空结果提示", text)
        self.assertIn("🟠 仅本机", text)
        self.assertIn("旷野之心", days[0]["no_change"])
        # No paths, SHAs or file names reach the model.
        for repo in self.repos.values():
            self.assertNotIn(str(repo), text)
        self.assertNotIn(str(self.home), text)
        self.assertNotRegex(text, r"[0-9a-f]{40}")

    def test_project_filter(self):
        days = self.packet("笔记工具")
        self.assertEqual([p["project"] for p in days[0]["projects"]], ["笔记工具"])
        self.assertEqual([s["project"] for s in days[0]["status"]], ["笔记工具"])
        self.assertTrue(days[0]["status"][0]["no_remote"])
        self.assertTrue(all(s.startswith("笔记工具") for s in days[0]["next_steps"]))

    def test_status_covers_projects_without_changes_that_day(self):
        """10/31: mini-exchange did not change but still held 5 unpushed commits."""
        def project(name, changed, unpushed=(), commits=None):
            return {"project_id": name, "display_name": name, "repo_id": name, "changed": changed,
                    "commits": [], "ongoing": [], "activity": [], "trees": [],
                    "delivery_state": {"has_remote": True, "default_ref": "refs/remotes/origin/main",
                                       "unpushed": list(unpushed), "dirty_files": 0, "commits": commits or {}}}
        bundle = {"window": {"date": "2026-10-31"}, "projects": [
            project("mini-exchange", False, unpushed=["a" * 40] * 5),
            project("Session Steward", True, commits={"b" * 40: "pushed"}),
            project("QAI", False)]}
        status = {s["project"]: s for s in build_packet([bundle])[0]["status"]}
        self.assertEqual(status["mini-exchange"]["unpushed_commits"], 5)
        self.assertEqual((status["Session Steward"]["unpushed_commits"],
                          status["Session Steward"]["pushed_not_merged"]), (0, 1))
        self.assertNotIn("QAI", status)                                   # nothing pending
        self.assertEqual(self.packet("不存在的项目"), [])

    def test_answer_names_its_days(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        code, out, _err, client = self.ask()
        self.assertEqual(code, 0)
        self.assertIn("xiuqiu-site 加了空结果提示", out)
        self.assertIn("依据：10月2日的晚报", out)
        self.assertIn("xiuqiu-site 做了什么", client.calls[0]["user"])
        self.assertEqual(client.calls[0]["model"], self.cfg.ask_model)                  # general model by default
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)   # nothing written

    def test_answer_without_valid_days_is_not_shown(self):
        code, out, err, _ = self.ask(reply={"answer": "做了很多。", "sources": ["2026-09-01"],
                                            "not_in_records": False})
        self.assertEqual((code, out), (3, ""))
        self.assertIn("没有注明依据", err)
        self.assertIsNone(validated({"json": {"answer": "", "sources": [], "not_in_records": True}}, {"2026-10-02"}))

    def test_not_in_records_is_said_plainly(self):
        code, out, _err, _ = self.ask("上个月部署了吗", reply={"answer": "这几天的记录里没有部署信息。",
                                                          "sources": [], "not_in_records": True})
        self.assertEqual(code, 0)
        self.assertEqual(out, "这几天的记录里没有部署信息。\n")

    def test_model_failure_and_paused_inference(self):
        class Broken:
            def generate_json(self, **kwargs):
                raise TimeoutError("slow")
        ns = Namespace(question="q", days=7, project=None, model=None)
        err = io.StringIO()
        code = ask_command(ns, self.home, client_factory=lambda cfg: Broken(), today="2026-10-03",
                           out=io.StringIO(), err=err)
        self.assertEqual(code, 3)
        self.assertIn("本地模型调用失败", err.getvalue())
        with open_db(self.db) as conn:
            set_meta(conn, "infer_paused", "1")
        try:
            code, _out, err_text, client = self.ask()
        finally:
            with open_db(self.db) as conn:
                set_meta(conn, "infer_paused", "0")
        self.assertEqual(code, 3)
        self.assertIn("推理已暂停", err_text)
        self.assertEqual(client.calls, [])

    def test_only_registered_models_and_no_records(self):
        code, _out, err, client = self.ask(model="some-cloud-model")
        self.assertEqual(code, 2)
        self.assertIn("只能使用配置里登记的本地模型", err)
        self.assertEqual(client.calls, [])
        ns = Namespace(question="q", days=3, project=None, model=None)
        err = io.StringIO()
        self.assertEqual(ask_command(ns, self.home, client_factory=lambda cfg: None, today="2026-11-01",
                                     out=io.StringIO(), err=err), 2)
        self.assertIn("最近 3 天还没有晚报记录", err.getvalue())


if __name__ == "__main__":
    unittest.main()
