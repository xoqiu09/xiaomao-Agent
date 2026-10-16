from __future__ import annotations

import os
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from xiaomao.store import SCHEMA, utc_now
from xiaomao.swiftbar import (
    STALE_AFTER_S,
    _alerts,
    _dirty_inventory,
    _tree_label,
    file_href,
    latest_daily,
    relative_zh,
    render_menu,
)


TZ = ZoneInfo("Asia/Taipei")


def _project_entry(project_id: str, *, menu_hide_dirty: bool = False) -> dict:
    path = f"/tmp/{project_id}"
    return {
        "project_id": project_id,
        "approved_root": path,
        "menu_hide_dirty": menu_hide_dirty,
        "worktrees": [{"worktree_id": f"{project_id}-main", "path": path, "scan": True}],
    }


def _write_home(
    root: Path,
    *,
    menu_hide_dirty: bool = False,
    second_project: str | None = None,
) -> Path:
    home = root / "home"
    (home / "reports" / "daily").mkdir(parents=True)
    (home / "reports" / "handoff").mkdir(parents=True)
    (home / "reports" / "projects").mkdir(parents=True)
    entries = [_project_entry("website", menu_hide_dirty=menu_hide_dirty)]
    if second_project:
        entries.append(_project_entry(second_project))
    (home / "config.json").write_text(
        json.dumps({"timezone": "Asia/Taipei", "schema_version": 1, "projects": entries}),
        encoding="utf-8",
    )
    conn = sqlite3.connect(home / "xiaomao.sqlite")
    conn.executescript(SCHEMA)
    for entry in entries:
        pid = entry["project_id"]
        conn.execute(
            "INSERT INTO projects(project_id, display_name, approved_root, policy_version) VALUES (?,?,?,?)",
            (pid, pid, entry["approved_root"], "1"),
        )
        conn.execute(
            """
            INSERT INTO worktrees(worktree_id, project_id, canonical_path, scan_enabled, notes)
            VALUES (?,?,?,?,?)
            """,
            (f"{pid}-main", pid, entry["approved_root"], 1, ""),
        )
    conn.commit()
    conn.close()
    return home


def _insert_scan(
    home: Path,
    *,
    finished: datetime,
    outcome: str = "success",
    error: str | None = None,
    project_id: str = "website",
) -> None:
    conn = sqlite3.connect(home / "xiaomao.sqlite")
    stamp = finished.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    conn.execute(
        """
        INSERT INTO scan_runs(
          run_id, project_id, started_at, finished_at, outcome, last_safe_error,
          inserted, unchanged, gap_since_last_success_s, last_observation_id
        ) VALUES (?, ?, ?, ?, ?, ?, 0, 1, NULL, NULL)
        """,
        (f"run_{project_id}_{stamp}_{outcome}", project_id, stamp, stamp, outcome, error),
    )
    conn.commit()
    conn.close()


def _insert_obs(
    home: Path,
    *,
    status: str = "ok",
    staged: int = 0,
    unstaged: int = 0,
    untracked: int = 0,
    project_id: str = "website",
    worktree_id: str = "website-main",
    observation_id: str = "obs_1",
    paths: dict[str, list[str]] | None = None,
) -> None:
    conn = sqlite3.connect(home / "xiaomao.sqlite")
    conn.execute(
        """
        INSERT INTO observations(
          observation_id, project_id, worktree_id, observed_at_utc, local_timezone,
          head_oid, branch_ref, dirty_snapshot_id, source_type, collection_status,
          fingerprint, staged_count, unstaged_count, untracked_count, facts_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            observation_id,
            project_id,
            worktree_id,
            utc_now(),
            "Asia/Taipei",
            "abc",
            "refs/heads/main",
            None,
            "git",
            status,
            f"fp-{worktree_id}-{status}-{staged}-{unstaged}-{untracked}",
            staged,
            unstaged,
            untracked,
            json.dumps(paths or {}),
        ),
    )
    conn.commit()
    conn.close()


class SwiftbarTests(unittest.TestCase):
    def test_branch_prefix_tree_labels_use_project_and_branch(self) -> None:
        row = {
            "worktree_id": "website~deadbeef00",
            "project_id": "website",
            "notes": "branch_prefix",
            "branch_ref": "refs/heads/codex/tree-prefix",
        }
        self.assertEqual(_tree_label(row), "website · codex/tree-prefix")

    def test_branch_prefix_dirty_inventory_uses_human_tree_label(self) -> None:
        state = {
            "worktrees": [{
                "worktree_id": "website~deadbeef00",
                "project_id": "website",
                "notes": "branch_prefix",
                "branch_ref": "refs/heads/codex/tree-prefix",
                "scan_enabled": 1,
                "staged_count": 1,
                "unstaged_count": 2,
                "untracked_count": 1,
                "facts_json": json.dumps({"unstaged": ["src/swiftbar.py"]}),
            }],
        }
        self.assertEqual(
            _dirty_inventory(state, set()),
            ["website · codex/tree-prefix 未提交（staged 1 / unstaged 2 / untracked 1）　swiftbar.py"],
        )

    def test_branch_prefix_collection_error_alert_uses_human_tree_label(self) -> None:
        state = {
            "db_ok": True,
            "worktrees": [{
                "worktree_id": "website~deadbeef00",
                "project_id": "website",
                "notes": "branch_prefix",
                "branch_ref": "refs/heads/codex/tree-prefix",
                "scan_enabled": 1,
                "collection_status": "error",
            }],
        }
        self.assertEqual(
            _alerts(state, stale=False),
            ["website · codex/tree-prefix 采集状态：error"],
        )

    def test_relative_zh(self) -> None:
        self.assertEqual(relative_zh(10), "刚刚")
        self.assertEqual(relative_zh(180), "3 分钟前")
        self.assertEqual(relative_zh(7200), "2 小时前")

    def test_fresh_success_is_not_stale(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        home = _write_home(root)
        now = datetime(2026, 9, 21, 14, 40, tzinfo=timezone.utc)
        _insert_scan(home, finished=now - timedelta(minutes=3))
        daily = home / "reports" / "daily" / "2026-09-21.txt"
        daily.write_text("日报\n", encoding="utf-8")
        os.utime(daily, (now.timestamp(), now.timestamp()))
        menu = render_menu(home, now=now)
        title = menu.split("\n", 1)[0]
        self.assertTrue(title.startswith(" | image=") or title.startswith(" | emojize="))
        self.assertNotIn("上次检查", title)
        self.assertNotIn("🐱", title)
        self.assertIn("今天", menu)
        self.assertIn("很久没看", menu)
        self.assertIn("本次没有模型解读", menu)
        self.assertNotIn("交接待核实", menu)
        self.assertIn("扫描：成功 · 1 棵树", menu)
        self.assertIn("需要处理：无", menu)
        self.assertIn("技术细节", menu)
        self.assertIn("工作区未提交：0 项", menu)
        self.assertNotIn("信息已过期", menu)
        self.assertIn("最新日报范围未核实", menu)
        self.assertNotIn(file_href(daily), menu)
        self.assertIn("只读 · 不扫描业务仓 · 不加载模型", menu)
        self.assertNotIn("--with-model", menu)
        self.assertNotIn("xiaomao scan", menu)

    def test_missing_handoff_has_no_terminal_button(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        home = _write_home(root)
        now = datetime(2026, 9, 21, 14, 40, tzinfo=timezone.utc)
        _insert_scan(home, finished=now - timedelta(minutes=3))
        menu = render_menu(home, now=now)
        self.assertIn("尚无交接：website", menu)
        self.assertNotIn("查看交接与未核实项", menu)
        self.assertNotIn("terminal=true", menu)

    def test_stale_scan_shows_expired_not_green(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        home = _write_home(root)
        now = datetime(2026, 9, 21, 14, 40, tzinfo=timezone.utc)
        _insert_scan(home, finished=now - timedelta(seconds=STALE_AFTER_S + 30))
        menu = render_menu(home, now=now)
        title = menu.split("\n", 1)[0]
        self.assertIn("信息已过期", title)
        self.assertIn("image=", title)
        self.assertNotIn("🐱", title)
        self.assertIn("扫描结果已过期", menu)
        self.assertNotIn("上次检查：", title)

    def test_scan_fault_is_an_alert_and_dirty_stays_an_inventory(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        home = _write_home(root)
        now = datetime(2026, 9, 21, 14, 40, tzinfo=timezone.utc)
        # A recent success keeps the data fresh, so the title reports the
        # failure of the latest run rather than expiry.
        _insert_scan(home, finished=now - timedelta(minutes=4))
        _insert_scan(home, finished=now - timedelta(minutes=2), outcome="error", error="not a git worktree")
        _insert_obs(home, status="error", unstaged=2, paths={"unstaged": ["wallet/bnb.ts", "test/bnb.test.ts"]})
        handoff = home / "reports" / "handoff" / "website-20260921-141404.txt"
        handoff.write_text("handoff\n", encoding="utf-8")
        menu = render_menu(home, now=now)
        title = menu.split("\n", 1)[0]
        self.assertIn("扫描失败", title)
        self.assertNotIn("信息已过期", menu.split("---", 1)[0])
        self.assertNotIn("🐱", title)
        self.assertIn("扫描：失败 · 1 棵树", menu)
        self.assertIn("需要处理：2 项", menu)
        self.assertIn("-- 最近一次扫描失败：not a git worktree", menu)
        self.assertIn("-- website-main 采集状态：error", menu)
        # Uncommitted work is inventory under 技术细节, not a fault.
        self.assertIn("技术细节", menu)
        self.assertIn("工作区未提交：1 项", menu)
        self.assertIn("---- website-main 未提交（staged 0 / unstaged 2 / untracked 0）", menu)
        self.assertIn("bnb.ts", menu)
        today_block = menu.split("很久没看", 1)[0]
        self.assertNotIn("wallet/bnb.ts", today_block)
        self.assertIn("website：未核实", menu)
        self.assertIn("旧交接没有来源校验信息", menu)
        self.assertIn("查看交接与未核实项：website | bash=", menu)
        self.assertIn("打开报告文件夹 | href=", menu)

    def test_quieted_project_hides_dirty_but_not_collection_error(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        home = _write_home(root, menu_hide_dirty=True)
        now = datetime(2026, 9, 21, 14, 40, tzinfo=timezone.utc)
        _insert_scan(home, finished=now - timedelta(minutes=2))
        _insert_obs(home, unstaged=2, paths={"unstaged": ["wallet/bnb.ts", "test/bnb.test.ts"]})
        menu = render_menu(home, now=now)
        self.assertIn("工作区未提交：0 项", menu)
        self.assertNotIn("未提交（staged", menu)
        self.assertNotIn("bnb.ts", menu)
        self.assertIn("已静音（仍在扫描与日报里）：website", menu)

        _insert_obs(home, status="error", unstaged=2, observation_id="obs_2")
        menu = render_menu(home, now=now)
        self.assertIn("-- website-main 采集状态：error", menu)

    def test_second_project_dirty_still_listed_when_one_is_quieted(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        home = _write_home(root, menu_hide_dirty=True, second_project="notes")
        now = datetime(2026, 9, 21, 14, 40, tzinfo=timezone.utc)
        _insert_scan(home, finished=now - timedelta(minutes=2))
        _insert_scan(home, finished=now - timedelta(minutes=2), project_id="notes")
        _insert_obs(home, unstaged=2, paths={"unstaged": ["wallet/bnb.ts"]})
        _insert_obs(
            home,
            project_id="notes",
            worktree_id="notes-main",
            observation_id="obs_notes",
            unstaged=1,
            paths={"unstaged": ["CURRENT.md"]},
        )
        menu = render_menu(home, now=now)
        self.assertIn("工作区未提交：1 项", menu)
        self.assertIn("---- notes-main 未提交（staged 0 / unstaged 1 / untracked 0）", menu)
        self.assertIn("CURRENT.md", menu)
        self.assertNotIn("website-main 未提交", menu)

    def test_failed_scan_without_any_success_still_reads_expired(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        home = _write_home(root)
        now = datetime(2026, 9, 21, 14, 40, tzinfo=timezone.utc)
        _insert_scan(home, finished=now - timedelta(minutes=2), outcome="error", error="not a git worktree")
        menu = render_menu(home, now=now)
        title = menu.split("\n", 1)[0]
        self.assertIn("信息已过期", title)
        self.assertIn("尚无成功扫描记录", menu)
        self.assertIn("-- 最近一次扫描失败：not a git worktree", menu)

    def test_missing_home_is_expired(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        menu = render_menu(root / "missing")
        self.assertIn("信息已过期", menu)
        self.assertIn("还没有状态库", menu)
        self.assertIn("打开最新日报（尚无文件）", menu)

    def test_latest_daily_skips_model_copy(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        folder = root / "reports" / "daily"
        folder.mkdir(parents=True)
        (folder / "2026-09-20.txt").write_text("a\n", encoding="utf-8")
        (folder / "2026-09-21.txt").write_text("b\n", encoding="utf-8")
        (folder / "2026-09-21.model.txt").write_text("model\n", encoding="utf-8")
        self.assertEqual(latest_daily(root).name, "2026-09-21.txt")

    def test_today_section_talks_modules_not_files(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        home = _write_home(root)
        now = datetime(2026, 9, 23, 6, 0, tzinfo=timezone.utc)
        _insert_scan(home, finished=now - timedelta(minutes=2))
        _insert_obs(
            home,
            unstaged=1,
            paths={
                "unstaged": ["wallet/bnb.ts"],
                "last_commit_at": "2026-09-23T04:00:00+00:00",
                "recent_subjects": ["align ledger gates"],
                "module_digest": {
                    "new_modules": [],
                    "gone_modules": [],
                    "changed_modules": ["internal/funds"],
                },
            },
        )
        menu = render_menu(home, now=now)
        self.assertIn("website：动了 internal/funds", menu)
        today_block = menu.split("很久没看", 1)[0]
        self.assertNotIn("wallet/bnb.ts", today_block)
        self.assertNotIn("bnb.ts", today_block)
        self.assertIn("---- website-main 未提交", menu)
