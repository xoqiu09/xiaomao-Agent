from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from xiaomao.config import AppConfig, ProjectSpec, WorktreeSpec
from xiaomao.git_readonly import GitSnapshot, collect_snapshot, snapshot_fingerprint
from xiaomao.policy import path_is_denied
from xiaomao.store import (
    evidence_for_observation,
    insert_evidence,
    insert_observation,
    latest_observation,
    record_discovered_worktree,
    utc_now,
    upsert_project,
    upsert_worktree,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def register_project(conn, cfg: AppConfig, project: ProjectSpec) -> None:
    upsert_project(conn, project.project_id, project.display_name, project.approved_root, cfg.policy_version)
    for wt in project.worktrees:
        upsert_worktree(
            conn,
            worktree_id=wt.worktree_id,
            project_id=project.project_id,
            canonical_path=str(Path(wt.path).expanduser()),
            common_git_dir_id=None,
            scan_enabled=wt.scan,
            notes=wt.notes,
        )


def _classify_entry(path: str) -> tuple[str, str]:
    if path_is_denied(path):
        return "denied_path", "redacted"
    return "path", "clean"


def _facts(snap: GitSnapshot, worktree_id: str, extra: dict | None = None) -> dict:
    staged = [e.path for e in snap.staged]
    unstaged = [e.path for e in snap.unstaged]
    untracked = [e.path for e in snap.untracked]
    denied = [p for p in staged + unstaged + untracked if path_is_denied(p)]
    payload = {
        "worktree_id": worktree_id,
        "toplevel": snap.toplevel,
        "head_oid": snap.head_oid,
        "branch_ref": snap.branch_ref,
        "is_unborn": snap.is_unborn,
        "is_detached": snap.is_detached,
        "collection_status": snap.collection_status,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "denied_paths": denied,
        "discovered_worktrees": [
            {
                "path": w.path,
                "head": w.head,
                "branch": w.branch,
                "detached": w.detached,
            }
            for w in snap.worktrees
        ],
        "test_status": "unknown",
        "deploy_status": "unknown",
    }
    if extra:
        payload.update(extra)
    return payload


def scan_worktree(conn, cfg: AppConfig, project: ProjectSpec, wt: WorktreeSpec) -> dict:
    path = Path(wt.path).expanduser()
    observed_at = utc_now()
    try:
        snap = collect_snapshot(path)
    except Exception as exc:
        observation_id = _new_id("obs")
        fingerprint = f"error:{type(exc).__name__}:{path}"
        inserted = insert_observation(
            conn,
            {
                "observation_id": observation_id,
                "project_id": project.project_id,
                "worktree_id": wt.worktree_id,
                "observed_at_utc": observed_at,
                "local_timezone": cfg.timezone,
                "head_oid": None,
                "branch_ref": None,
                "dirty_snapshot_id": None,
                "source_type": "git",
                "collection_status": "error",
                "fingerprint": fingerprint,
                "is_unborn": 0,
                "is_detached": 0,
                "staged_count": 0,
                "unstaged_count": 0,
                "untracked_count": 0,
                "facts_json": json.dumps(
                    {
                        "error": type(exc).__name__,
                        "message": str(exc)[:400],
                        "test_status": "unknown",
                        "deploy_status": "unknown",
                    },
                    ensure_ascii=False,
                ),
            },
        )
        return {
            "worktree_id": wt.worktree_id,
            "status": "error",
            "inserted": inserted,
            "error": str(exc)[:400],
            "observation_id": observation_id if inserted else None,
        }

    fingerprint = snapshot_fingerprint(snap)
    last = latest_observation(conn, wt.worktree_id)
    if last and last["fingerprint"] == fingerprint and last["collection_status"] == snap.collection_status:
        # Still record discovered worktrees so new trees surface as candidates.
        _record_discovered(conn, project, wt, snap)
        upsert_worktree(
            conn,
            worktree_id=wt.worktree_id,
            project_id=project.project_id,
            canonical_path=snap.toplevel,
            common_git_dir_id=snap.git_common_dir,
            scan_enabled=wt.scan,
            notes=wt.notes,
        )
        return {
            "worktree_id": wt.worktree_id,
            "status": "unchanged",
            "inserted": False,
            "observation_id": last["observation_id"],
            "fingerprint": fingerprint,
            "head_oid": snap.head_oid,
            "branch_ref": snap.branch_ref,
        }

    observation_id = _new_id("obs")
    facts = _facts(snap, wt.worktree_id)
    dirty_id = fingerprint if (snap.staged or snap.unstaged or snap.untracked) else None
    inserted = insert_observation(
        conn,
        {
            "observation_id": observation_id,
            "project_id": project.project_id,
            "worktree_id": wt.worktree_id,
            "observed_at_utc": observed_at,
            "local_timezone": cfg.timezone,
            "head_oid": snap.head_oid,
            "branch_ref": snap.branch_ref,
            "dirty_snapshot_id": dirty_id,
            "source_type": "git",
            "collection_status": snap.collection_status,
            "fingerprint": fingerprint,
            "is_unborn": 1 if snap.is_unborn else 0,
            "is_detached": 1 if snap.is_detached else 0,
            "staged_count": len(snap.staged),
            "unstaged_count": len(snap.unstaged),
            "untracked_count": len(snap.untracked),
            "facts_json": json.dumps(facts, ensure_ascii=False),
        },
    )
    if not inserted:
        _record_discovered(conn, project, wt, snap)
        last = latest_observation(conn, wt.worktree_id)
        return {
            "worktree_id": wt.worktree_id,
            "status": "unchanged",
            "inserted": False,
            "observation_id": last["observation_id"] if last else None,
            "fingerprint": fingerprint,
            "head_oid": snap.head_oid,
            "branch_ref": snap.branch_ref,
        }

    upsert_worktree(
        conn,
        worktree_id=wt.worktree_id,
        project_id=project.project_id,
        canonical_path=snap.toplevel,
        common_git_dir_id=snap.git_common_dir,
        scan_enabled=wt.scan,
        notes=wt.notes,
    )
    _record_discovered(conn, project, wt, snap)
    _insert_status_evidence(conn, observation_id, observed_at, snap)

    return {
        "worktree_id": wt.worktree_id,
        "status": snap.collection_status,
        "inserted": True,
        "observation_id": observation_id,
        "fingerprint": fingerprint,
        "head_oid": snap.head_oid,
        "branch_ref": snap.branch_ref,
        "staged": len(snap.staged),
        "unstaged": len(snap.unstaged),
        "untracked": len(snap.untracked),
        "discovered_unauthorized": [
            w.path
            for w in snap.worktrees
            if not _authorized(project, w.path)
        ],
    }


def _authorized(project: ProjectSpec, path: str) -> bool:
    target = os.path.realpath(path)
    for wt in project.worktrees:
        if os.path.realpath(wt.path) == target:
            return True
    return False


def _record_discovered(conn, project: ProjectSpec, wt: WorktreeSpec, snap: GitSnapshot) -> None:
    for discovered in snap.worktrees:
        record_discovered_worktree(
            conn,
            common_git_dir_id=snap.git_common_dir,
            path=discovered.path,
            head_oid=discovered.head,
            branch_ref=discovered.branch,
            detached=discovered.detached,
            authorized=_authorized(project, discovered.path),
        )


def _insert_status_evidence(conn, observation_id: str, observed_at: str, snap: GitSnapshot) -> None:
    def add(kind: str, path: str, xy: str) -> None:
        kind_out, redaction = _classify_entry(path)
        locator = path if redaction == "clean" else f"{path} (name retained, content denied)"
        insert_evidence(
            conn,
            {
                "evidence_id": _new_id("ev"),
                "observation_id": observation_id,
                "source_locator": f"git-status:{xy}:{locator}",
                "source_hash": None,
                "safe_excerpt": None,
                "evidence_kind": kind if kind_out != "denied_path" else "denied_path",
                "observed_at": observed_at,
                "availability": "observed",
                "redaction_status": redaction,
            },
        )

    for e in snap.staged:
        add("staged", e.path, f"{e.staged} ")
    for e in snap.unstaged:
        add("unstaged", e.path, f" {e.unstaged}")
    for e in snap.untracked:
        add("untracked", e.path, "??")


def scan_project(conn, cfg: AppConfig, project_id: str) -> list[dict]:
    project = cfg.project(project_id)
    register_project(conn, cfg, project)
    results = []
    for wt in project.worktrees:
        if not wt.scan:
            continue
        results.append(scan_worktree(conn, cfg, project, wt))
    return results


def observation_bundle(conn, observation_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM observations WHERE observation_id = ?",
        (observation_id,),
    ).fetchone()
    if not row:
        raise KeyError(observation_id)
    facts = json.loads(row["facts_json"] or "{}")
    return {
        "observation": dict(row),
        "facts": facts,
        "evidence": [dict(e) for e in evidence_for_observation(conn, observation_id)],
    }
