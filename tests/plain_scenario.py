"""Synthetic evening-report scenario: four delivery states, no remote, non-main default.

Never reads user repositories; remotes are local bare repositories.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests.helpers import git, init_repo, website_fixture_config
from xiaomao.config import ProjectSpec, WorktreeSpec, save_config

NOW = datetime(2026, 10, 2, 14, tzinfo=timezone.utc)


def _commit(repo: Path, name: str, body: str, message: str, at: str) -> None:
    (repo / name).write_text(body)
    git(repo, "add", name)
    with patch.dict("os.environ", {"GIT_COMMITTER_DATE": at, "GIT_AUTHOR_DATE": at}):
        git(repo, "commit", "-m", message)


def _with_remote(root: Path, name: str, default: str) -> Path:
    repo = init_repo(root / name)
    git(repo, "branch", "-m", default)
    bare = root / f"{name}.git"
    git(root, "clone", "--bare", str(repo), str(bare))
    git(bare, "symbolic-ref", "HEAD", f"refs/heads/{default}")
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "fetch", "origin")
    git(repo, "remote", "set-head", "origin", default)
    git(repo, "branch", f"--set-upstream-to=origin/{default}", default)
    return repo


def build(root: Path):
    """Return (cfg, scan) with day-0 baseline collected; call `changes(scan)` next."""
    site = _with_remote(root, "site", "main")
    wallet = _with_remote(root, "wallet", "trunk")
    notes = init_repo(root / "notes")           # no remote at all
    quiet = init_repo(root / "quiet")
    cfg = website_fixture_config(root / "home", site)
    cfg.projects[0].display_name = "xiuqiu-site"
    cfg.projects.extend([
        ProjectSpec("wallet", "Wallet", str(wallet), [WorktreeSpec("wallet-main", str(wallet))]),
        ProjectSpec("notes", "笔记工具", str(notes), [WorktreeSpec("notes-main", str(notes))]),
        ProjectSpec("quiet", "旷野之心", str(quiet), [WorktreeSpec("quiet-main", str(quiet))]),
    ])
    save_config(cfg)
    return cfg, {"site": site, "wallet": wallet, "notes": notes, "quiet": quiet}


def scan(cfg, at: str) -> None:
    from xiaomao.collect import scan_authorized
    from xiaomao.store import open_db
    with open_db(Path(cfg.home) / "xiaomao.sqlite") as conn, patch("xiaomao.collect.utc_now", return_value=at):
        scan_authorized(conn, cfg)


def make_day(cfg, repos) -> None:
    scan(cfg, "2026-10-01T13:00:00+00:00")
    site, wallet, notes = repos["site"], repos["wallet"], repos["notes"]
    # site: merged into main, pushed feature branch, local-only commit, dirty file.
    _commit(site, "filters.py", "def clear_filters():\n    return []\n", "筛选可一键清除", "2026-10-02T02:00:00+00:00")
    git(site, "push", "origin", "main")
    git(site, "switch", "-c", "feat/sources")
    _commit(site, "sources.py", "SOURCES = {'news': 'rss'}\n", "数据来源可配置", "2026-10-02T03:00:00+00:00")
    git(site, "push", "-u", "origin", "feat/sources")
    _commit(site, "empty.py", "EMPTY_HINT = '没有结果'\n", "空结果提示", "2026-10-02T04:00:00+00:00")
    (site / "draft.py").write_text("draft = True\n")
    # wallet: default branch is trunk; a branch named main is NOT merged.
    _commit(wallet, "guard.py", "def pause_signing():\n    return True\n", "对账异常时暂停签名", "2026-10-02T05:00:00+00:00")
    git(wallet, "push", "origin", "trunk")
    git(wallet, "switch", "-c", "main")
    _commit(wallet, "recover.py", "def recover():\n    return 'ok'\n", "对账恢复入口", "2026-10-02T05:30:00+00:00")
    git(wallet, "push", "-u", "origin", "main")
    # notes: no remote; one commit plus an uncommitted edit.
    _commit(notes, "todo.py", "TODO = []\n", "待办列表", "2026-10-02T06:00:00+00:00")
    (notes / "todo.py").write_text("TODO = ['写晚报']\n")
    scan(cfg, "2026-10-02T07:00:00+00:00")
