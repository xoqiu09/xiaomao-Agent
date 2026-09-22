from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from xiaomao.config import AppConfig, ProjectSpec, WorktreeSpec
from xiaomao.git_readonly import GitSnapshot, collect_snapshot, snapshot_fingerprint
from xiaomao.ops import GAP_WARN_AFTER_S, gap_seconds_since
from xiaomao.policy import path_is_denied
from xiaomao.scope import project_exclusion_reason, worktree_exclusion_reason
from xiaomao.store import (
    evidence_for_observation,
    insert_event,
    insert_evidence,
    insert_observation,
    insert_scan_run,
    last_scan_success,
    latest_observation,
    record_discovered_worktree,
    scan_paused,
    utc_now,
    upsert_project,
    upsert_worktree,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _matches_observation(row, fingerprint: str, collection_status: str) -> bool:
    if not row or row["collection_status"] != collection_status:
        return False
    # The existing schema makes each stored fingerprint unique for a tree.
    # A later occurrence of a historical snapshot gets an occurrence suffix;
    # compare its original fingerprint so the next identical scan is a no-op.
    return row["fingerprint"] in {
        fingerprint,
        f"{fingerprint}:reobserved:{row['observation_id']}",
    }


def _insert_observation_occurrence(conn, row: dict) -> tuple[bool, str]:
    fingerprint = row["fingerprint"]
    latest = latest_observation(conn, row["worktree_id"])
    if _matches_observation(latest, fingerprint, row["collection_status"]):
        return False, latest["observation_id"]
    if insert_observation(conn, row):
        return True, row["observation_id"]

    # A concurrent identical observation can be reused. A historical match
    # cannot: it would keep the intervening state as the latest observation.
    latest = latest_observation(conn, row["worktree_id"])
    if _matches_observation(latest, fingerprint, row["collection_status"]):
        return False, latest["observation_id"]
    collision = conn.execute(
        "SELECT 1 FROM observations WHERE worktree_id = ? AND fingerprint = ?",
        (row["worktree_id"], fingerprint),
    ).fetchone()
    if not collision:
        raise RuntimeError("无法保存本次观察；未将写入失败标记为 unchanged")
    row["fingerprint"] = f"{fingerprint}:reobserved:{row['observation_id']}"
    if not insert_observation(conn, row):
        raise RuntimeError("无法保存历史状态的本次重现；未复用无关观察")
    return True, row["observation_id"]


def register_project(conn, cfg: AppConfig, project: ProjectSpec) -> None:
    exclusion = project_exclusion_reason(project)
    upsert_project(conn, project.project_id, project.display_name, project.approved_root, cfg.policy_version)
    for wt in project.worktrees:
        upsert_worktree(
            conn,
            worktree_id=wt.worktree_id,
            project_id=project.project_id,
            canonical_path=str(Path(wt.path).expanduser()),
            common_git_dir_id=None,
            scan_enabled=wt.scan and exclusion is None,
            notes="；".join(part for part in (wt.notes, exclusion) if part),
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
    exclusion = project_exclusion_reason(project) or worktree_exclusion_reason(wt.path)
    if exclusion:
        return {
            "worktree_id": wt.worktree_id,
            "status": "excluded",
            "inserted": False,
            "reason": exclusion,
        }
    path = Path(wt.path).expanduser()
    observed_at = utc_now()
    try:
        snap = collect_snapshot(path)
    except Exception as exc:
        observation_id = _new_id("obs")
        fingerprint = f"error:{type(exc).__name__}:{path}"
        inserted, observation_id = _insert_observation_occurrence(
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
            "observation_id": observation_id,
        }

    fingerprint = snapshot_fingerprint(snap)
    last = latest_observation(conn, wt.worktree_id)
    if _matches_observation(last, fingerprint, snap.collection_status):
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
    inserted, observation_id = _insert_observation_occurrence(
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
        return {
            "worktree_id": wt.worktree_id,
            "status": "unchanged",
            "inserted": False,
            "observation_id": observation_id,
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


def _record_scan_run(
    conn,
    *,
    project_id: str,
    started_at: str,
    outcome: str,
    results: list[dict],
    last_safe_error: str | None = None,
    scope_sha256: str | None = None,
) -> None:
    run_id = _new_id("run")
    finished = utc_now()
    prev = last_scan_success(conn, project_id)
    gap = gap_seconds_since(prev["finished_at"] if prev else None, started_at)
    last_obs = None
    for row in reversed(results):
        if row.get("observation_id"):
            last_obs = row["observation_id"]
            break
    insert_scan_run(
        conn,
        {
            "run_id": run_id,
            "project_id": project_id,
            "started_at": started_at,
            "finished_at": finished,
            "outcome": outcome,
            "last_safe_error": last_safe_error,
            "inserted": sum(1 for r in results if r.get("inserted")),
            "unchanged": sum(1 for r in results if r.get("status") == "unchanged"),
            "gap_since_last_success_s": gap,
            "last_observation_id": last_obs,
        },
    )
    insert_event(
        conn,
        kind=f"scan_{outcome}",
        project_id=project_id,
        payload={
            "run_id": run_id,
            "scope_sha256": scope_sha256,
            "observation_ids": [
                {"worktree_id": row["worktree_id"], "observation_id": row["observation_id"]}
                for row in results
                if row.get("observation_id") and row.get("worktree_id")
            ],
            "inserted": sum(1 for r in results if r.get("inserted")),
            "unchanged": sum(1 for r in results if r.get("status") == "unchanged"),
            "gap_since_last_success_s": gap,
            "last_safe_error": last_safe_error,
        },
    )
    if gap is not None and gap > GAP_WARN_AFTER_S:
        insert_event(
            conn,
            kind="collection_gap",
            project_id=project_id,
            payload={
                "gap_s": gap,
                "note": "距上次成功扫描偏长，可能含睡眠/关机/登出或其他未采集窗口。缺口内未保存到磁盘的编辑不可见，不编造。",
            },
        )


def scan_project(conn, cfg: AppConfig, project_id: str) -> list[dict]:
    from xiaomao.handoff_view import scope_identity

    started = utc_now()
    project = cfg.project(project_id)
    register_project(conn, cfg, project)
    exclusion = project_exclusion_reason(project)
    if exclusion:
        _record_scan_run(
            conn,
            project_id=project_id,
            started_at=started,
            outcome="skip",
            results=[],
            last_safe_error=exclusion,
        )
        return [{"project": project_id, "status": "excluded", "inserted": False, "reason": exclusion}]
    if scan_paused(conn):
        _record_scan_run(
            conn,
            project_id=project_id,
            started_at=started,
            outcome="skip",
            results=[],
            last_safe_error="scan_paused",
        )
        return [{"project": project_id, "status": "paused", "inserted": False}]
    scan_scope_sha256 = scope_identity(project)
    results: list[dict] = []
    for wt in project.worktrees:
        if not wt.scan:
            continue
        results.append(scan_worktree(conn, cfg, project, wt))
    errors = [r.get("error") for r in results if r.get("status") == "error"]
    exclusions = [r.get("reason") for r in results if r.get("status") == "excluded"]
    if not results:
        outcome = "skip"
        errors = ["no_enabled_worktrees"]
    elif errors:
        outcome = "error"
        errors.extend(exclusions)
    elif exclusions:
        # A link can change after the project check. The per-tree guard must
        # not turn that refusal into a successful scan heartbeat.
        outcome = "skip"
        errors.extend(exclusions)
    else:
        outcome = "success"
    _record_scan_run(
        conn,
        project_id=project_id,
        started_at=started,
        outcome=outcome,
        results=results,
        last_safe_error="; ".join(str(e) for e in errors if e)[:400] or None,
        scope_sha256=scan_scope_sha256 if outcome in {"success", "error"} else None,
    )
    return results


def scan_authorized(conn, cfg: AppConfig, project_id: str | None = None) -> dict[str, list[dict]]:
    """Scan one project, or every project in the allow-list."""
    if project_id:
        return {project_id: scan_project(conn, cfg, project_id)}
    return {project.project_id: scan_project(conn, cfg, project.project_id) for project in cfg.projects}


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
