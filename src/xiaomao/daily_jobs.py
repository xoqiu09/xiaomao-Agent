"""Daily orchestration. Only collection holds the scan lock / write transaction."""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date as Date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from xiaomao.daily import build_bundle, completed_date, render_bundle, report_window
from xiaomao.paths import ensure_layout, layout
from xiaomao.report_access import bind_report, scope_set
from xiaomao.store import open_db, utc_now

from xiaomao.feature_daily import (PROMPT_VERSION, SYSTEM, model_packet, prepare_inference,
                                   inference_schema, expand_inference, validate_records)
MAX_CATCHUP = 7


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _scope_hash(cfg) -> str:
    return _hash(scope_set(cfg))


def _record_key(bundle) -> str:
    return bundle["window"]["key"] + ":" + _hash(bundle["scope"])


def _model_packet(bundle, project) -> dict:
    return model_packet(bundle, project)


def summarize_projects(cfg, bundle, client) -> dict:
    from xiaomao.ops import storage_over_budget
    notes = {}
    model = cfg.depth_candidates[0] if cfg.depth_candidates else cfg.lite_model
    for project in bundle["projects"]:
        if not project["changed"]:
            continue
        packet = _model_packet(bundle, project)
        request, bindings = prepare_inference(packet, max_chars=min(48000, cfg.context_length * 2))
        encoded = json.dumps(request, ensure_ascii=False, sort_keys=True)
        cache_key = _hash([PROMPT_VERSION, model, bundle["scope"], packet])
        with open_db(layout(Path(cfg.home))["db"]) as conn:
            cached = conn.execute("SELECT note_json FROM daily_model_cache WHERE cache_key=?",
                                  (cache_key,)).fetchone()
        if cached:
            notes[project["repo_id"]] = json.loads(cached[0])
            continue
        error = None
        if client is None:
            error = "本地模型不可用或未启用"
        elif storage_over_budget(Path(cfg.home)):
            error = "达到存储预算，保留规则证据"
        elif not request["units"]:
            error = "没有可供模型解读的完整差异，保留全部原始依据"
        result = {}
        if error is None:
            try:
                raw = client.generate_json(model=model, system=SYSTEM, user=encoded,
                                           schema=inference_schema(request), timeout=180)
                result = validate_records(project, packet, expand_inference(raw.get("json") or {}, bindings))
                result.update(model=model, prompt_version=PROMPT_VERSION,
                              input_units=len(request["units"]),
                              omitted_input_units=len(packet["units"]) - len(request["units"]))
                if not result["accepted"]:
                    result["error"] = "输出未通过证据校验"
                elif result["rejected"] or result["uninterpreted_units"]:
                    result["error"] = "部分功能影响待确认，已保留可用解读"
            except Exception as exc:
                # Never persist raw model output or exception bodies.
                error = "输出未通过证据校验" if isinstance(exc, (ValueError, TypeError, AttributeError)) else "模型调用失败"
        if error:
            result = {"error": error}
        elif result.get("accepted"):
            with open_db(layout(Path(cfg.home))["db"]) as conn:
                conn.execute("INSERT OR REPLACE INTO daily_model_cache VALUES (?,?)",
                             (cache_key, json.dumps(result, ensure_ascii=False)))
        notes[project["repo_id"]] = result
    return notes


def due_dates(cfg, conn, now: datetime) -> list[str]:
    """Catch up chronologically, at most seven windows per invocation."""
    from xiaomao.inventory import effective_config, scope_key
    from xiaomao.scope import project_exclusion_reason
    effective = effective_config(cfg)
    keys = [scope_key(p) for p in effective.projects if not project_exclusion_reason(p)]
    starts = [r[0] for key in keys for r in conn.execute(
        "SELECT MIN(first_seen) FROM daily_trees WHERE scope_key=?", (key,)) if r[0]]
    if not starts:
        return [completed_date(cfg, now)]
    first_seen = datetime.fromisoformat(min(starts))
    # First baseline belongs to the next cutoff, including when taken at cutoff.
    first = Date.fromisoformat(completed_date(cfg, first_seen)) + timedelta(days=1)
    last = Date.fromisoformat(completed_date(cfg, now))
    completed = {r["date"]: r for r in conn.execute(
        "SELECT * FROM daily_windows WHERE scope_hash=?", (_scope_hash(cfg),))}
    dates = []
    day = first
    while day <= last and len(dates) < MAX_CATCHUP:
        name = day.isoformat()
        previous = completed.get(name)
        if previous is None:
            dates.append(name)
        else:
            # Commits learned after a sleep can repair a previously published window.
            start, end = report_window(cfg, name)
            late = any(conn.execute(
                "SELECT 1 FROM daily_commit_links WHERE scope_key=? AND effective_at>=? "
                "AND effective_at<? AND first_seen>? LIMIT 1",
                (key, start.isoformat(), end.isoformat(), previous["completed_at"])).fetchone()
                for key in keys)
            if late:
                dates.append(name)
        day += timedelta(days=1)
    return dates


def persist_window(conn, bundle) -> None:
    if bundle["preview"]:
        return
    conn.execute("INSERT OR REPLACE INTO daily_windows VALUES (?,?,?,?,?,?)", (
        _record_key(bundle), bundle["window"]["date"], _hash(bundle["scope"]),
        bundle["evidence_hash"], json.dumps(bundle, ensure_ascii=False), utc_now(),
    ))


def macos_notification(date: str, path: str) -> bool:
    # Arguments are data, never interpolated AppleScript or shell.
    script = ('on run argv\n display notification (item 1 of argv) '
              'with title "小猫日报" subtitle (item 2 of argv)\nend run')
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script, f"{date} 工作总结已生成，可从菜单打开。", path],
            capture_output=True, timeout=15, check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def notify_once(conn, cfg, bundle, sender=macos_notification) -> None:
    if bundle["preview"] or not cfg.daily_notify:
        return
    # Claim before sending: a crash or OS refusal never produces duplicate alerts.
    key = bundle["window"]["key"]
    row = conn.execute("INSERT OR IGNORE INTO daily_notifications VALUES (?,?,?)",
                       (key, "attempted", utc_now()))
    conn.commit()
    if not row.rowcount:
        return
    from xiaomao.reports import daily_path
    try:
        ok = sender(bundle["window"]["date"], str(daily_path(cfg, bundle["window"]["date"])))
    except Exception:
        ok = False
    conn.execute("UPDATE daily_notifications SET state=? WHERE window_key=?",
                 ("sent" if ok else "failed", key))
    conn.commit()


def preserve_report(path: Path, text: str) -> None:
    """Keep previous bytes before any regeneration; history is never pruned."""
    if not path.exists():
        return
    from xiaomao.handoff_view import _read_file
    from xiaomao.menu_briefing import _atomic_write
    old = _read_file(path, path.parent, 2 * 1024 * 1024)
    if old == text.encode():
        return
    meta_path = path.with_suffix(".json")
    meta = _read_file(meta_path, path.parent, 128 * 1024) if meta_path.exists() else None
    signature = hashlib.sha256(old + (meta or b"")).hexdigest()
    archive = path.parent / "history" / path.stem
    if archive.resolve() != path.parent.resolve() / "history" / path.stem:
        raise ValueError("历史报告目录越出范围")
    archive.mkdir(parents=True, exist_ok=True)
    _atomic_write(archive / (signature + ".txt"), old.decode("utf-8"))
    if meta is not None:
        _atomic_write(archive / (signature + ".json"), meta.decode("utf-8"))


def run_daily(cfg, *, date=None, scheduled=False, with_model=False, now=None,
              client_factory=None, notifier=macos_notification) -> list[Path]:
    from xiaomao.collect import scan_authorized
    from xiaomao.config import load_config
    from xiaomao.inventory import effective_config
    from xiaomao.lock import ScanLock
    from xiaomao.menu_briefing import _atomic_write, write_daily_briefing
    from xiaomao.reports import daily_path
    from xiaomao.store import infer_paused
    now = now or datetime.now(timezone.utc)
    paths = ensure_layout(Path(cfg.home))
    written = []
    with ScanLock(Path(cfg.home) / "daily.lock"):
        with open_db(paths["db"]) as conn:
            dates = due_dates(cfg, conn, now) if scheduled and not date else [
                date or now.astimezone(ZoneInfo(cfg.timezone)).date().isoformat()]
        if not dates:
            return []
        with ScanLock(paths["lock"], retries=5, retry_s=1), open_db(paths["db"]) as conn:
            effective = effective_config(cfg)
            scan_authorized(conn, effective)  # final collection; contains no model calls
            if scheduled and not date:
                dates = due_dates(effective, conn, now)
            bundles = [build_bundle(effective, conn, date=d, now=now) for d in dates]
            paused = infer_paused(conn)
        # Both SQLite and the collection lock are released before local inference.
        client = None
        if (scheduled or with_model) and not paused and any(
                p["changed"] for b in bundles for p in b["projects"]):
            if client_factory is None:
                from xiaomao.cli import _briefing_client
                client_factory = _briefing_client
            client = client_factory(cfg)
        for bundle in bundles:
            notes = summarize_projects(cfg, bundle, client) if scheduled or with_model else {}
            if paused:
                notes = {p["repo_id"]: {"error": "推理已暂停"} for p in bundle["projects"] if p["changed"]}
            # A concurrent settings edit invalidates the pending publication.
            if scope_set(load_config(Path(cfg.home))) != bundle["scope"]:
                raise RuntimeError("观察范围已改变，请重新生成日报")
            from xiaomao.feature_daily import feature_records
            from xiaomao.feature_render import write_details
            bundle["functional_records"] = {p["repo_id"]: feature_records(p, notes.get(p["repo_id"]))
                                             for p in bundle["projects"]}
            bundle["interpretation_hash"] = _hash(bundle["functional_records"])
            dest = daily_path(cfg, bundle["window"]["date"])
            if dest.parent.resolve() != Path(cfg.home).resolve() / "reports" / "daily":
                raise ValueError("日报目录越出数据 home")
            text = render_bundle(bundle, notes)
            write_details(cfg, bundle, notes)
            preserve_report(dest, text)
            _atomic_write(dest, text)
            bind_report(dest, text, cfg, "daily", daily_bundle=bundle)
            write_daily_briefing(cfg, bundle, notes)
            with open_db(paths["db"]) as conn:
                persist_window(conn, bundle)
            if scheduled:
                with open_db(paths["db"]) as conn:
                    notify_once(conn, cfg, bundle, notifier)
            written.append(dest)
    return written
