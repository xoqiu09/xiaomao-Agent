"""Personal repository metadata discovery; business contents are never read."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from xiaomao.config import ProjectSpec, WorktreeSpec
from xiaomao.git_readonly import parse_worktree_porcelain, run_git
from xiaomao.scope import path_exclusion_reason, project_exclusion_reason, worktree_exclusion_reason

DAILY_TREE_NOTE = "daily_worktree"
IGNORED = {".git", ".venv", "node_modules", "__pycache__", ".cache", "_worklogs"}


def excluded(path: str) -> str | None:
    reason = worktree_exclusion_reason(path)
    if reason:
        return reason
    normalized = str(Path(path).expanduser().resolve())
    if "/Library/Application Support/Xiaomao/release/" in normalized + "/":
        return "runtime_release_excluded"
    return None


def remote_identity(raw: str) -> tuple[str | None, str | None]:
    # Never retain or return credentials embedded in a remote URL.
    match = re.fullmatch(r"(?:git@)?(github(?:-personal|-work)?(?:\.com)?):([^/]+)/(.+)", raw.strip())
    if match:
        owner, name = match.group(2), match.group(3)
    else:
        value = urlsplit(raw.strip())
        if value.hostname not in {"github.com", "ssh.github.com", "github-personal", "github-work"}:
            return None, None
        parts = value.path.strip("/").split("/")
        if len(parts) != 2:
            return None, None
        owner, name = parts
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return None, None
    return f"github:{owner.lower()}/{name.removesuffix('.git').lower()}", owner.lower()


def metadata(path: str) -> dict:
    if reason := excluded(path):
        raise ValueError(reason)
    root = Path(path).expanduser().resolve()
    common = run_git(root, "rev-parse", "--git-common-dir").stdout.strip()
    common = str((root / common).resolve())
    raw = run_git(root, "config", "--get", "remote.origin.url", check=False).stdout.strip()
    repo_id, owner = remote_identity(raw)
    if owner and owner.lower() == "theaicommunity":
        raise ValueError("company_repository_excluded")
    repo_id = repo_id or "local:" + hashlib.sha256(common.encode()).hexdigest()[:24]
    return {"path": str(root), "repo_id": repo_id, "owner": owner, "common_dir": common}


def tree_id(project_id: str, path: str) -> str:
    return project_id + "~" + hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:10]


def scope_key(project) -> str:
    trees = getattr(project, "_registered_worktrees", project.worktrees)
    payload = {
        "project": project.project_id, "root": str(Path(project.approved_root).resolve()),
        "trees": [(w.worktree_id, str(Path(w.path).resolve()), w.scan) for w in trees],
        "policy": project.worktree_policy, "prefixes": project.branch_prefixes,
        "discovery": getattr(project, "_discovery_scope", []),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def discover(cfg, roots: list[str] | None = None, *, errors: list[str] | None = None) -> list[dict]:
    errors = errors if errors is not None else []
    registered = {str(Path(w.path).resolve()) for p in cfg.projects
                  if not project_exclusion_reason(p) for w in p.worktrees}
    rows, seen = [], set()
    owners = {s.lower() for s in cfg.personal_owners}

    def walk(root: Path, depth: int):
        if len(seen) >= 5000:
            if "inventory_directory_limit" not in errors:
                errors.append("inventory_directory_limit")
            return
        if root.is_symlink() or path_exclusion_reason(str(root)) or excluded(str(root)):
            return
        if depth > 3:
            errors.append(f"{root}: inventory_depth_limit")
            return
        resolved = str(root.resolve())
        if resolved in seen:
            return
        seen.add(resolved)
        try:
            if (root / ".git").exists():
                try:
                    item = metadata(resolved)
                except Exception as exc:
                    if str(exc) != "company_repository_excluded":
                        errors.append(f"{resolved}: repository_metadata_unavailable")
                    return
                item["status"] = ("registered" if resolved in registered else
                                  "personal" if item["owner"] in owners else "candidate")
                rows.append(item)
                return
            for child in sorted(root.iterdir()):
                if child.name.startswith(".") or child.name in IGNORED:
                    continue
                if child.is_dir():
                    walk(child, depth + 1)
        except OSError:
            errors.append(f"{resolved}: directory_unavailable")
            return

    for value in roots if roots is not None else cfg.personal_roots:
        root = Path(value).expanduser()
        if root.is_absolute():
            walk(root, 0)
    return rows


def effective_config(cfg):
    if getattr(cfg, "_inventory_ready", False):
        return cfg
    from xiaomao.report_access import scope_set
    result = copy.deepcopy(cfg)
    result._daily_scope = scope_set(cfg)
    result._inventory_ready = True
    result._inventory_candidates = []
    result._inventory_errors = []
    by_repo = {}
    all_registered = {str(Path(w.path).resolve()) for p in result.projects for w in p.worktrees}
    for project in result.projects:
        project._registered_worktrees = list(project.worktrees)
        project._inventory_failures = []
        project._inventory_incomplete = False
        project._discovery_scope = [cfg.personal_roots, cfg.personal_owners] if cfg.personal_roots else []
        if project_exclusion_reason(project):
            continue
        try:
            info = metadata(project.approved_root)
            project._repo_id = info["repo_id"]
            by_repo.setdefault(info["repo_id"], project)
        except Exception:
            continue  # registered scan will report the actual error
    for row in discover(cfg, errors=result._inventory_errors):
        if row["status"] == "candidate":
            result._inventory_candidates.append({"path": row["path"], "status": "candidate"})
            continue
        if row["path"] in all_registered:
            continue
        project = by_repo.get(row["repo_id"])
        if project is None:
            pid = "personal-" + hashlib.sha256(row["repo_id"].encode()).hexdigest()[:12]
            project = ProjectSpec(pid, row["repo_id"].split("/")[-1], row["path"],
                                  [WorktreeSpec(pid + "-main", row["path"])], worktree_policy="all")
            project._registered_worktrees = list(project.worktrees)
            project._discovery_scope = [cfg.personal_roots, cfg.personal_owners]
            project._repo_id = row["repo_id"]
            project.worktrees[0]._expected_common_dir = row["common_dir"]
            project.worktrees[0]._expected_repo_id = row["repo_id"]
            result.projects.append(project)
            by_repo[row["repo_id"]] = project
        else:
            tree = WorktreeSpec(tree_id(project.project_id, row["path"]), row["path"], notes=DAILY_TREE_NOTE)
            tree._expected_common_dir = row["common_dir"]
            tree._expected_repo_id = row["repo_id"]
            project.worktrees.append(tree)
        all_registered.add(row["path"])
    for project in result.projects:
        if project.worktree_policy != "all" or project_exclusion_reason(project):
            continue
        project._inventory_failures = getattr(project, "_inventory_failures", [])
        project._inventory_incomplete = getattr(project, "_inventory_incomplete", False)
        known = {str(Path(w.path).resolve()) for w in project.worktrees}
        for source in list(project.worktrees):
            if not source.scan or excluded(source.path):
                continue
            try:
                source_meta = metadata(source.path)
                listing = run_git(Path(source.path), "worktree", "list", "--porcelain").stdout
                for found in parse_worktree_porcelain(listing):
                    resolved = str(Path(found.path).resolve())
                    if found.bare or resolved in known or not Path(resolved).is_dir():
                        continue
                    reason = excluded(resolved)
                    if reason:
                        if reason.startswith(("git_metadata_timeout", "invalid_git_metadata", "invalid_project_path")):
                            result._inventory_errors.append(f"{project.project_id}: {resolved}: {reason}")
                            project._inventory_failures.append({"worktree_id": tree_id(project.project_id, resolved),
                                "status": "error", "inserted": False, "error": reason})
                            known.add(resolved)  # one failure per tree, even through multiple copies
                        continue
                    try:
                        target = metadata(resolved)
                    except Exception:
                        result._inventory_errors.append(
                            f"{project.project_id}: {resolved}: worktree_metadata_unavailable")
                        project._inventory_failures.append({"worktree_id": tree_id(project.project_id, resolved),
                            "status": "error", "inserted": False, "error": "worktree_metadata_unavailable"})
                        known.add(resolved)
                        continue
                    if target["common_dir"] != source_meta["common_dir"]:
                        continue
                    known.add(resolved)
                    tree = WorktreeSpec(tree_id(project.project_id, resolved), resolved, notes=DAILY_TREE_NOTE)
                    tree._expected_common_dir = target["common_dir"]
                    tree._expected_repo_id = target["repo_id"]
                    project.worktrees.append(tree)
            except Exception:
                project._inventory_incomplete = True
                result._inventory_errors.append(f"{project.project_id}: worktree_inventory_unavailable")
    verified = {str(Path(w.path).resolve()) for p in result.projects
                if not project_exclusion_reason(p) for w in p.worktrees}
    result._inventory_candidates = [row for row in result._inventory_candidates if row["path"] not in verified]
    return result
