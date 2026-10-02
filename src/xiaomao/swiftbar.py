"""Read-only SwiftBar menu for Xiaomao.

Reads existing SQLite + report files. Does not scan worktrees, does not
load a model or take the scan lock. SQLite may manage WAL/SHM coordination
files; application rows and report/config contents are never written.
"""

from __future__ import annotations

import json
import base64
import shlex
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from xiaomao.config import ProjectSpec, load_config
from xiaomao.handoff_view import inspect_handoff
from xiaomao.menu_briefing import (
    facts_of_row,
    latest_menu_briefing,
    parse_menu_briefing,
    rule_sentence,
)
from xiaomao.scope import project_exclusion_reason

# Keep in lockstep with xiaomao.ops.GAP_WARN_AFTER_S (two missed 5-minute ticks + 60s).
STALE_AFTER_S = 300 * 2 + 60
DEFAULT_TZ = "Asia/Taipei"
DEFAULT_HOME = Path.home() / "Library" / "Application Support" / "Xiaomao"
HEAD_ICON = Path(__file__).resolve().parents[2] / "scripts" / "swiftbar" / "xiaoba-head.png"


def _head_image() -> str:
    """Single-line colour PNG for SwiftBar `image=`. Empty if the asset is missing."""
    try:
        data = HEAD_ICON.read_bytes()
    except OSError:
        return ""
    if not data or len(data) > 64 * 1024:
        return ""
    return base64.b64encode(data).decode("ascii")


def _title_line(label: str, image_b64: str) -> str:
    suffix = "emojize=false symbolize=false"
    if image_b64:
        suffix = f"image={image_b64} {suffix}"
    return f"{_escape(label)} | {suffix}" if label else f" | {suffix}"


def _tz(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _now_utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def relative_zh(seconds: int | None) -> str:
    if seconds is None:
        return "尚无"
    if seconds < 0:
        seconds = 0
    if seconds < 45:
        return "刚刚"
    if seconds < 3600:
        minutes = max(1, seconds // 60)
        return f"{minutes} 分钟前"
    if seconds < 86400:
        hours = max(1, seconds // 3600)
        return f"{hours} 小时前"
    days = max(1, seconds // 86400)
    return f"{days} 天前"


def _escape(text: str) -> str:
    return (
        text.replace("|", "/")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def file_href(path: Path) -> str:
    resolved = path.expanduser().resolve()
    return "file://" + quote(str(resolved), safe="/")


def latest_daily(home: Path) -> Path | None:
    folder = home / "reports" / "daily"
    if not folder.is_dir():
        return None
    files = sorted(
        p
        for p in folder.glob("*.txt")
        if p.name != ".keep" and not p.name.endswith(".model.txt")
    )
    dated = [p for p in files if len(p.stem) == 10 and p.stem[4] == "-" and p.stem[7] == "-"]
    pool = dated or files
    return pool[-1] if pool else None


def latest_handoff(home: Path, project_id: str = "website") -> Path | None:
    folder = home / "reports" / "handoff"
    if not folder.is_dir():
        return None
    files = sorted(folder.glob(f"{project_id}-*.txt"))
    return files[-1] if files else None


def reports_dir(home: Path) -> Path:
    return home / "reports"


def _load_timezone(home: Path) -> str:
    path = home / "config.json"
    if not path.is_file():
        return DEFAULT_TZ
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_TZ
    tz = data.get("timezone")
    return tz if isinstance(tz, str) and tz else DEFAULT_TZ


def _connect_ro(db: Path) -> sqlite3.Connection:
    uri = f"file:{db.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1500")
    return conn


def _row_get(row: sqlite3.Row | None, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _read_state(home: Path, project_ids: list[str]) -> dict[str, Any]:
    state: dict[str, Any] = {
        "db_ok": False,
        "db_busy": False,
        "db_missing": False,
        "last_run": None,
        "last_success": None,
        "infer_paused": False,
        "scan_paused": False,
        "pilot_status": None,
        "worktrees": [],
        "error": None,
    }
    db = home / "xiaomao.sqlite"
    if not db.is_file():
        state["db_missing"] = True
        return state
    if not project_ids:
        state["error"] = "no_authorized_projects"
        return state
    try:
        conn = _connect_ro(db)
    except sqlite3.Error as exc:
        msg = str(exc).lower()
        state["db_busy"] = "busy" in msg or "locked" in msg
        state["error"] = type(exc).__name__
        return state
    try:
        try:
            marks = ",".join("?" for _ in project_ids)
            state["last_run"] = conn.execute(
                f"""
                SELECT outcome, finished_at, last_safe_error, inserted, unchanged
                FROM scan_runs
                WHERE project_id IN ({marks})
                ORDER BY finished_at DESC, rowid DESC
                LIMIT 1
                """, project_ids,
            ).fetchone()
            successes = [conn.execute(
                "SELECT outcome, finished_at, last_safe_error, inserted, unchanged "
                "FROM scan_runs WHERE outcome='success' AND project_id=? "
                "ORDER BY finished_at DESC, rowid DESC LIMIT 1", (pid,),
            ).fetchone() for pid in project_ids]
            if all(row is not None for row in successes):
                state["last_success"] = min(successes, key=lambda row: row["finished_at"])
        except sqlite3.Error as exc:
            state["error"] = type(exc).__name__
            return state
        try:
            meta = {
                row["key"]: row["value"]
                for row in conn.execute("SELECT key, value FROM meta")
            }
            state["infer_paused"] = meta.get("infer_paused") == "1"
            state["scan_paused"] = meta.get("scan_paused") == "1"
            state["pilot_status"] = meta.get("pilot_status")
        except sqlite3.Error:
            pass
        try:
            state["worktrees"] = list(
                conn.execute(
                    f"""
                    SELECT
                      w.worktree_id AS worktree_id,
                      w.project_id AS project_id,
                      w.scan_enabled AS scan_enabled,
                      w.notes AS notes,
                      o.branch_ref AS branch_ref,
                      o.collection_status AS collection_status,
                      o.staged_count AS staged_count,
                      o.unstaged_count AS unstaged_count,
                      o.untracked_count AS untracked_count,
                      o.observed_at_utc AS observed_at_utc,
                      o.facts_json AS facts_json
                    FROM worktrees w
                    LEFT JOIN observations o ON o.observation_id = (
                      SELECT observation_id FROM observations
                      WHERE worktree_id = w.worktree_id AND project_id = w.project_id
                      ORDER BY observed_at_utc DESC, rowid DESC LIMIT 1
                    )
                    WHERE w.project_id IN ({marks})
                    ORDER BY w.worktree_id
                    """, project_ids,
                )
            )
        except sqlite3.Error:
            state["worktrees"] = []
        state["db_ok"] = True
        return state
    finally:
        conn.close()


def _outcome_zh(outcome: str | None) -> str:
    mapping = {
        "success": "成功",
        "error": "失败",
        "skip": "跳过",
    }
    if not outcome:
        return "尚无"
    return mapping.get(outcome, outcome)


def _daily_stamp(path: Path | None, tz: ZoneInfo, now_local: datetime) -> str:
    if path is None or not path.is_file():
        return "尚无"
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz)
    except OSError:
        return "尚无"
    clock = mtime.strftime("%H:%M")
    if mtime.date() == now_local.date():
        return f"今天 {clock}"
    if (now_local.date() - mtime.date()).days == 1:
        return f"昨天 {clock}"
    return f"{mtime.strftime('%Y-%m-%d')} {clock}"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _alerts(state: dict[str, Any], *, stale: bool) -> list[str]:
    """Faults only: unreadable state, stale or failed scans, collection errors.

    Uncommitted work is not a fault and is listed by _dirty_inventory instead.
    """
    items: list[str] = []
    if state.get("db_missing"):
        return ["还没有状态库"]
    if state.get("db_busy"):
        return ["状态文件忙，未能读取"]
    if not state.get("db_ok"):
        return ["状态文件不可读"]
    if stale:
        items.append("扫描结果已过期")
    last_run = state.get("last_run")
    outcome = _row_get(last_run, "outcome")
    err = _row_get(last_run, "last_safe_error")
    if outcome == "error":
        detail = f"：{err}" if err else ""
        items.append(f"最近一次扫描失败{detail}")
    elif outcome == "skip" and err:
        items.append(f"最近一次扫描跳过：{err}")
    if state.get("scan_paused"):
        items.append("扫描已暂停")
    for row in state.get("worktrees") or []:
        if not _row_get(row, "scan_enabled"):
            continue
        # A collection error is reported even for a menu-quieted project.
        if _row_get(row, "collection_status") == "error":
            items.append(f"{_tree_label(row)} 采集状态：error")
    return _dedupe(items)


def _sample_names(row: sqlite3.Row | None, limit: int = 2) -> list[str]:
    """Basenames from the stored facts only; never opens a business file."""
    raw = _row_get(row, "facts_json")
    if not isinstance(raw, str):
        return []
    try:
        facts = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(facts, dict):
        return []
    names: list[str] = []
    for key in ("staged", "unstaged", "untracked"):
        value = facts.get(key)
        if not isinstance(value, list):
            continue
        for entry in value:
            if isinstance(entry, str) and entry:
                names.append(PurePosixPath(entry).name or entry)
            if len(names) >= limit:
                return names
    return names


def _tree_label(row) -> str:
    """Branch-prefix trees have hashed ids; show project + branch instead."""
    wid = _row_get(row, "worktree_id") or "unknown"
    if _row_get(row, "notes") == "branch_prefix":
        branch = str(_row_get(row, "branch_ref") or "").removeprefix("refs/heads/")
        if branch:
            return f"{_row_get(row, 'project_id') or wid} · {branch}"
    return wid


def _dirty_inventory(state: dict[str, Any], quiet_projects: set[str]) -> list[str]:
    """Uncommitted-work inventory, skipping projects quieted in config."""
    items: list[str] = []
    for row in state.get("worktrees") or []:
        if not _row_get(row, "scan_enabled"):
            continue
        if (_row_get(row, "project_id") or "") in quiet_projects:
            continue
        staged = int(_row_get(row, "staged_count") or 0)
        unstaged = int(_row_get(row, "unstaged_count") or 0)
        untracked = int(_row_get(row, "untracked_count") or 0)
        if staged + unstaged + untracked == 0:
            continue
        counts = f"staged {staged} / unstaged {unstaged} / untracked {untracked}"
        sample = "、".join(_sample_names(row))
        tail = f"　{sample}" if sample else ""
        items.append(f"{_tree_label(row)} 未提交（{counts}）{tail}")
    return _dedupe(items)


def _facts_by_project(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Merge stored facts_json per project. Never opens a business file."""
    out: dict[str, dict[str, Any]] = {}
    for row in state.get("worktrees") or []:
        if not _row_get(row, "scan_enabled"):
            continue
        pid = _row_get(row, "project_id") or ""
        if not pid:
            continue
        facts = facts_of_row(row)
        merged = out.setdefault(
            pid,
            {
                "last_commit_at": None,
                "recent_subjects": [],
                "module_digest": {"new_modules": [], "gone_modules": [], "changed_modules": []},
            },
        )
        stamp = facts.get("last_commit_at")
        if stamp and (merged["last_commit_at"] is None or str(stamp) > str(merged["last_commit_at"])):
            merged["last_commit_at"] = stamp
        for subject in facts.get("recent_subjects") or []:
            if isinstance(subject, str) and subject and subject not in merged["recent_subjects"]:
                merged["recent_subjects"].append(subject)
        digest = facts.get("module_digest") if isinstance(facts.get("module_digest"), dict) else {}
        for key in ("new_modules", "gone_modules", "changed_modules"):
            bucket = merged["module_digest"][key]
            for name in digest.get(key) or []:
                if isinstance(name, str) and name and name not in bucket:
                    bucket.append(name)
    return out


def _briefing_sections(
    home: Path,
    projects: list[ProjectSpec],
    state: dict[str, Any],
    *,
    now: datetime | None = None,
    cfg=None,
) -> tuple[list[str], list[str], bool]:
    """Prefer the already-written menu briefing; else reconstruct from facts."""
    path = latest_menu_briefing(home)
    if path is not None and cfg is not None:
        try:
            from xiaomao.report_access import read_bound_report
            text = read_bound_report(path, home, cfg, "briefing")
        except (OSError, ValueError, KeyError, TypeError):
            return ["日报摘要范围未核实，请重新生成。"], [], False
        if text:
            today, idle, model_used = parse_menu_briefing(text)
            if today or idle:
                return today, idle, model_used
    facts_map = _facts_by_project(state)
    today: list[str] = []
    idle: list[str] = []
    for project in projects:
        facts = facts_map.get(project.project_id) or {}
        section, sentence = rule_sentence(project, facts, now=now)
        if section == "today":
            today.append(sentence)
        elif section == "idle":
            idle.append(sentence)
    return today, idle, False


def render_menu(home: Path | None = None, *, now: datetime | None = None) -> str:
    home = (home or DEFAULT_HOME).expanduser()
    tz_name = _load_timezone(home)
    tz = _tz(tz_name)
    now_utc = _now_utc(now)
    now_local = now_utc.astimezone(tz)
    cfg = None
    try:
        cfg = load_config(home) if (home / "config.json").is_file() else None
        projects = [p for p in cfg.projects if not project_exclusion_reason(p)] if cfg else []
    except (OSError, ValueError, KeyError, TypeError):
        projects = []
    state = _read_state(home, [p.project_id for p in projects])
    views = [inspect_handoff(home, p.project_id, now=now_utc) for p in projects]

    last_run = state.get("last_run")
    last_success = state.get("last_success")
    last_finished = _parse_utc(_row_get(last_run, "finished_at"))
    last_success_at = _parse_utc(_row_get(last_success, "finished_at"))
    age_s = None if last_finished is None else max(0, int((now_utc - last_finished).total_seconds()))
    success_age_s = (
        None if last_success_at is None else max(0, int((now_utc - last_success_at).total_seconds()))
    )

    stale = True
    if state.get("db_ok") and success_age_s is not None:
        stale = success_age_s > STALE_AFTER_S or last_success_at > now_utc
    if state.get("db_missing") or state.get("db_busy") or not state.get("db_ok"):
        stale = True

    alerts = _alerts(state, stale=stale)
    quiet_projects = {p.project_id for p in projects if p.menu_hide_dirty}
    dirty = _dirty_inventory(state, quiet_projects)
    today, idle, model_used = _briefing_sections(home, projects, state, now=now_utc, cfg=cfg)
    daily = latest_daily(home)
    reports = reports_dir(home)
    scan_outcome = _row_get(last_run, "outcome")
    image_b64 = _head_image()

    # Quiet title: cat head only when the scan is fresh. Stale/error never
    # pretend to be a green light.
    if stale:
        title = "信息已过期"
    elif scan_outcome == "error":
        title = "扫描失败"
    elif age_s is None:
        title = "尚无扫描"
    else:
        title = ""

    enabled_trees = sum(1 for row in state.get("worktrees") or [] if _row_get(row, "scan_enabled"))
    lines = [
        _title_line(title, image_b64),
        "---",
        "今天",
    ]
    if today:
        lines.extend(f"-- {_escape(item)}" for item in today)
    else:
        lines.append("-- 今天没有新的模块变化。")
    lines.append("很久没看")
    if idle:
        lines.extend(f"-- {_escape(item)}" for item in idle)
    else:
        lines.append("-- 没有很久没提交的项目。")
    if not model_used:
        lines.append("本次没有模型解读")

    lines.append("---")
    lines.append(f"扫描：{_escape(_outcome_zh(scan_outcome))} · {enabled_trees} 棵树")

    daily_verified = False
    if daily is not None and daily.is_file() and cfg:
        from xiaomao.report_access import read_bound_report
        try:
            read_bound_report(daily, home, cfg, "daily")
            daily_verified = True
        except (OSError, ValueError):
            pass
    daily_stamp = _daily_stamp(daily, tz, now_local)
    if daily_verified:
        lines.append(f"打开日报 {_escape(daily.stem)}　{_escape(daily_stamp)} | href={file_href(daily)}")
    elif daily is not None:
        lines.append(f"最新日报范围未核实（旧文件保留，需重新生成）　{_escape(daily_stamp)}")
    else:
        lines.append("打开最新日报（尚无文件）")

    if stale:
        lines.append("---")
        lines.append("信息已过期：不要把菜单栏当成当前绿灯")
        if success_age_s is None:
            lines.append("尚无成功扫描记录")
        else:
            lines.append(f"最近成功扫描：{relative_zh(success_age_s)}")

    lines.append("---")
    if alerts:
        lines.append(f"需要处理：{len(alerts)} 项")
        for item in alerts:
            lines.append(f"-- {_escape(item)}")
    else:
        lines.append("需要处理：无")

    lines.append("---")
    lines.append("技术细节")
    lines.append(f"-- 工作区未提交：{len(dirty)} 项")
    for item in dirty:
        lines.append(f"---- {_escape(item)}")
    if quiet_projects:
        lines.append(f"-- 已静音（仍在扫描与日报里）：{_escape('、'.join(sorted(quiet_projects)))}")
    lines.append("-- 交接 / 未核实项")
    if not views:
        lines.append("---- 尚无可读取的个人项目配置")
    pending = [v for v in views if v.path is None]
    for view in views:
        if view.path is None:
            continue
        lines.append(_escape(f"---- {view.project_id}：{view.status}"))
        lines.append(_escape(f"------ {view.reason}"))
        lines.append(_escape(f"------ 生成（UTC）：{view.generated_at or 'unknown'}"))
        lines.append(_escape(f"------ 采集核对截至（UTC）：{view.collected_through or 'unknown'}"))
        payload = base64.urlsafe_b64encode(json.dumps([str(home.resolve()), view.project_id]).encode()).decode()
        reader = Path(__file__).resolve().parents[2] / "scripts" / "read-handoff.py"
        lines.append(
            f"{_escape('------ 查看交接与未核实项：' + str(view.project_id))} | "
            f"bash={shlex.quote(sys.executable)} param1={shlex.quote(str(reader))} "
            f"param2={payload} terminal=true"
        )
    if pending:
        names = "、".join(str(v.project_id) for v in pending)
        lines.append(_escape(f"---- 尚无交接：{names}"))
    lines.append("---- 测试：unknown / 部署及验收：unknown；查询不会更新资料")
    if reports.is_dir():
        lines.append(f"打开报告文件夹 | href={file_href(reports)}")
    else:
        lines.append("打开报告文件夹（尚无目录）")

    lines.append("---")
    footnote = "只读 · 不扫描业务仓 · 不加载模型"
    if state.get("pilot_status"):
        footnote += f" · {state['pilot_status']}"
    if state.get("infer_paused"):
        footnote += " · 推理已暂停"
    lines.append(_escape(footnote))
    lines.append("刷新 | refresh=true")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import os

    del argv
    override = os.environ.get("XIAOMAO_HOME")
    home = Path(override).expanduser() if override else DEFAULT_HOME
    try:
        text = render_menu(home)
    except Exception as exc:
        image = _head_image()
        suffix = "emojize=false symbolize=false"
        if image:
            suffix = f"image={image} {suffix}"
        text = (
            f"信息已过期 | {suffix}\n"
            "---\n"
            f"插件错误：{_escape(type(exc).__name__)}\n"
            "只读入口：不扫描业务仓、不加载模型\n"
            "刷新 | refresh=true\n"
        )
    import sys

    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
