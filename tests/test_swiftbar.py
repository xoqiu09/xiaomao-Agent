from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from xiaomao.store import SCHEMA, utc_now
from xiaomao.swiftbar import (
    STALE_AFTER_S,
    file_href,
    latest_daily,
    relative_zh,
    render_menu,
)


TZ = ZoneInfo("Asia/Taipei")


def _write_home(root: Path) -> Path:
    home = root / "home"
    (home / "reports" / "daily").mkdir(parents=True)
    (home / "reports" / "handoff").mkdir(parents=True)
    (home / "reports" / "projects").mkdir(parents=True)
    (home / "config.json").write_text(
        '{"timezone": "Asia/Taipei", "schema_version": 1}\n',
        encoding="utf-8",
    )
    conn = sqlite3.connect(home / "xiaomao.sqlite")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO projects(project_id, display_name, approved_root, policy_version) VALUES (?,?,?,?)",
        ("website", "The AI 官网后端", "/tmp/website", "1"),
    )
    conn.execute(
        """
        INSERT INTO worktrees(worktree_id, project_id, canonical_path, scan_enabled, notes)
        VALUES (?,?,?,?,?)
        """,
        ("website-main", "website", "/tmp/website", 1, ""),
    )
    conn.commit()
    conn.close()
    return home


def _insert_scan(home: Path, *, finished: datetime, outcome: str = "success", error: str | None = None) -> None:
    conn = sqlite3.connect(home / "xiaomao.sqlite")
    stamp = finished.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    conn.execute(
        """
        INSERT INTO scan_runs(
          run_id, project_id, started_at, finished_at, outcome, last_safe_error,
          inserted, unchanged, gap_since_last_success_s, last_observation_id
        ) VALUES (?, 'website', ?, ?, ?, ?, 0, 1, NULL, NULL)
        """,
        (f"run_{stamp}_{outcome}", stamp, stamp, outcome, error),
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
            "obs_1",
            "website",
            "website-main",
            utc_now(),
            "Asia/Taipei",
            "abc",
            "refs/heads/main",
            None,
            "git",
            status,
            f"fp-{status}-{staged}-{unstaged}-{untracked}",
            staged,
            unstaged,
            untracked,
            "{}",
        ),
    )
    conn.commit()
    conn.close()


class SwiftbarTests(unittest.TestCase):
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
        self.assertIn("🐱 小猫｜上次检查：3 分钟前", menu)
        self.assertIn("最近一次扫描：成功", menu)
        self.assertIn("需要关注：0 项", menu)
        self.assertIn("日报更新时间：今天 22:40", menu)
        self.assertNotIn("信息已过期", menu)
        self.assertIn("打开最新日报 | href=", menu)
        self.assertIn(file_href(daily), menu)
        self.assertIn("只读入口：不扫描业务仓、不加载模型", menu)
        self.assertNotIn("--with-model", menu)
        self.assertNotIn("xiaomao scan", menu)

    def test_stale_scan_shows_expired_not_green(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        home = _write_home(root)
        now = datetime(2026, 9, 21, 14, 40, tzinfo=timezone.utc)
        _insert_scan(home, finished=now - timedelta(seconds=STALE_AFTER_S + 30))
        menu = render_menu(home, now=now)
        self.assertIn("🐱 小猫｜信息已过期", menu)
        self.assertIn("扫描结果已过期", menu)
        self.assertNotIn("上次检查：", menu.split("\n", 1)[0])

    def test_error_scan_and_dirty_tree_count_as_attention(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        home = _write_home(root)
        now = datetime(2026, 9, 21, 14, 40, tzinfo=timezone.utc)
        _insert_scan(home, finished=now - timedelta(minutes=2), outcome="error", error="not a git worktree")
        _insert_obs(home, status="error", unstaged=2)
        handoff = home / "reports" / "handoff" / "website-20260921-141404.txt"
        handoff.write_text("handoff\n", encoding="utf-8")
        menu = render_menu(home, now=now)
        self.assertIn("最近一次扫描：失败", menu)
        self.assertIn("website-main 采集状态：error", menu)
        self.assertIn("website-main 工作区 dirty", menu)
        self.assertIn("打开最新 Handoff | href=", menu)
        self.assertIn(file_href(handoff), menu)
        self.assertIn("打开报告文件夹 | href=", menu)

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
