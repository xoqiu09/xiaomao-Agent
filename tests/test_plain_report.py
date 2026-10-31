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
        # Only baselines and collection intervals here: nothing failed, no issue line.
        self.assertNotIn("采集问题", self.body)
        self.assertIn("首次观察只建立基线", self.evidence)
        steps = self.body.split("明天从这里继续\n", 1)[1].split("\n\n", 1)[0].splitlines()
        self.assertTrue(2 <= len(steps) <= 3)
        self.assertIn("推送本地的 1 个提交", steps[0])

    def test_menu_uses_the_same_plain_wording(self):
        from xiaomao.menu_briefing import parse_menu_briefing
        menu = (Path(self.cfg.home) / "reports/briefing/menu-2026-10-02.txt").read_text()
        today, idle, _model_used = parse_menu_briefing(menu)
        self.assertEqual(today[0], self.body.splitlines()[1])
        self.assertEqual(today[1], "xiuqiu-site：空结果提示  🟠 仅本机（另有 2 项）")
        self.assertIn("笔记工具（无远端）：待办列表  🟠 仅本机", today)
        self.assertEqual(today[-1], "明天先：xiuqiu-site：推送本地的 1 个提交")
        self.assertLessEqual(len(today), 5)
        for term in (*BANNED_TERMS, "待确认", "部分覆盖", "未观察到新增变化", "历史活跃度"):
            self.assertNotIn(term, menu)
        self.assertNotIn("旷野之心", "".join(today))

    def test_evidence_keeps_full_detail(self):
        self.assertIn("功能日报（完整版）", self.evidence)
        self.assertIn("代码位置（生成时）", self.evidence)
        self.assertIn("依据：origin/HEAD", self.evidence)
        self.assertIn("refs/remotes/origin/trunk", self.evidence)

    def test_busy_day_like_10_13_stays_within_limits(self):
        """7 projects, dozens of commits and 72 dirty files, many gaps (10-13 shape)."""
        def project(i, commits, dirty, has_remote=True):
            shas = [f"{i:02d}{n:038d}" for n in range(commits)]
            states = ["merged", "pushed", "local"]
            return {"project_id": f"p{i}", "display_name": f"项目{i}", "repo_id": f"r{i}",
                    "changed": bool(commits or dirty), "trees": [], "activity": [],
                    "commits": [{"sha": s, "subject": f"功能变化 {n}：" + "很长的说明" * 20,
                                 "evidence_id": f"commit:r{i}:{s}", "files": []} for n, s in enumerate(shas)],
                    "ongoing": [{"tree_id": "t", "files": [{"path": f"f{n}.py"} for n in range(dirty)],
                                 "observation_id": f"o{i}"}] if dirty else [],
                    # Routine gaps (not counted) plus one real scan failure per project.
                    "gaps": ["首次接入或缺少窗口起点基线；此前未提交修改不可恢复",
                             "采集缺口：2026-10-13T01:00:00+00:00 → 2026-10-13T03:00:00+00:00",
                             f"t{i}: 采集 error"],
                    "limitations": ["a.py: diff_truncated", "b.py: metadata_only", "c.py: unreadable"],
                    "delivery_state": {"has_remote": has_remote, "default_ref": "refs/remotes/origin/main",
                                       "default_basis": "remote_head", "errors": [],
                                       "commits": {s: states[n % 3] if has_remote else "local"
                                                   for n, s in enumerate(shas)},
                                       "unpushed": shas[2::3], "dirty_files": dirty, "dirty_trees": 1 if dirty else 0}}
        bundle = {"window": {"date": "2026-10-13"}, "preview": False,
                  "inventory_errors": ["/x: inventory_depth_limit", "p1: /y: worktree_metadata_unavailable"],
                  "candidates": [{"path": "/x"}],
                  "projects": [project(1, 12, 72), project(2, 9, 3), project(3, 4, 0, has_remote=False),
                               project(4, 2, 1), project(5, 0, 0), project(6, 0, 0), project(7, 0, 0)]}
        text = render_plain_summary(bundle, {})
        lines = text.splitlines()
        self.assertLessEqual(len(lines), PLAIN_MAX_LINES)
        self.assertLessEqual(sum(l.startswith("★ ") for l in lines), 3)
        for term in BANNED_TERMS:
            self.assertNotIn(term, text)
        self.assertIn("今天没动：项目5、项目6、项目7", text)
        # 7 scan errors + 7 unreadable files + 1 inventory read failure = 15;
        # baselines, intervals, truncation, depth limit and candidates are not counted.
        self.assertIn("15 处采集问题，详见依据 → 菜单「查看依据」", lines)
        self.assertEqual(sum("采集问题" in l for l in lines), 1)
        self.assertIn("· 项目4：", text)                 # 4th active project folded to one line
        self.assertIn("72 处未提交改动，建议分批提交", text)
        self.assertTrue(all(len(l) <= 90 for l in lines), max(lines, key=len))

    def test_squash_merged_copy_shows_once_and_no_false_merge_step(self):
        branch, landed = "a" * 40, "b" * 40
        project = {"project_id": "s", "display_name": "站点", "repo_id": "rs", "changed": True,
                   "trees": [], "activity": [], "ongoing": [], "gaps": [], "limitations": [],
                   "commits": [{"sha": branch, "subject": "content: radar 10-30", "files": [], "evidence_id": "commit:rs:" + branch},
                               {"sha": landed, "subject": "content: radar 10-30 (#129)", "files": [], "evidence_id": "commit:rs:" + landed}],
                   "delivery_state": {"has_remote": True, "default_ref": "refs/remotes/origin/main",
                                      "default_basis": "remote_head", "errors": [], "unpushed": [],
                                      "unpushed_stale": [], "merged_by_content": [branch],
                                      "commits": {branch: "merged", landed: "merged"},
                                      "dirty_files": 0, "dirty_trees": 0}}
        bundle = {"window": {"date": "2026-10-30"}, "preview": False, "inventory_errors": [],
                  "candidates": [], "projects": [project]}
        text = render_plain_summary(bundle, {})
        self.assertEqual(text.count("content: radar 10-30"), 1)
        self.assertIn("content: radar 10-30 (#129)  🟢 已合并", text)
        self.assertNotIn("还没合进", text)

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
