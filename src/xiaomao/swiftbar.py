"""Read-only SwiftBar menu for Xiaomao.

Reads existing SQLite + report files. Does not scan worktrees, does not
load a model, does not take the scan lock, and does not write the data dir.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

# Keep in lockstep with xiaomao.ops.GAP_WARN_AFTER_S (two missed 5-minute ticks + 60s).
STALE_AFTER_S = 300 * 2 + 60
DEFAULT_TZ = "Asia/Taipei"
DEFAULT_HOME = Path.home() / "Library" / "Application Support" / "Xiaomao"


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


def _read_state(home: Path) -> dict[str, Any]:
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
    try:
        conn = _connect_ro(db)
    except sqlite3.Error as exc:
        msg = str(exc).lower()
        state["db_busy"] = "busy" in msg or "locked" in msg
        state["error"] = type(exc).__name__
        return state
    try:
        try:
            state["last_run"] = conn.execute(
                """
                SELECT outcome, finished_at, last_safe_error, inserted, unchanged
                FROM scan_runs
                ORDER BY finished_at DESC
                LIMIT 1
                """
            ).fetchone()
            state["last_success"] = conn.execute(
                """
                SELECT outcome, finished_at, last_safe_error, inserted, unchanged
                FROM scan_runs
                WHERE outcome = 'success'
                ORDER BY finished_at DESC
                LIMIT 1
                """
            ).fetchone()
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
                    """
                    SELECT
                      w.worktree_id AS worktree_id,
                      w.scan_enabled AS scan_enabled,
                      o.collection_status AS collection_status,
                      o.staged_count AS staged_count,
                      o.unstaged_count AS unstaged_count,
                      o.untracked_count AS untracked_count,
                      o.observed_at_utc AS observed_at_utc
                    FROM worktrees w
                    LEFT JOIN observations o ON o.observation_id = (
                      SELECT observation_id FROM observations
                      WHERE worktree_id = w.worktree_id
                      ORDER BY observed_at_utc DESC LIMIT 1
                    )
                    ORDER BY w.worktree_id
                    """
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


def _attention(state: dict[str, Any], *, stale: bool) -> list[str]:
    items: list[str] = []
    if state.get("db_missing"):
        items.append("还没有状态库")
        return items
    if state.get("db_busy"):
        items.append("状态文件忙，未能读取")
        return items
    if not state.get("db_ok"):
        items.append("状态文件不可读")
        return items
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
        wt = _row_get(row, "worktree_id") or "unknown"
        status = _row_get(row, "collection_status")
        if status == "error":
            items.append(f"{wt} 采集状态：error")
        dirty = (
            int(_row_get(row, "staged_count") or 0)
            + int(_row_get(row, "unstaged_count") or 0)
            + int(_row_get(row, "untracked_count") or 0)
        )
        if dirty:
            items.append(
                f"{wt} 工作区 dirty"
                f"（staged {_row_get(row, 'staged_count') or 0}"
                f" / unstaged {_row_get(row, 'unstaged_count') or 0}"
                f" / untracked {_row_get(row, 'untracked_count') or 0}）"
            )
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def render_menu(home: Path | None = None, *, now: datetime | None = None) -> str:
    home = (home or DEFAULT_HOME).expanduser()
    tz_name = _load_timezone(home)
    tz = _tz(tz_name)
    now_utc = _now_utc(now)
    now_local = now_utc.astimezone(tz)
    state = _read_state(home)

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
        stale = success_age_s > STALE_AFTER_S
    if state.get("db_missing") or state.get("db_busy") or not state.get("db_ok"):
        stale = True

    attention = _attention(state, stale=stale)
    daily = latest_daily(home)
    handoff = latest_handoff(home)
    reports = reports_dir(home)

    if stale:
        title = "🐱 小猫｜信息已过期"
    elif age_s is None:
        title = "🐱 小猫｜尚无扫描"
    else:
        title = f"🐱 小猫｜上次检查：{relative_zh(age_s)}"

    lines = [
        f"{_escape(title)} | emojize=false symbolize=false",
        "---",
        f"最近一次扫描：{_escape(_outcome_zh(_row_get(last_run, 'outcome')))}",
        f"需要关注：{len(attention)} 项",
        f"日报更新时间：{_escape(_daily_stamp(daily, tz, now_local))}",
    ]

    if stale:
        lines.append("---")
        lines.append("信息已过期：不要把菜单栏当成当前绿灯")
        if success_age_s is None:
            lines.append("尚无成功扫描记录")
        else:
            lines.append(f"最近成功扫描：{relative_zh(success_age_s)}")

    if attention:
        lines.append("---")
        for item in attention:
            lines.append(_escape(item))

    lines.append("---")
    if daily is not None and daily.is_file():
        lines.append(f"打开最新日报 | href={file_href(daily)}")
    else:
        lines.append("打开最新日报（尚无文件）")
    if handoff is not None and handoff.is_file():
        lines.append(f"打开最新 Handoff | href={file_href(handoff)}")
    else:
        lines.append("打开最新 Handoff（尚无文件）")
    if reports.is_dir():
        lines.append(f"打开报告文件夹 | href={file_href(reports)}")
    else:
        lines.append("打开报告文件夹（尚无目录）")

    lines.append("---")
    lines.append("只读入口：不扫描业务仓、不加载模型")
    if state.get("pilot_status"):
        lines.append(f"试运行：{_escape(str(state['pilot_status']))}")
    if state.get("infer_paused"):
        lines.append("推理已暂停")
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
        text = (
            "🐱 小猫｜信息已过期 | emojize=false symbolize=false\n"
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
