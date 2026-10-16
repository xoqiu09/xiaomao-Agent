from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  project_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  approved_root TEXT NOT NULL,
  policy_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worktrees (
  worktree_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(project_id),
  canonical_path TEXT NOT NULL,
  common_git_dir_id TEXT,
  scan_enabled INTEGER NOT NULL DEFAULT 1,
  notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS observations (
  observation_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  worktree_id TEXT NOT NULL,
  observed_at_utc TEXT NOT NULL,
  local_timezone TEXT NOT NULL,
  head_oid TEXT,
  branch_ref TEXT,
  dirty_snapshot_id TEXT,
  source_type TEXT NOT NULL,
  collection_status TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  is_unborn INTEGER NOT NULL DEFAULT 0,
  is_detached INTEGER NOT NULL DEFAULT 0,
  staged_count INTEGER NOT NULL DEFAULT 0,
  unstaged_count INTEGER NOT NULL DEFAULT 0,
  untracked_count INTEGER NOT NULL DEFAULT 0,
  facts_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(worktree_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS evidence (
  evidence_id TEXT PRIMARY KEY,
  observation_id TEXT NOT NULL REFERENCES observations(observation_id),
  source_locator TEXT NOT NULL,
  source_hash TEXT,
  safe_excerpt TEXT,
  evidence_kind TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  availability TEXT NOT NULL,
  redaction_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS test_evidence (
  evidence_id TEXT PRIMARY KEY,
  worktree_id TEXT NOT NULL,
  head_oid TEXT,
  dirty_snapshot_id TEXT,
  test_suite_id TEXT,
  environment_label TEXT,
  exit_code INTEGER,
  result_summary TEXT,
  started_at TEXT,
  finished_at TEXT,
  provenance TEXT NOT NULL,
  applicability TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS summaries (
  summary_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  observation_range TEXT,
  model_identifier TEXT,
  model_digest TEXT,
  prompt_version TEXT,
  facts_json TEXT NOT NULL,
  interpretations_json TEXT NOT NULL DEFAULT '[]',
  suggestions_json TEXT NOT NULL DEFAULT '[]',
  evidence_ids TEXT NOT NULL DEFAULT '[]',
  validation_status TEXT NOT NULL,
  generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  deduplication_key TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL,
  retry_count INTEGER NOT NULL DEFAULT 0,
  not_before TEXT,
  last_safe_error TEXT
);

CREATE TABLE IF NOT EXISTS user_notes (
  note_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  confirmed_goal_or_decision TEXT NOT NULL,
  provenance TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discovered_worktrees (
  common_git_dir_id TEXT NOT NULL,
  path TEXT NOT NULL,
  head_oid TEXT,
  branch_ref TEXT,
  detached INTEGER NOT NULL DEFAULT 0,
  authorized INTEGER NOT NULL DEFAULT 0,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  PRIMARY KEY (common_git_dir_id, path)
);

CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(
  evidence_id,
  source_locator,
  safe_excerpt,
  evidence_kind
);

CREATE TABLE IF NOT EXISTS scan_runs (
  run_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  outcome TEXT NOT NULL,
  last_safe_error TEXT,
  inserted INTEGER NOT NULL DEFAULT 0,
  unchanged INTEGER NOT NULL DEFAULT 0,
  gap_since_last_success_s INTEGER,
  last_observation_id TEXT
);

CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  at_utc TEXT NOT NULL,
  project_id TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}'
);
"""

SCHEMA_VERSION = "5"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        if not db_path.parent.is_dir():
            raise
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    from xiaomao.activity import SCHEMA as DAILY_SCHEMA
    conn.executescript(DAILY_SCHEMA)
    from xiaomao.feature_context import SCHEMA as FEATURE_SCHEMA
    conn.executescript(FEATURE_SCHEMA)
    from xiaomao.documents import SCHEMA as DOCUMENT_SCHEMA
    conn.executescript(DOCUMENT_SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        ("schema", SCHEMA_VERSION),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return row["value"]


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        (key, value),
    )


def infer_paused(conn: sqlite3.Connection) -> bool:
    return get_meta(conn, "infer_paused", "0") == "1"


def scan_paused(conn: sqlite3.Connection) -> bool:
    return get_meta(conn, "scan_paused", "0") == "1"


def insert_event(
    conn: sqlite3.Connection,
    *,
    kind: str,
    project_id: str | None = None,
    payload: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> str:
    import json
    import uuid

    eid = event_id or f"evn_{uuid.uuid4().hex[:16]}"
    conn.execute(
        """
        INSERT INTO events(event_id, kind, at_utc, project_id, payload_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (eid, kind, utc_now(), project_id, json.dumps(payload or {}, ensure_ascii=False)),
    )
    return eid


def last_scan_success(conn: sqlite3.Connection, project_id: str | None = None) -> sqlite3.Row | None:
    if project_id:
        return conn.execute(
            """
            SELECT * FROM scan_runs
            WHERE project_id = ? AND outcome = 'success'
            ORDER BY finished_at DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    return conn.execute(
        """
        SELECT * FROM scan_runs
        WHERE outcome = 'success'
        ORDER BY finished_at DESC
        LIMIT 1
        """
    ).fetchone()


def insert_scan_run(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO scan_runs(
          run_id, project_id, started_at, finished_at, outcome, last_safe_error,
          inserted, unchanged, gap_since_last_success_s, last_observation_id
        ) VALUES (
          :run_id, :project_id, :started_at, :finished_at, :outcome, :last_safe_error,
          :inserted, :unchanged, :gap_since_last_success_s, :last_observation_id
        )
        """,
        row,
    )


def insert_summary(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO summaries(
          summary_id, project_id, observation_range, model_identifier, model_digest,
          prompt_version, facts_json, interpretations_json, suggestions_json,
          evidence_ids, validation_status, generated_at
        ) VALUES (
          :summary_id, :project_id, :observation_range, :model_identifier, :model_digest,
          :prompt_version, :facts_json, :interpretations_json, :suggestions_json,
          :evidence_ids, :validation_status, :generated_at
        )
        """,
        row,
    )


def latest_summary_for_range(conn: sqlite3.Connection, project_id: str, observation_range: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM summaries
        WHERE project_id = ? AND observation_range = ?
        ORDER BY generated_at DESC
        LIMIT 1
        """,
        (project_id, observation_range),
    ).fetchone()


def job_get(conn: sqlite3.Connection, deduplication_key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM jobs WHERE deduplication_key = ?",
        (deduplication_key,),
    ).fetchone()


def upsert_job(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    deduplication_key: str,
    state: str,
    retry_count: int = 0,
    last_safe_error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO jobs(job_id, deduplication_key, state, retry_count, not_before, last_safe_error)
        VALUES (?, ?, ?, ?, NULL, ?)
        ON CONFLICT(deduplication_key) DO UPDATE SET
          state=excluded.state,
          retry_count=excluded.retry_count,
          last_safe_error=excluded.last_safe_error
        """,
        (job_id, deduplication_key, state, retry_count, last_safe_error),
    )


@contextmanager
def open_db(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        init_db(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_project(conn: sqlite3.Connection, project_id: str, display_name: str, approved_root: str, policy_version: str) -> None:
    conn.execute(
        """
        INSERT INTO projects(project_id, display_name, approved_root, policy_version)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
          display_name=excluded.display_name,
          approved_root=excluded.approved_root,
          policy_version=excluded.policy_version
        """,
        (project_id, display_name, approved_root, policy_version),
    )


def upsert_worktree(
    conn: sqlite3.Connection,
    worktree_id: str,
    project_id: str,
    canonical_path: str,
    common_git_dir_id: str | None,
    scan_enabled: bool,
    notes: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO worktrees(worktree_id, project_id, canonical_path, common_git_dir_id, scan_enabled, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(worktree_id) DO UPDATE SET
          project_id=excluded.project_id,
          canonical_path=excluded.canonical_path,
          common_git_dir_id=excluded.common_git_dir_id,
          scan_enabled=excluded.scan_enabled,
          notes=excluded.notes
        """,
        (worktree_id, project_id, canonical_path, common_git_dir_id, 1 if scan_enabled else 0, notes),
    )


def latest_observation(conn: sqlite3.Connection, worktree_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM observations
        WHERE worktree_id = ?
        ORDER BY observed_at_utc DESC, rowid DESC
        LIMIT 1
        """,
        (worktree_id,),
    ).fetchone()


def insert_observation(conn: sqlite3.Connection, row: dict[str, Any]) -> bool:
    """Insert observation. Returns False if fingerprint already stored for this worktree."""
    try:
        conn.execute(
            """
            INSERT INTO observations(
              observation_id, project_id, worktree_id, observed_at_utc, local_timezone,
              head_oid, branch_ref, dirty_snapshot_id, source_type, collection_status,
              fingerprint, is_unborn, is_detached, staged_count, unstaged_count,
              untracked_count, facts_json
            ) VALUES (
              :observation_id, :project_id, :worktree_id, :observed_at_utc, :local_timezone,
              :head_oid, :branch_ref, :dirty_snapshot_id, :source_type, :collection_status,
              :fingerprint, :is_unborn, :is_detached, :staged_count, :unstaged_count,
              :untracked_count, :facts_json
            )
            """,
            row,
        )
        return True
    except sqlite3.IntegrityError:
        return False


def insert_evidence(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO evidence(
          evidence_id, observation_id, source_locator, source_hash, safe_excerpt,
          evidence_kind, observed_at, availability, redaction_status
        ) VALUES (
          :evidence_id, :observation_id, :source_locator, :source_hash, :safe_excerpt,
          :evidence_kind, :observed_at, :availability, :redaction_status
        )
        """,
        row,
    )


def record_discovered_worktree(
    conn: sqlite3.Connection,
    *,
    common_git_dir_id: str,
    path: str,
    head_oid: str | None,
    branch_ref: str | None,
    detached: bool,
    authorized: bool,
) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO discovered_worktrees(
          common_git_dir_id, path, head_oid, branch_ref, detached, authorized,
          first_seen_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(common_git_dir_id, path) DO UPDATE SET
          head_oid=excluded.head_oid,
          branch_ref=excluded.branch_ref,
          detached=excluded.detached,
          authorized=excluded.authorized,
          last_seen_at=excluded.last_seen_at
        """,
        (common_git_dir_id, path, head_oid, branch_ref, 1 if detached else 0, 1 if authorized else 0, now, now),
    )


def latest_by_project(conn: sqlite3.Connection, project_id: str, worktree_ids: list[str] | None = None) -> list[sqlite3.Row]:
    if worktree_ids == []:
        return []
    restriction = ""
    args = [project_id]
    if worktree_ids is not None:
        restriction = " AND w.worktree_id IN (" + ",".join("?" for _ in worktree_ids) + ")"
        args.extend(worktree_ids)
    return list(
        conn.execute(
            """
            SELECT
              w.worktree_id AS worktree_id,
              w.canonical_path AS canonical_path,
              w.scan_enabled AS scan_enabled,
              w.notes AS notes,
              p.display_name AS display_name,
              p.approved_root AS approved_root,
              o.observation_id AS observation_id,
              o.observed_at_utc AS observed_at_utc,
              o.head_oid AS head_oid,
              o.branch_ref AS branch_ref,
              o.collection_status AS collection_status,
              o.is_unborn AS is_unborn,
              o.is_detached AS is_detached,
              o.staged_count AS staged_count,
              o.unstaged_count AS unstaged_count,
              o.untracked_count AS untracked_count,
              o.facts_json AS facts_json
            FROM worktrees w
            JOIN projects p ON p.project_id = w.project_id
            LEFT JOIN observations o ON o.observation_id = (
              SELECT observation_id FROM observations
              WHERE worktree_id = w.worktree_id AND project_id = w.project_id
              ORDER BY observed_at_utc DESC, rowid DESC LIMIT 1
            )
            WHERE w.project_id = ?
            ORDER BY w.worktree_id
            """.replace("WHERE w.project_id = ?", "WHERE w.project_id = ?" + restriction),
            args,
        ).fetchall()
    )


def evidence_for_observation(conn: sqlite3.Connection, observation_id: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM evidence WHERE observation_id = ? ORDER BY evidence_kind, source_locator",
            (observation_id,),
        ).fetchall()
    )
