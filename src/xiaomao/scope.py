"""Current project exclusions, checked from paths and bounded Git pointers.

These checks never run Git or open business files/config. They inspect path
names, filesystem links and only .git/commondir pointer metadata, refusing an
excluded target before reading it. A mixed project is refused in full,
including disabled trees.
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xiaomao.config import ProjectSpec


_COMPANY_PREFIXES = ("theaiapp-service", "event-services-chooseme-event")
_ARCHIVE_COMPONENT = "_待删除旧项目_2026-09-22"
_MAX_GIT_POINTER_BYTES = 4096
_THIRD_PARTY_COMPONENTS = frozenset(
    ("stats", "stats.app", "agentnotch", "agentnotch.app", "tokenmonitor", "tokenmonitor.app",
     "token-monitor", "token monitor.app")
)
# These original registrations were confirmed retired. Their compatibility
# links no longer exist, so filesystem resolution alone cannot identify them.
# Keep the deny entries even if a directory is later recreated at the old path.
_RETIRED_REGISTRATION_PARTS = tuple(
    tuple(part.casefold() for part in Path(f"/Users/xiuqiu/WorkSpace/{project_id}").parts)
    for project_id in (
        "agent-accord",
        "agent-stablecoin-wallet",
        "dolphinode",
        "event-watcher",
        "stableflow",
        "wallet",
        "wallet-mpc-sign",
        "wallet-reliability-lab",
        "web3-wallet-engineer-lab",
        "xiuqiu-hermes-skills",
        "xiuqiu-token",
    )
)


def _named_exclusion(path: Path) -> str | None:
    parts = tuple(part.casefold() for part in path.parts)
    for component in path.parts:
        name = component.casefold()
        if any(name.startswith(prefix) for prefix in _COMPANY_PREFIXES):
            return "company_project_excluded"
        if name == _ARCHIVE_COMPONENT.casefold():
            return "archived_project_excluded"
        if name in _THIRD_PARTY_COMPONENTS:
            return "third_party_project_excluded"
    if any(parts[:len(root)] == root for root in _RETIRED_REGISTRATION_PARTS):
        return "archived_project_excluded"
    return None


def path_exclusion_reason(value: str) -> str | None:
    """Return a fail-closed reason without reading a repository's contents."""
    try:
        if not value or "\x00" in value:
            return "invalid_project_path: 路径为空或含无效字符"
        path = Path(value).expanduser()
        if not path.is_absolute():
            return f"invalid_project_path: 需要明确的绝对路径（{value}）"
        # Check the original path first: do not even resolve a known company
        # registration, and do not let a link pointing out of an excluded root
        # make that registration eligible again.
        reason = _named_exclusion(path)
        if reason:
            return f"{reason}: {path}"
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            # Missing paths may still be compatibility links into the archive.
            # Other failures (permissions, link loops) must remain exclusions;
            # non-strict resolve alone suppresses those on newer Python builds.
            resolved = path.resolve(strict=False)
        reason = _named_exclusion(resolved)
        if reason:
            return f"{reason}: {path} -> {resolved}"
    except (OSError, RuntimeError, ValueError):
        return f"invalid_project_path: 无法确认登记路径（{value}）"
    return None


def _read_git_pointer(path: Path) -> str:
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Git 指针必须是普通文件")
    with path.open("rb") as handle:
        raw = handle.read(_MAX_GIT_POINTER_BYTES + 1)
    if len(raw) > _MAX_GIT_POINTER_BYTES:
        raise ValueError("Git 指针超过读取上限")
    value = raw.decode("utf-8").strip()
    if not value or any(char in value for char in ("\n", "\r", "\x00")):
        raise ValueError("Git 指针不是单一有效路径")
    return value


def _git_pointer_exclusion_reason(root: Path) -> str | None:
    git_entry = root / ".git"
    reason = path_exclusion_reason(str(git_entry))
    if reason:
        return f"{reason}（.git 指针）"
    try:
        if not git_entry.exists():
            if git_entry.is_symlink():
                return f"invalid_git_metadata: .git 链接目标不存在（{git_entry}）"
            return None
        if git_entry.is_dir():
            git_dir = git_entry
        else:
            pointer = _read_git_pointer(git_entry)
            if not pointer.startswith("gitdir:") or not pointer[7:].strip():
                return f"invalid_git_metadata: 无效 .git 指针（{git_entry}）"
            git_dir = Path(pointer[7:].strip())
            if not git_dir.is_absolute():
                git_dir = git_entry.parent / git_dir
            reason = path_exclusion_reason(str(git_dir))
            if reason:
                return f"{reason}（gitdir 指针）"

        common_entry = git_dir / "commondir"
        reason = path_exclusion_reason(str(common_entry))
        if reason:
            return f"{reason}（commondir 指针）"
        if not common_entry.exists():
            if common_entry.is_symlink():
                return f"invalid_git_metadata: commondir 链接目标不存在（{common_entry}）"
            return None
        common_dir = Path(_read_git_pointer(common_entry))
        if not common_dir.is_absolute():
            common_dir = git_dir / common_dir
        reason = path_exclusion_reason(str(common_dir))
        if reason:
            return f"{reason}（commondir 目标）"
    except (OSError, RuntimeError, ValueError):
        return f"invalid_git_metadata: 无法确认 Git 指针（{git_entry}）"
    return None


def worktree_exclusion_reason(value: str) -> str | None:
    """Check a registered path before inspecting only its Git pointer metadata."""
    reason = path_exclusion_reason(value)
    if reason:
        return reason
    return _git_pointer_exclusion_reason(Path(value).expanduser())


def project_exclusion_reason(project: ProjectSpec) -> str | None:
    """Refuse the whole project if any declared root or tree is excluded.

    This is a path/pointer boundary, not remote repository-identity discovery.
    Unknown checkouts are never auto-added or mapped to replacement locations.
    """
    declared = [("approved_root", project.approved_root)]
    declared.extend((f"worktree:{wt.worktree_id}", wt.path) for wt in project.worktrees)
    for label, value in declared:
        reason = worktree_exclusion_reason(value)
        if reason:
            return f"{reason}（{label}；整个项目已排除）"
    return None
