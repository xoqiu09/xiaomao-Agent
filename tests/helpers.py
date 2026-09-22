from __future__ import annotations

import os
import subprocess
from pathlib import Path

from xiaomao.config import AppConfig, default_config


GIT_ENV = {
    "GIT_AUTHOR_NAME": "xiaomao-test",
    "GIT_AUTHOR_EMAIL": "xiaomao-test@example.invalid",
    "GIT_COMMITTER_NAME": "xiaomao-test",
    "GIT_COMMITTER_EMAIL": "xiaomao-test@example.invalid",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
}


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(GIT_ENV)
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"git {args} failed: {proc.stderr}")
    return proc


def init_repo(path: Path, *, initial_commit: bool = True) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "xiaomao-test")
    git(path, "config", "user.email", "xiaomao-test@example.invalid")
    if initial_commit:
        (path / "README.md").write_text("hello\n", encoding="utf-8")
        git(path, "add", "README.md")
        git(path, "commit", "-m", "init")
    return path


def website_fixture_config(home: Path, repo: Path, extra_root: Path | None = None) -> AppConfig:
    """Isolate tests from the live allow-list of real worktrees."""
    cfg = default_config(home)
    cfg.home = str(home)
    extra_root = extra_root or Path(repo).parent
    website = cfg.projects[0]
    website.approved_root = str(repo)
    website.worktrees[0].path = str(repo)
    website.worktrees[0].worktree_id = "website-main"
    website.worktrees[0].scan = True
    for extra in website.worktrees[1:]:
        extra.path = str(extra_root / extra.worktree_id)
        extra.scan = False
    cfg.projects = [website]
    return cfg
