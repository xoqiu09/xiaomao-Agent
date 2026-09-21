"""Pilot operations: pause flags, evidence snapshots, scheduled daily policy."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from xiaomao.config import AppConfig
from xiaomao.paths import STORAGE_BUDGET_BYTES, layout
from xiaomao.policy import dir_size_bytes
from xiaomao.store import (
    get_meta,
    infer_paused,
    insert_event,
    insert_summary,
    job_get,
    latest_summary_for_range,
    scan_paused,
    set_meta,
    upsert_job,
    utc_now,
)

DAILY_HOUR = 21
DAILY_MINUTE = 30
SCAN_INTERVAL_S = 300
GAP_WARN_AFTER_S = SCAN_INTERVAL_S * 2 + 60  # two missed 5-minute ticks
PILOT_DAYS = 7
PILOT_MID_DAYS = 3


def _tz(cfg: AppConfig):
    try:
        return ZoneInfo(cfg.timezone)
    except Exception:
        return ZoneInfo("Asia/Taipei")


def evidence_snapshot_key(facts: dict[str, Any]) -> str:
    """Stable key for date/worktree/evidence dedup. Program facts only."""
    trees = []
    for wt in facts.get("worktrees") or []:
        trees.append(
            {
                "worktree_id": wt.get("worktree_id"),
                "observation_id": wt.get("observation_id"),
                "head_oid": wt.get("head_oid"),
                "staged_count": wt.get("staged_count"),
                "unstaged_count": wt.get("unstaged_count"),
                "untracked_count": wt.get("untracked_count"),
                "scan_enabled": wt.get("scan_enabled"),
            }
        )
    blob = json.dumps(
        {"project_id": facts.get("project_id"), "worktrees": trees},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def storage_over_budget(home: Path) -> bool:
    return dir_size_bytes(home) > STORAGE_BUDGET_BYTES


def pause_infer(conn) -> None:
    set_meta(conn, "infer_paused", "1")
    insert_event(conn, kind="infer_paused")


def resume_infer(conn) -> None:
    set_meta(conn, "infer_paused", "0")
    insert_event(conn, kind="infer_resumed")


def pause_scan_flag(conn) -> None:
    set_meta(conn, "scan_paused", "1")
    insert_event(conn, kind="scan_paused")


def resume_scan_flag(conn) -> None:
    set_meta(conn, "scan_paused", "0")
    insert_event(conn, kind="scan_resumed")


def mark_pilot_running(conn, *, started_at: str | None = None) -> dict[str, str]:
    started = started_at or utc_now()
    set_meta(conn, "pilot_status", "PILOT_RUNNING")
    existing = get_meta(conn, "pilot_started_at")
    if not existing:
        set_meta(conn, "pilot_started_at", started)
        started_use = started
    else:
        started_use = existing
    start_dt = datetime.fromisoformat(started_use.replace("Z", "+00:00"))
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    mid = (start_dt + timedelta(days=PILOT_MID_DAYS)).date().isoformat()
    end = (start_dt + timedelta(days=PILOT_DAYS)).date().isoformat()
    set_meta(conn, "pilot_mid_date", mid)
    set_meta(conn, "pilot_end_date", end)
    insert_event(
        conn,
        kind="pilot_started",
        payload={"started_at": started_use, "mid_date": mid, "end_date": end},
    )
    return {"started_at": started_use, "mid_date": mid, "end_date": end, "status": "PILOT_RUNNING"}


def pilot_info(conn) -> dict[str, str | None]:
    return {
        "status": get_meta(conn, "pilot_status"),
        "started_at": get_meta(conn, "pilot_started_at"),
        "mid_date": get_meta(conn, "pilot_mid_date"),
        "end_date": get_meta(conn, "pilot_end_date"),
        "infer_paused": "1" if infer_paused(conn) else "0",
        "scan_paused": "1" if scan_paused(conn) else "0",
    }


def gap_seconds_since(last_finished_at: str | None, now: str | None = None) -> int | None:
    if not last_finished_at:
        return None
    now_s = now or utc_now()
    try:
        a = datetime.fromisoformat(last_finished_at.replace("Z", "+00:00"))
        b = datetime.fromisoformat(now_s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((b - a).total_seconds()))


def should_call_depth_model(
    conn,
    facts: dict[str, Any],
    *,
    with_model: bool,
    scheduled: bool,
) -> tuple[bool, str]:
    """Decide whether to load qwen3-coder:30b. Scan path never reaches here."""
    if not with_model and not scheduled:
        return False, "rules_only"
    if infer_paused(conn):
        return False, "infer_paused"
    snapshot = evidence_snapshot_key(facts)
    pid = facts.get("project_id") or ""
    prior = latest_summary_for_range(conn, pid, snapshot)
    if prior is not None and prior["validation_status"] == "pass":
        return False, f"dedup:{snapshot}"
    if scheduled:
        dirty = False
        for wt in facts.get("worktrees") or []:
            if not wt.get("scan_enabled"):
                continue
            counts = (
                int(wt.get("staged_count") or 0)
                + int(wt.get("unstaged_count") or 0)
                + int(wt.get("untracked_count") or 0)
            )
            if counts:
                dirty = True
                break
        if not dirty:
            return False, "no_new_evidence"
    return True, snapshot


def record_summary_row(
    conn,
    *,
    facts: dict[str, Any],
    result: dict[str, Any],
    model: str,
) -> str:
    sid = f"sum_{uuid.uuid4().hex[:16]}"
    payload = result.get("payload") or {}
    raw = result.get("raw") or {}
    insert_summary(
        conn,
        {
            "summary_id": sid,
            "project_id": facts.get("project_id") or "",
            "observation_range": evidence_snapshot_key(facts),
            "model_identifier": model,
            "model_digest": raw.get("digest") if isinstance(raw, dict) else None,
            "prompt_version": "v0.1-summarize-b",
            "facts_json": json.dumps(facts, ensure_ascii=False),
            "interpretations_json": json.dumps(payload.get("interpretations") or [], ensure_ascii=False),
            "suggestions_json": json.dumps(payload.get("suggestions") or [], ensure_ascii=False),
            "evidence_ids": json.dumps(facts.get("evidence_ids") or [], ensure_ascii=False),
            "validation_status": "pass" if result.get("ok") else "degraded",
            "generated_at": result.get("generated_at") or utc_now(),
        },
    )
    insert_event(
        conn,
        kind="summary",
        project_id=facts.get("project_id"),
        payload={
            "summary_id": sid,
            "ok": bool(result.get("ok")),
            "degraded": bool(result.get("degraded")),
            "errors": (result.get("errors") or [])[:8],
            "model": model,
            "prompt_eval_count": raw.get("prompt_eval_count") if isinstance(raw, dict) else None,
            "eval_count": raw.get("eval_count") if isinstance(raw, dict) else None,
            "load_duration": raw.get("load_duration") if isinstance(raw, dict) else None,
            "eval_duration": raw.get("eval_duration") if isinstance(raw, dict) else None,
            "retry_count": result.get("retry_count") or 0,
        },
    )
    return sid


def reuse_summary_note(row) -> str:
    from xiaomao.summarize import format_model_note

    try:
        interpretations = json.loads(row["interpretations_json"] or "[]")
        suggestions = json.loads(row["suggestions_json"] or "[]")
    except json.JSONDecodeError:
        interpretations, suggestions = [], []
    payload = {
        "interpretations": interpretations,
        "suggestions": suggestions,
        "unknowns": [],
    }
    return format_model_note(
        payload,
        model=row["model_identifier"] or "",
        digest=row["model_digest"],
        validation=row["validation_status"] or "pass",
    ) + "\n（同一证据快照已有通过的解读，未再次加载模型。）"


def daily_job_key(date: str, project_id: str, snapshot: str) -> str:
    return f"daily:{date}:{project_id}:{snapshot}"


def claim_daily_job(conn, key: str) -> tuple[str, bool]:
    """Return (job_id, is_new). Duplicate same-day snapshot is not a new inference."""
    existing = job_get(conn, key)
    if existing is not None and existing["state"] in {"done", "running"}:
        return existing["job_id"], False
    jid = existing["job_id"] if existing else f"job_{uuid.uuid4().hex[:16]}"
    upsert_job(conn, job_id=jid, deduplication_key=key, state="running")
    return jid, True


def latest_report(home: Path, kind: str, project_id: str = "website") -> Path | None:
    paths = layout(home)
    if kind == "daily":
        folder = paths["reports_daily"]
        files = sorted(folder.glob("*.txt"))
        return files[-1] if files else None
    if kind == "handoff":
        folder = paths["reports_handoff"]
        files = sorted(folder.glob(f"{project_id}-*.txt"))
        return files[-1] if files else None
    if kind == "status":
        folder = paths["reports_projects"]
        files = sorted(folder.glob(f"{project_id}-*.txt"))
        return files[-1] if files else None
    return None
