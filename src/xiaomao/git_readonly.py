from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

READONLY_ENV = {
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_PAGER": "cat",
    "GIT_EDITOR": "true",
    "GCM_INTERACTIVE": "never",
    "GIT_ASKPASS": "",
    "GIT_EXTERNAL_DIFF": "",
}

READONLY_CONFIG = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "core.pager=",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "diff.external=",
    "-c",
    "advice.detachedHead=false",
    "-c",
    "filter.lfs.smudge=",
    "-c",
    "filter.lfs.clean=",
    "-c",
    "filter.lfs.process=",
    "-c",
    "filter.lfs.required=false",
    "-c",
    "submodule.recurse=false",
)


class GitReadError(RuntimeError):
    def __init__(self, message: str, *, argv: Sequence[str] | None = None, stderr: str = ""):
        super().__init__(message)
        self.argv = list(argv or [])
        self.stderr = stderr


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(READONLY_ENV)
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env.pop("GIT_INDEX_FILE", None)
    env.pop("GIT_OBJECT_DIRECTORY", None)
    env.pop("GIT_ALTERNATE_OBJECT_DIRECTORIES", None)
    return env


def run_git(worktree: Path, *args: str, check: bool = True, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    argv = [
        "git",
        "--no-optional-locks",
        "-C",
        str(worktree),
        *READONLY_CONFIG,
        *args,
    ]
    try:
        proc = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_git_env(),
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitReadError(f"git timed out: {args[0] if args else argv}", argv=argv) from exc
    if check and proc.returncode != 0:
        raise GitReadError(
            f"git {args[0] if args else ''} failed ({proc.returncode}): {proc.stderr.strip()}",
            argv=argv,
            stderr=proc.stderr,
        )
    return proc


@dataclass
class StatusEntry:
    staged: str
    unstaged: str
    path: str
    orig_path: str | None = None

    @property
    def is_untracked(self) -> bool:
        return self.staged == "?" and self.unstaged == "?"

    @property
    def is_ignored(self) -> bool:
        return self.staged == "!" and self.unstaged == "!"


@dataclass
class DiscoveredWorktree:
    path: str
    head: str | None
    branch: str | None
    detached: bool
    bare: bool = False


@dataclass
class GitSnapshot:
    toplevel: str
    git_dir: str
    git_common_dir: str
    head_oid: str | None
    branch_ref: str | None
    is_detached: bool
    is_unborn: bool
    status: list[StatusEntry] = field(default_factory=list)
    worktrees: list[DiscoveredWorktree] = field(default_factory=list)
    collection_status: str = "ok"
    error: str | None = None
    pre_head: str | None = None
    post_head: str | None = None

    @property
    def staged(self) -> list[StatusEntry]:
        return [e for e in self.status if e.staged not in {" ", "?", "!"} and not e.is_untracked]

    @property
    def unstaged(self) -> list[StatusEntry]:
        return [e for e in self.status if e.unstaged not in {" ", "?", "!"} and not e.is_untracked]

    @property
    def untracked(self) -> list[StatusEntry]:
        return [e for e in self.status if e.is_untracked]


def parse_status_z(blob: str) -> list[StatusEntry]:
    if not blob:
        return []
    parts = blob.split("\0")
    entries: list[StatusEntry] = []
    i = 0
    while i < len(parts):
        token = parts[i]
        if not token:
            i += 1
            continue
        if len(token) < 3 or token[2] != " ":
            i += 1
            continue
        xy, path = token[:2], token[3:]
        orig = None
        if xy[0] in {"R", "C"}:
            i += 1
            if i < len(parts):
                orig = path
                path = parts[i]
        entries.append(StatusEntry(staged=xy[0], unstaged=xy[1], path=path, orig_path=orig))
        i += 1
    return entries


def parse_worktree_porcelain(text: str) -> list[DiscoveredWorktree]:
    blocks = text.split("\n\n")
    result: list[DiscoveredWorktree] = []
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln]
        if not lines:
            continue
        path = head = branch = None
        detached = False
        bare = False
        for ln in lines:
            if ln.startswith("worktree "):
                path = ln[len("worktree ") :]
            elif ln.startswith("HEAD "):
                head = ln[len("HEAD ") :]
            elif ln.startswith("branch "):
                branch = ln[len("branch ") :]
            elif ln == "detached":
                detached = True
            elif ln == "bare":
                bare = True
        if path:
            result.append(
                DiscoveredWorktree(
                    path=path,
                    head=head,
                    branch=branch,
                    detached=detached,
                    bare=bare,
                )
            )
    return result


def _head_state(worktree: Path) -> tuple[str | None, str | None, bool, bool]:
    unborn = False
    detached = False
    head_oid = None
    branch_ref = None

    verified = run_git(worktree, "rev-parse", "--verify", "HEAD", check=False)
    if verified.returncode != 0:
        unborn = True
    else:
        head_oid = verified.stdout.strip() or None

    symbolic = run_git(worktree, "symbolic-ref", "-q", "HEAD", check=False)
    if symbolic.returncode == 0:
        branch_ref = symbolic.stdout.strip() or None
    else:
        if not unborn:
            detached = True
            branch_ref = None
        else:
            # Unborn still has a symbolic ref in a normal `git init`.
            fallback = run_git(worktree, "symbolic-ref", "HEAD", check=False)
            branch_ref = fallback.stdout.strip() or None
    return head_oid, branch_ref, detached, unborn


def collect_snapshot(worktree: Path) -> GitSnapshot:
    worktree = Path(worktree)
    if not worktree.exists():
        raise GitReadError(f"worktree does not exist: {worktree}")

    inside = run_git(worktree, "rev-parse", "--is-inside-work-tree", check=False)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise GitReadError(f"not a git worktree: {worktree}", stderr=inside.stderr)

    toplevel = run_git(worktree, "rev-parse", "--show-toplevel").stdout.strip()
    git_dir = run_git(worktree, "rev-parse", "--git-dir").stdout.strip()
    common = run_git(worktree, "rev-parse", "--git-common-dir").stdout.strip()
    git_dir_abs = str((worktree / git_dir).resolve()) if not Path(git_dir).is_absolute() else git_dir
    common_abs = str((worktree / common).resolve()) if not Path(common).is_absolute() else common

    pre_head, branch_ref, detached, unborn = _head_state(worktree)

    status_proc = run_git(
        worktree,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignore-submodules=all",
        "--no-renames",
    )
    entries = parse_status_z(status_proc.stdout)

    wt_proc = run_git(worktree, "worktree", "list", "--porcelain")
    worktrees = parse_worktree_porcelain(wt_proc.stdout)

    post_head, _, _, post_unborn = _head_state(worktree)
    collection_status = "ok"
    error = None
    if pre_head != post_head or unborn != post_unborn:
        collection_status = "inconsistent"
        error = "HEAD changed during scan"

    return GitSnapshot(
        toplevel=toplevel,
        git_dir=git_dir_abs,
        git_common_dir=common_abs,
        head_oid=post_head,
        branch_ref=branch_ref,
        is_detached=detached,
        is_unborn=unborn or post_unborn,
        status=entries,
        worktrees=worktrees,
        collection_status=collection_status,
        error=error,
        pre_head=pre_head,
        post_head=post_head,
    )


def snapshot_fingerprint(snap: GitSnapshot) -> str:
    import hashlib
    import json

    payload = {
        "toplevel": snap.toplevel,
        "head": snap.head_oid,
        "branch": snap.branch_ref,
        "detached": snap.is_detached,
        "unborn": snap.is_unborn,
        "status": [
            {
                "s": e.staged,
                "u": e.unstaged,
                "p": e.path,
                "o": e.orig_path,
            }
            for e in snap.status
        ],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
