"""Where the code is: uncommitted / local only / pushed / merged.

Read-only: uses only the local `refs/remotes/*` (no fetch), through
`git_readonly.run_git`. Local remote refs update only on push or fetch, so a
commit pushed from another machine may still read as local; that is a known
stage-1 limit, recorded in the evidence, not hidden.
"""
from __future__ import annotations

from pathlib import Path

from xiaomao.git_readonly import GitReadError, run_git

ORDER = ("uncommitted", "local", "pushed", "merged")
LABELS = {"uncommitted": "🟡 未提交", "local": "🟠 仅本机", "pushed": "🔵 已推送", "merged": "🟢 已合并"}
MAX_UNPUSHED = 1000


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


def unpushed_commits(path: Path, view: dict) -> list[str] | None:
    """Commits on this tree's HEAD that no remote branch contains."""
    if not view["has_remote"]:
        return None
    proc = run_git(path, "rev-list", f"--max-count={MAX_UNPUSHED}", "HEAD", "--not", "--remotes",
                   check=False)
    return proc.stdout.split() if proc.returncode == 0 else None


def earliest(states) -> str | None:
    known = [s for s in states if s in ORDER]
    return min(known, key=ORDER.index) if known else None


def annotate_project(project: dict) -> dict:
    """Attach current delivery facts to one daily bundle project (read-only)."""
    live = [t for t in project["trees"] if t.get("path") and not t.get("gone") and Path(t["path"]).is_dir()]
    info = {"has_remote": None, "default_ref": None, "default_basis": None,
            "commits": {}, "unpushed": [], "dirty_files": 0, "dirty_trees": 0, "errors": []}
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
    for commit in project["commits"]:
        tree = next((paths[t] for t in commit.get("tree_ids", []) if t in paths), Path(live[0]["path"]))
        try:
            state = commit_state(tree, commit["sha"], view)
        except GitReadError:
            state = None
        if state is None:
            info["errors"].append(f"commit_state_unavailable:{commit['sha'][:12]}")
        info["commits"][commit["sha"]] = state
    unpushed = set()
    for tree in live:
        if not tree.get("head"):
            continue
        try:
            found = unpushed_commits(Path(tree["path"]), view)
        except GitReadError:
            found = None
        if found is None and view["has_remote"]:
            info["errors"].append(f"unpushed_unavailable:{tree['tree_id']}")
        unpushed.update(found or [])
    info["unpushed"] = sorted(unpushed)
    project["delivery_state"] = info
    return info


def annotate_bundle(bundle: dict) -> dict:
    for project in bundle["projects"]:
        annotate_project(project)
    return bundle


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
