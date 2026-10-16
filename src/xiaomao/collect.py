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
        "last_commit_at": snap.last_commit_at,
        "recent_subjects": list(snap.recent_subjects),
        "content_changes": snap.content_changes,
        "evidence_limitations": snap.evidence_limitations,
        "module_digest": {
            "new_modules": list(snap.module_digest.get("new_modules") or []),
            "gone_modules": list(snap.module_digest.get("gone_modules") or []),
            "changed_modules": list(snap.module_digest.get("changed_modules") or []),
        },
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


def scan_worktree(
    conn,
    cfg: AppConfig,
    project: ProjectSpec,
    wt: WorktreeSpec,
    *,
    expect_common_dir: str | None = None,
) -> dict:
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
        from xiaomao.inventory import metadata
        identity = metadata(str(path))
        if getattr(wt, "_expected_repo_id", identity["repo_id"]) != identity["repo_id"]:
            return {"worktree_id": wt.worktree_id, "status": "excluded", "inserted": False,
                    "reason": "repository_identity_changed"}
        expect_common_dir = expect_common_dir or getattr(wt, "_expected_common_dir", None)
        if expect_common_dir and os.path.realpath(identity["common_dir"]) != os.path.realpath(expect_common_dir):
            return {"worktree_id": wt.worktree_id, "status": "excluded", "inserted": False,
                    "reason": "not_same_repository"}
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

    if expect_common_dir is not None and os.path.realpath(snap.git_common_dir) != os.path.realpath(expect_common_dir):
        # The path now belongs to a different repository; record nothing.
        return {
            "worktree_id": wt.worktree_id,
            "status": "excluded",
            "inserted": False,
            "reason": "not_same_repository",
        }
    from xiaomao.ops import storage_over_budget
    if storage_over_budget(Path(cfg.home)):
        snap.content_changes = [{k: v for k, v in dict(item, patch="").items()
                                 if k not in {"units", "snapshot"}} for item in snap.content_changes]
        snap.evidence_limitations.append("storage_budget")
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
        _record_daily_snapshot(conn, cfg, project, wt, snap, observed_at, last["observation_id"])
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
    _record_daily_snapshot(conn, cfg, project, wt, snap, observed_at, observation_id)

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
            if not _authorized(project, w.path) and not _prefix_match(project, w)
        ],
    }


def _record_daily_snapshot(conn, cfg, project, wt, snap, at, observation_id):
    from xiaomao.activity import record_snapshot
    try:
        record_snapshot(conn, cfg, project, wt, snap, at, observation_id)
    except Exception as exc:
        # Preserve registered observation/handoff behavior, but surface the
        # separate daily-evidence failure in coverage, never as "no changes".
        insert_event(conn, kind="daily_evidence_error", project_id=project.project_id,
                     payload={"worktree_id": wt.worktree_id, "error": type(exc).__name__})


def _authorized(project: ProjectSpec, path: str) -> bool:
    target = os.path.realpath(path)
    for wt in project.worktrees:
        if os.path.realpath(wt.path) == target:
            return True
    return False


def _prefix_match(project: ProjectSpec, found) -> bool:
    if not project.branch_prefixes or found.bare or found.detached:
        return False
    branch = _short_branch(found.branch)
    return bool(branch) and any(branch.startswith(prefix) for prefix in project.branch_prefixes)


def _record_discovered(conn, project: ProjectSpec, wt: WorktreeSpec, snap: GitSnapshot) -> None:
    for discovered in snap.worktrees:
        record_discovered_worktree(
            conn,
            common_git_dir_id=snap.git_common_dir,
            path=discovered.path,
            head_oid=discovered.head,
            branch_ref=discovered.branch,
            detached=discovered.detached,
            authorized=_authorized(project, discovered.path) or _prefix_match(project, discovered),
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


PREFIX_WORKTREE_NOTE = "branch_prefix"


def _short_branch(ref: str | None) -> str | None:
    if not ref or not ref.startswith("refs/heads/"):
        return None
    return ref[len("refs/heads/"):]


def prefix_worktree_id(project_id: str, path: str) -> str:
    """Stable id for an auto-observed tree: same path, same id across scans."""
    import hashlib

    digest = hashlib.sha256(os.path.realpath(path).encode("utf-8")).hexdigest()[:10]
    return f"{project_id}~{digest}"


def prefix_candidates(project: ProjectSpec, snap: GitSnapshot) -> list[tuple[WorktreeSpec, str]]:
    """Other trees of the main tree's repo whose branch matches a prefix.

    Detached and bare trees never qualify: without a branch name there is no
    rule to match. Paths still go through the same exclusion checks as
    registered trees before anything is read.
    """
    if not project.branch_prefixes:
        return []
    out: list[tuple[WorktreeSpec, str]] = []
    for found in snap.worktrees:
        if _authorized(project, found.path) or not _prefix_match(project, found):
            continue
        branch = _short_branch(found.branch)
        spec = WorktreeSpec(
            worktree_id=prefix_worktree_id(project.project_id, found.path),
            path=found.path,
            scan=True,
            notes=PREFIX_WORKTREE_NOTE,
        )
        out.append((spec, branch))
    return out


def _main_snapshot(project: ProjectSpec) -> GitSnapshot | None:
    for wt in project.worktrees:
        if not wt.scan or worktree_exclusion_reason(wt.path):
            continue
        try:
            from xiaomao.inventory import metadata
            metadata(wt.path)
            return collect_snapshot(Path(wt.path).expanduser())
        except Exception:
            continue
    return None


def _mark_gone_prefix_trees(conn, project: ProjectSpec, live_ids: set[str]) -> list[dict]:
    """A prefix tree missing from `git worktree list` is gone, not an error."""
    rows = conn.execute(
        "SELECT worktree_id, canonical_path FROM worktrees"
        " WHERE project_id = ? AND notes = ? AND scan_enabled = 1",
        (project.project_id, PREFIX_WORKTREE_NOTE),
    ).fetchall()
    gone = []
    for row in rows:
        if row["worktree_id"] in live_ids:
            continue
        conn.execute(
            "UPDATE worktrees SET scan_enabled = 0 WHERE worktree_id = ?",
            (row["worktree_id"],),
        )
        insert_event(
            conn,
            kind="worktree_gone",
            project_id=project.project_id,
            payload={"worktree_id": row["worktree_id"], "path": row["canonical_path"]},
        )
        gone.append({"worktree_id": row["worktree_id"], "status": "gone", "inserted": False})
    return gone


def scan_prefix_worktrees(conn, cfg: AppConfig, project: ProjectSpec) -> list[dict]:
    """Observe branch-prefix trees. Never affects the project's scan outcome."""
    if not project.branch_prefixes:
        return []
    snap = _main_snapshot(project)
    if snap is None:
        return []
    candidates = prefix_candidates(project, snap)
    results: list[dict] = []
    live: set[str] = set()
    for spec, branch in candidates:
        # Prunable entries stay in `git worktree list` after the directory is
        # deleted; treat them as gone instead of collecting an error.
        if not Path(spec.path).is_dir() or worktree_exclusion_reason(spec.path):
            continue
        live.add(spec.worktree_id)
        upsert_worktree(
            conn,
            worktree_id=spec.worktree_id,
            project_id=project.project_id,
            canonical_path=os.path.realpath(spec.path),
            common_git_dir_id=snap.git_common_dir,
            scan_enabled=True,
            notes=PREFIX_WORKTREE_NOTE,
        )
        row = scan_worktree(conn, cfg, project, spec, expect_common_dir=snap.git_common_dir)
        row["branch"] = branch
        row["source"] = PREFIX_WORKTREE_NOTE
        results.append(row)
    results.extend(_mark_gone_prefix_trees(conn, project, live))
    return results


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
        from xiaomao.activity import record_checks
        record_checks(conn, project, [{"status": "paused"}], started)
        _record_scan_run(
            conn,
            project_id=project_id,
            started_at=started,
            outcome="skip",
            results=[],
            last_safe_error="scan_paused",
        )
        return [{"project": project_id, "status": "paused", "inserted": False}]
    import copy
    registered = copy.copy(project)
    registered.worktrees = getattr(project, "_registered_worktrees", project.worktrees)
    scan_scope_sha256 = scope_identity(registered)
    results: list[dict] = []
    for wt in registered.worktrees:
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
    # Supplementary observation. Recorded separately so the registered scope,
    # its scan_run outcome and handoff coverage stay exactly as before.
    extra = scan_prefix_worktrees(conn, cfg, project) if outcome != "skip" else []
    if outcome != "skip":
        registered_ids = {w.worktree_id for w in registered.worktrees}
        for wt in project.worktrees:
            if wt.worktree_id not in registered_ids and wt.scan:
                extra.append(scan_worktree(conn, cfg, project, wt,
                                           expect_common_dir=getattr(wt, "_expected_common_dir", None)))
    # Failed discovery is coverage evidence, never proof of disappearance.
    extra.extend(getattr(project, "_inventory_failures", []))
    if extra:
        insert_event(
            conn,
            kind="prefix_scan",
            project_id=project_id,
            payload={
                "prefixes": list(project.branch_prefixes),
                "trees": [
                    {k: row.get(k) for k in ("worktree_id", "branch", "status", "observation_id")}
                    for row in extra
                ],
            },
        )
    from xiaomao.activity import record_checks
    record_checks(conn, project, results + extra, utc_now())
    return results + extra


def scan_authorized(conn, cfg: AppConfig, project_id: str | None = None) -> dict[str, list[dict]]:
    """Scan one project, or every project in the allow-list."""
    from xiaomao.inventory import effective_config
    cfg = effective_config(cfg)
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
