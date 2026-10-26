"""Plain evening report on the synthetic four-state scenario."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.plain_scenario import NOW, build, make_day
from xiaomao.daily_jobs import run_daily
from xiaomao.feature_render import BANNED_TERMS, PLAIN_MAX_LINES, render_plain_summary


class PlainReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.cfg, cls.repos = build(Path(cls._tmp.name))
        make_day(cls.cfg, cls.repos)
        with patch("xiaomao.collect.utc_now", return_value=NOW.isoformat()):
            cls.dest = run_daily(cls.cfg, date="2026-10-02", now=NOW)[0]
        cls.body = cls.dest.read_text()
        cls.evidence = (Path(cls.cfg.home) / "reports/daily-evidence/2026-10-02.txt").read_text()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def line(self, text):
        return next(l for l in self.body.splitlines() if text in l)

    def test_first_line_and_shape(self):
        lines = self.body.splitlines()
        self.assertEqual(lines[0], "小猫晚报 · 10月2日（周五）")
        self.assertEqual(lines[1], "今天 3 个项目有进展，xiuqiu-site 有 1 个提交还没推送。")
        self.assertLessEqual(len(lines), PLAIN_MAX_LINES)
        self.assertLessEqual(sum(l.startswith("★ ") for l in lines), 3)
        for term in BANNED_TERMS:
            self.assertNotIn(term, self.body)

    def test_each_of_four_states(self):
        self.assertIn("🟢 已合并", self.line("筛选可一键清除"))
        self.assertIn("🔵 已推送", self.line("数据来源可配置"))
        self.assertIn("🟠 仅本机", self.line("空结果提示"))
        self.assertIn("另有 1 处改动还没提交", self.body)

    def test_default_branch_not_main(self):
        self.assertIn("🟢 已合并", self.line("对账异常时暂停签名"))   # on trunk
        self.assertIn("🔵 已推送", self.line("对账恢复入口"))         # only on a branch named main

    def test_project_without_remote_shows_first_two_states_only(self):
        self.assertIn("★ 笔记工具（无远端）", self.body)
        self.assertIn("🟠 仅本机", self.line("待办列表"))
        section = self.body.split("★ 笔记工具", 1)[1].split("\n\n", 1)[0]
        self.assertNotIn("已推送", section)
        self.assertNotIn("已合并", section)

    def test_quiet_projects_and_next_steps(self):
        self.assertIn("今天没动：旷野之心", self.body)
        steps = self.body.split("明天从这里继续\n", 1)[1].split("\n\n", 1)[0].splitlines()
        self.assertTrue(2 <= len(steps) <= 3)
        self.assertIn("推送本地的 1 个提交", steps[0])

    def test_evidence_keeps_full_detail(self):
        self.assertIn("功能日报（完整版）", self.evidence)
        self.assertIn("代码位置（生成时）", self.evidence)
        self.assertIn("依据：origin/HEAD", self.evidence)
        self.assertIn("refs/remotes/origin/trunk", self.evidence)

    def test_validated_model_record_replaces_rule_line(self):
        from xiaomao.daily import build_bundle
        from xiaomao.delivery_state import annotate_bundle
        from xiaomao.feature_daily import project_units
        from xiaomao.store import open_db_readonly
        with open_db_readonly(Path(self.cfg.home) / "xiaomao.sqlite") as conn:
            bundle = build_bundle(self.cfg, conn, date="2026-10-02", now=NOW)
        annotate_bundle(bundle)
        site = next(p for p in bundle["projects"] if p["display_name"] == "xiuqiu-site")
        merged_sha = next(s for s, st in site["delivery_state"]["commits"].items() if st == "merged")
        units = [u for u in project_units(site) if u.get("sha") == merged_sha]
        record = {"record_id": "feature:x", "feature_name": "简报筛选", "after": "没有结果时可以一键清除筛选。",
                  "impact": "", "progress": "behavior", "today": True, "priority": 3, "variant": "",
                  "interpretation": "model_static", "unit_ids": [u["unit_id"] for u in units]}
        text = render_plain_summary(bundle, {site["repo_id"]: {"records": [record], "accepted": 1}},
                                    supplemented=True)
        self.assertIn("· 已补充解读", text.splitlines()[0])
        self.assertIn("没有结果时可以一键清除筛选  🟢 已合并", text)
        self.assertNotIn("筛选可一键清除", text)


if __name__ == "__main__":
    unittest.main()
