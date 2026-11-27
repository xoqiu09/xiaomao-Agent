"""Where the code is: uncommitted / local only / pushed / merged.

Read-only: uses only the local `refs/remotes/*` (no fetch), through
`git_readonly.run_git`. Local remote refs update only on push or fetch, so a
commit pushed from another machine may still read as local; that is a known
stage-1 limit, recorded in the evidence, not hidden.

A rebase merge or a one-commit squash merge lands the same change on the
default branch under a new SHA. `git cherry` (patch-id, read-only) recognises
that, so such a commit reads as merged instead of pushed or local forever.
A squash of several commits still reads by ancestry; that limit is recorded.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from xiaomao.git_readonly import GitReadError, run_git

ORDER = ("uncommitted", "local", "pushed", "merged")
LABELS = {"uncommitted": "🟡 未提交", "local": "🟠 仅本机", "pushed": "🔵 已推送", "merged": "🟢 已合并"}
MAX_UNPUSHED = 1000
MAX_CONTENT_CHECKS = 200
# Unpushed commits older than this stay in the evidence but no longer drive
# the "push" reminder: weeks-old branches are usually abandoned, not pending.
STALE_LOCAL_DAYS = 7


def _remote_refs(path: Path) -> dict[str, str]:
    """Concrete remote branches; symbolic refs such as origin/HEAD excluded."""
    raw = run_git(path, "for-each-ref", "--format=%(refname)%00%(symref)", "refs/remotes").stdout
    refs = {}
    for line in raw.splitlines():
        name, _, symref = line.partition("\0")
        if name and not symref:
            refs[name] = name
    return refs


def remote_view(path: Path) -> dict:
    remotes = run_git(path, "remote").stdout.split()
    if not remotes:
        return {"has_remote": False, "remotes": [], "default_ref": None, "default_basis": None}
    refs = _remote_refs(path)
    primary = "origin" if "origin" in remotes else remotes[0] if len(remotes) == 1 else None
    default, basis = None, None
    if primary:
        head = run_git(path, "symbolic-ref", "-q", f"refs/remotes/{primary}/HEAD", check=False)
        target = head.stdout.strip()
        if head.returncode == 0 and target in refs:
            default, basis = target, "remote_head"
        else:
            # `git remote add` + push never records origin/HEAD; only then
            # fall back to the conventional names, and say so in the evidence.
            for name in ("main", "master"):
                if f"refs/remotes/{primary}/{name}" in refs:
                    default, basis = f"refs/remotes/{primary}/{name}", "conventional_name"
                    break
    return {"has_remote": True, "remotes": remotes, "default_ref": default,
            "default_basis": basis, "refs": sorted(refs)}


def commit_state(path: Path, oid: str, view: dict) -> str | None:
    """local / pushed / merged for one commit; None when Git cannot tell."""
    if not view["has_remote"]:
        return "local"
    proc = run_git(path, "for-each-ref", "--contains", oid, "--format=%(refname)%00%(symref)",
                   "refs/remotes", check=False)
    if proc.returncode:
        return None
    containing = {name for name, _, symref in (line.partition("\0") for line in proc.stdout.splitlines())
                  if name and not symref}
    if view["default_ref"] and view["default_ref"] in containing:
        return "merged"
    return "pushed" if containing else "local"


def unpushed_commits(path: Path, view: dict, *, other_branches: bool = False) -> list[str] | None:
    """List candidates from HEAD or other local branches, with one extra to detect truncation."""
    if not view["has_remote"]:
        return None
    refs = ("--branches", "--not", "HEAD", "--remotes") if other_branches else ("HEAD", "--not", "--remotes")
    proc = run_git(path, "rev-list", f"--max-count={MAX_UNPUSHED + 1}", *refs, check=False)
    return proc.stdout.split() if proc.returncode == 0 else None


def merged_by_content(path: Path, oid: str, view: dict) -> bool:
    """The same change already sits on the default branch under another SHA."""
    if not view.get("default_ref"):
        return False
    # Root commits have no parent to diff against; merge commits list several lines.
    proc = run_git(path, "cherry", view["default_ref"], oid, f"{oid}^", check=False, timeout=10)
    lines = proc.stdout.splitlines()
    return proc.returncode == 0 and len(lines) == 1 and lines[0] == f"- {oid}"


def commit_times(path: Path, oids: list[str]) -> dict[str, datetime]:
    if not oids:
        return {}
    proc = run_git(path, "log", "--no-walk=unsorted", "--format=%H %ct", *oids, check=False)
    times = {}
    for line in proc.stdout.splitlines():
        sha, _, stamp = line.partition(" ")
        if stamp.isdigit():
            times[sha] = datetime.fromtimestamp(int(stamp), timezone.utc)
    return times


def earliest(states) -> str | None:
    known = [s for s in states if s in ORDER]
    return min(known, key=ORDER.index) if known else None


def annotate_project(project: dict, now: datetime | None = None) -> dict:
    """Attach current delivery facts to one daily bundle project (read-only)."""
    now = now or datetime.now(timezone.utc)
    live = [t for t in project["trees"] if t.get("path") and not t.get("gone") and Path(t["path"]).is_dir()]
    info = {"has_remote": None, "default_ref": None, "default_basis": None,
            "commits": {}, "unpushed": [], "unpushed_stale": [], "merged_by_content": [],
            "unpushed_unchecked": [], "unpushed_incomplete": False,
            "dirty_files": 0, "dirty_trees": 0, "errors": []}

    for item in project["ongoing"]:
        if item.get("files"):
            info["dirty_trees"] += 1
            info["dirty_files"] += len(item["files"])
    if not live:
        info["errors"].append("no_live_tree")
        project["delivery_state"] = info
        return info
    try:
        view = remote_view(Path(live[0]["path"]))
    except GitReadError:
        info["errors"].append("remote_view_unavailable")
        project["delivery_state"] = info
        return info
    info.update(has_remote=view["has_remote"], default_ref=view["default_ref"],
                default_basis=view["default_basis"])
    paths = {t["tree_id"]: Path(t["path"]) for t in live}
    commit_paths = {}
    for commit in project["commits"]:
        sha = commit["sha"]
        tree = next((paths[t] for t in commit.get("tree_ids", []) if t in paths), Path(live[0]["path"]))
        try:
            state = commit_state(tree, sha, view)
        except GitReadError:
            state = None
        if state is None:
            info["errors"].append(f"commit_state_unavailable:{sha[:12]}")
        elif state in {"local", "pushed"}:
            commit_paths[sha] = tree
        info["commits"][sha] = state
    unpushed = {}
    if view["has_remote"]:
        info["unpushed_incomplete"] = (
            any(not t.get("head") for t in live)
            or len(live) < sum(not t.get("gone") for t in project["trees"])
        )
        # Reserve each tree's HEAD history before adding candidates from other branches.
        for other_branches in (False, True):
            ref_group = "branches" if other_branches else "head"
            for tree in live:
                if not tree.get("head"):
                    continue
                path = paths[tree["tree_id"]]
                try:
                    found = unpushed_commits(path, view, other_branches=other_branches)
                except GitReadError:
                    found = None
                if found is None:
                    info["errors"].append(f"unpushed_unavailable:{tree['tree_id']}:{ref_group}")
                    info["unpushed_incomplete"] = True
                    continue
                if len(found) > MAX_UNPUSHED:
                    info["errors"].append(f"unpushed_truncated:{tree['tree_id']}:{ref_group}")
                    info["unpushed_incomplete"] = True
                for sha in found[:MAX_UNPUSHED]:
                    unpushed.setdefault(sha, path)
    by_tree = {}
    for sha, path in unpushed.items():
        by_tree.setdefault(path, []).append(sha)
    cutoff = now - timedelta(days=STALE_LOCAL_DAYS)
    recent, stale = {}, {}
    for path, shas in by_tree.items():
        try:
            times = commit_times(path, shas)
        except GitReadError:
            times = {}
        for sha in shas:
            # Unknown age counts as recent: never hide a reminder on a read failure.
            (stale if sha in times and times[sha] < cutoff else recent)[sha] = path

    matched, checked = set(), set()
    if view["default_ref"]:
        # Spend the shared budget on reminders first, then the day's labels and old history.
        candidates = recent | commit_paths | stale
        for index, (sha, path) in enumerate(candidates.items()):
            if index >= MAX_CONTENT_CHECKS:
                info["errors"].append("content_check_truncated")
                break
            try:
                merged = merged_by_content(unpushed.get(sha, path), sha, view)
            except GitReadError:
                info["errors"].append(f"content_check_unavailable:{sha[:12]}")
                continue
            checked.add(sha)
            if merged:
                matched.add(sha)
                if sha in info["commits"]:
                    info["commits"][sha] = "merged"
    info["merged_by_content"] = sorted(matched)
    for sha in sorted(unpushed.keys() - matched):
        if view["default_ref"] and sha not in checked:
            info["unpushed_unchecked"].append(sha)
            info["unpushed_incomplete"] |= sha in recent
        else:
            info["unpushed" if sha in recent else "unpushed_stale"].append(sha)
    project["delivery_state"] = info
    return info


def annotate_bundle(bundle: dict, *, saved: dict | None = None, checked_at: str | None = None) -> dict:
    recheck = saved is not None or is_backfill(bundle)
    reference_at = (checked_at or bundle["generated_at"]) if recheck else bundle.get("window", {}).get("end")
    now = datetime.fromisoformat(reference_at) if reference_at else None
    for project in bundle["projects"]:
        info = annotate_project(project, now)
        if checked_at and not recheck and not bundle.get("preview"):
            info["unpushed_window_at"] = checked_at
    preserve_window_delivery(bundle, saved, checked_at=checked_at)
    return bundle


def is_backfill(bundle: dict) -> bool:
    window = bundle.get("window", {})
    if not bundle.get("generated_at") or not window.get("timezone"):
        return False
    observed = datetime.fromisoformat(bundle["generated_at"]).astimezone(ZoneInfo(window["timezone"]))
    return observed.date().isoformat() > window["date"]


def unpushed_count(info: dict) -> tuple[int | None, bool]:
    """The count and whether it is only a lower bound."""
    count = len(info.get("unpushed", []))
    incomplete = bool(info.get("unpushed_incomplete"))
    if info.get("has_remote") is None or (not count and incomplete):
        return None, False
    return count, incomplete


def _window_delivery(bundle: dict, info: dict) -> tuple[dict, str | None]:
    if check := info.get("unpushed_check"):
        at = info.get("unpushed_window_at")
        if not at:
            return {}, None
        if "commits" not in check:
            # Older backfills kept the old unpushed list but replaced the commit labels.
            local = set(info.get("unpushed", []))
            info = dict(info, commits={sha: "local" for sha in info.get("commits", {}) if sha in local},
                        merged_by_content=[], unpushed_stale=[], unpushed_unchecked=[], errors=[],
                        default_ref=None, default_basis=None)
        return info, at
    if info and not is_backfill(bundle):
        return info, info.get("unpushed_window_at") or bundle.get("generated_at")
    return {}, None


def preserve_window_delivery(bundle: dict, saved: dict | None = None, *, checked_at: str | None = None) -> None:
    """Keep a regenerated report's live query separate from the saved observation."""
    previous = {p["repo_id"]: p.get("delivery_state") or {} for p in (saved or {}).get("projects", [])}
    recheck = saved is not None or is_backfill(bundle)
    for project in bundle["projects"]:
        info = project.get("delivery_state") or {}
        existing = info.get("unpushed_check")
        if not info or (existing and "commits" in existing) or (not existing and not recheck):
            continue
        if existing:
            # Read old, mixed bundles without borrowing their live labels for the window.
            old, window_at = _window_delivery(bundle, info)
            count, incomplete = existing["count"], existing["incomplete"]
            if info.get("has_remote") is None or (not count and incomplete):
                count, incomplete = None, False
        else:
            old, window_at = _window_delivery(saved or {}, previous.get(project["repo_id"], {}))
            count, incomplete = unpushed_count(info)
        check = {"at": existing["at"] if existing else (checked_at or bundle["generated_at"]), "count": count,
                 "incomplete": incomplete, "commits": info.get("commits", {}),
                 "has_remote": info.get("has_remote"), "errors": info.get("errors", [])}
        project["delivery_state"] = dict(
            has_remote=old.get("has_remote"), default_ref=old.get("default_ref"),
            default_basis=old.get("default_basis"), commits=old.get("commits", {}),
            merged_by_content=old.get("merged_by_content", []), unpushed=old.get("unpushed", []),
            unpushed_stale=old.get("unpushed_stale", []), unpushed_unchecked=old.get("unpushed_unchecked", []),
            unpushed_incomplete=old.get("unpushed_incomplete", False), errors=old.get("errors", []),
            dirty_files=info.get("dirty_files", 0), dirty_trees=info.get("dirty_trees", 0),
            unpushed_check=check, unpushed_window_at=window_at,
        )


def unit_state(unit: dict, project: dict) -> str | None:
    info = project.get("delivery_state") or {}
    if unit["origin"] == "commit":
        return info.get("commits", {}).get(unit.get("sha"))
    pending = {f.get("path") for item in project["ongoing"] for f in item.get("files", [])}
    # An observed edit no longer pending was committed (its commit carries its
    # own state) or discarded; it is not "uncommitted" any more.
    return "uncommitted" if unit["path"] in pending else None


def record_state(record: dict, units_by_id: dict, project: dict) -> str | None:
    """The least-advanced state among the record's units: the honest summary."""
    return earliest(unit_state(units_by_id[u], project) for u in record.get("unit_ids", []) if u in units_by_id)
