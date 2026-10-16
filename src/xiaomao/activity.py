"""Append-only daily evidence, independent of the registered handoff proof."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from xiaomao.change_evidence import commit_evidence
from xiaomao.git_readonly import run_git
from xiaomao.inventory import metadata, scope_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_trees (
  tree_id TEXT NOT NULL, scope_key TEXT NOT NULL, project_id TEXT NOT NULL,
  path TEXT NOT NULL, repo_id TEXT NOT NULL, first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL, frontier_json TEXT NOT NULL, state_json TEXT NOT NULL,
  gone INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(tree_id, scope_key)
);
CREATE TABLE IF NOT EXISTS daily_samples (
  sample_id TEXT PRIMARY KEY, tree_id TEXT NOT NULL, scope_key TEXT NOT NULL,
  repo_id TEXT NOT NULL, at_utc TEXT NOT NULL, baseline INTEGER NOT NULL,
  state_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS daily_samples_window ON daily_samples(scope_key, tree_id, at_utc);
CREATE TABLE IF NOT EXISTS daily_commits (
  repo_id TEXT NOT NULL, sha TEXT NOT NULL, payload_json TEXT NOT NULL,
  PRIMARY KEY(repo_id, sha)
);
CREATE TABLE IF NOT EXISTS daily_commit_links (
  repo_id TEXT NOT NULL, sha TEXT NOT NULL, tree_id TEXT NOT NULL,
  scope_key TEXT NOT NULL, first_seen TEXT NOT NULL, effective_at TEXT NOT NULL,
  PRIMARY KEY(repo_id, sha, tree_id, scope_key)
);
CREATE INDEX IF NOT EXISTS daily_commits_window ON daily_commit_links(scope_key, effective_at);
CREATE TABLE IF NOT EXISTS daily_checks (
  check_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, scope_key TEXT NOT NULL,
  at_utc TEXT NOT NULL, results_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS daily_checks_window ON daily_checks(scope_key, at_utc);
CREATE TABLE IF NOT EXISTS daily_windows (
  window_key TEXT PRIMARY KEY, date TEXT NOT NULL, scope_hash TEXT NOT NULL,
  evidence_hash TEXT NOT NULL, bundle_json TEXT NOT NULL, completed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_model_cache (
  cache_key TEXT PRIMARY KEY, note_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_notifications (
  window_key TEXT PRIMARY KEY, state TEXT NOT NULL, attempted_at TEXT NOT NULL
);
"""
COMMIT_BATCH = 512


def stamp(value: str) -> str:
    from datetime import timezone
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()


def _frontier(path: Path, head: str | None) -> list[str]:
    raw = run_git(path, "for-each-ref", "--format=%(objectname)", "refs/heads", "refs/remotes").stdout
    return sorted(set(raw.split()) | ({head} if head else set()))


def record_snapshot(conn, cfg, project, wt, snap, observed_at: str, observation_id: str) -> None:
    from xiaomao.ops import storage_over_budget

    observed_at = stamp(observed_at)
    path = Path(wt.path)
    info = metadata(wt.path)
    key, repo_id = scope_key(project), info["repo_id"]
    previous = conn.execute("SELECT * FROM daily_trees WHERE tree_id=? AND scope_key=?",
                            (wt.worktree_id, key)).fetchone()
    if previous and previous["repo_id"] != repo_id:
        previous = None
    state = {
        "path": str(path.resolve()), "head": snap.head_oid, "branch": snap.branch_ref,
        "files": snap.content_changes, "limitations": list(snap.evidence_limitations),
        "observation_id": observation_id, "status": snap.collection_status, "gone": False,
        "staged": len(snap.staged), "unstaged": len(snap.unstaged), "untracked": len(snap.untracked),
    }
    from xiaomao.feature_context import snapshot_context
    state["feature_context"] = snapshot_context(conn, cfg, project, path, observed_at)
    frontier = _frontier(path, snap.head_oid)
    known = json.loads(previous["frontier_json"]) if previous else frontier
    first_seen = previous["first_seen"] if previous else observed_at
    over_budget = storage_over_budget(Path(cfg.home))
    if previous and frontier:
        listing = run_git(path, "rev-list", "--reverse", "--topo-order", *frontier,
                          *(["--not", *known] if known else []), check=False)
        if listing.returncode:
            state["limitations"].append("commit_history_unavailable")
            frontier = known
        else:
            pending = listing.stdout.split()
            for oid in pending[:COMMIT_BATCH]:
                cached = conn.execute("SELECT payload_json FROM daily_commits WHERE repo_id=? AND sha=?",
                                      (repo_id, oid)).fetchone()
                facts = json.loads(cached[0]) if cached else commit_evidence(path, oid)
                effective = observed_at
                # Old imported history belongs to its first observed window;
                # recover recent commit time across a sampling/sleep gap.
                committed = stamp(facts["committed_at"])
                if first_seen <= committed <= observed_at:
                    effective = committed
                known_time = conn.execute(
                    "SELECT MIN(effective_at) FROM daily_commit_links WHERE repo_id=? AND sha=?",
                    (repo_id, oid)).fetchone()[0]
                if known_time:
                    effective = known_time
                if not cached:
                    facts["feature_context"] = snapshot_context(conn, cfg, project, path, effective, ref=oid)
                if over_budget:
                    facts = dict(facts, files=[{k: v for k, v in dict(f, patch="").items()
                                               if k not in {"units", "snapshot"}} for f in facts["files"]])
                    facts["limitations"] = [*facts["limitations"], "storage_budget"]
                conn.execute("INSERT OR IGNORE INTO daily_commits VALUES (?,?,?)",
                             (repo_id, oid, json.dumps(facts, ensure_ascii=False)))
                conn.execute("INSERT OR IGNORE INTO daily_commit_links VALUES (?,?,?,?,?,?)",
                             (repo_id, oid, wt.worktree_id, key, observed_at, effective))
            if len(pending) > COMMIT_BATCH:
                frontier = sorted(set(known + pending[:COMMIT_BATCH]))
                state["limitations"].append(f"commit_backlog: {len(pending) - COMMIT_BATCH}")
    state_json = json.dumps(state, sort_keys=True, ensure_ascii=False)
    old_state = json.loads(previous["state_json"]) if previous else None
    if previous is None or state != old_state:
        conn.execute("INSERT INTO daily_samples VALUES (?,?,?,?,?,?,?)",
                     ("ds_" + uuid.uuid4().hex, wt.worktree_id, key, repo_id, observed_at,
                      int(previous is None), state_json))
    conn.execute("""
        INSERT INTO daily_trees VALUES (?,?,?,?,?,?,?,?,?,0)
        ON CONFLICT(tree_id,scope_key) DO UPDATE SET
          path=excluded.path, repo_id=excluded.repo_id, first_seen=excluded.first_seen,
          last_seen=excluded.last_seen, frontier_json=excluded.frontier_json,
          state_json=excluded.state_json, gone=0
    """, (wt.worktree_id, key, project.project_id, str(path.resolve()), repo_id, first_seen,
          observed_at, json.dumps(frontier), state_json))


def record_checks(conn, project, results: list[dict], at: str) -> None:
    at, key = stamp(at), scope_key(project)
    compact = [{k: row.get(k) for k in ("worktree_id", "status", "observation_id", "error", "reason")}
               for row in results]
    conn.execute("INSERT INTO daily_checks VALUES (?,?,?,?,?)",
                 ("dc_" + uuid.uuid4().hex, project.project_id, key, at, json.dumps(compact)))
    live = {r.get("worktree_id") for r in results if r.get("status") != "gone"}
    # Disappearance of a supplementary tree is history, not fabricated failure.
    for row in conn.execute("SELECT * FROM daily_trees WHERE scope_key=? AND gone=0", (key,)):
        if getattr(project, "_inventory_incomplete", False):
            continue  # listing failed; absence from this invocation proves nothing
        if row["tree_id"] in live or not any(r.get("status") in {"ok", "unchanged"} for r in results):
            continue
        if any(w.worktree_id == row["tree_id"] for w in getattr(project, "_registered_worktrees", project.worktrees)):
            continue
        state = dict(json.loads(row["state_json"]), gone=True)
        encoded = json.dumps(state, sort_keys=True, ensure_ascii=False)
        conn.execute("INSERT INTO daily_samples VALUES (?,?,?,?,?,?,?)",
                     ("ds_" + uuid.uuid4().hex, row["tree_id"], key, row["repo_id"], at, 0, encoded))
        conn.execute("UPDATE daily_trees SET gone=1,state_json=?,last_seen=? WHERE tree_id=? AND scope_key=?",
                     (encoded, at, row["tree_id"], key))
        conn.execute("UPDATE worktrees SET scan_enabled=0 WHERE worktree_id=?", (row["tree_id"],))
