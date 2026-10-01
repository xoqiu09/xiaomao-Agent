"""Menu sentences: today's modules and long-idle projects, never file lists.

SwiftBar only reads the text this module already wrote, or reconstructs the
same rule sentences from stored facts_json. It does not scan or load a model.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from xiaomao.config import AppConfig, ProjectSpec
from xiaomao.paths import layout
from xiaomao.policy import looks_like_secret, path_is_denied
from xiaomao.render import authorized_rows
from xiaomao.scope import project_exclusion_reason
from xiaomao.store import utc_now

DOC_NAME = "00-项目说明.md"
DOC_CAP_CHARS = 12_000
IDLE_AFTER_DAYS = 14
WALLET_ID = "Wallet-Infrastructure"
SAFE_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
# A module is `internal/funds`. A file leak looks like `wallet/bnb.ts`.
_FILE_PATH = re.compile(r"(?:^|[\s`（(])(?:[\w.-]+/)+\w+\.[A-Za-z0-9]{1,8}\b")


def briefing_dir(cfg: AppConfig) -> Path:
    return layout(Path(cfg.home))["reports_briefing"]


def menu_path(cfg: AppConfig, date: str) -> Path:
    return briefing_dir(cfg) / f"menu-{date}.txt"


def project_briefing_path(cfg: AppConfig, project_id: str, date: str) -> Path:
    if not SAFE_PROJECT_ID.fullmatch(project_id):
        raise ValueError("invalid project ID")
    return briefing_dir(cfg) / f"{project_id}-{date}.txt"


def _parse_utc(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
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


def idle_days(last_commit_at: str | None, now: datetime | None = None) -> int | None:
    stamp = _parse_utc(last_commit_at)
    if stamp is None:
        return None
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return max(0, int((now_utc.astimezone(timezone.utc) - stamp).total_seconds() // 86400))


def idle_zh(days: int | None) -> str:
    if days is None:
        return "尚无提交记录"
    if days < 1:
        return "今天有提交"
    if days == 1:
        return "1 天没提交"
    if days < 14:
        return f"{days} 天没提交"
    if days < 45:
        weeks = max(1, days // 7)
        return f"大约 {weeks} 周没提交"
    months = max(1, days // 30)
    if months == 1:
        return "一个多月没提交"
    if months == 2:
        return "两个多月没提交"
    return f"{months} 个月没提交"


def digest_of(facts: dict[str, Any] | None) -> dict[str, list[str]]:
    raw = (facts or {}).get("module_digest") or {}
    if not isinstance(raw, dict):
        return {"new_modules": [], "gone_modules": [], "changed_modules": []}

    def _names(key: str) -> list[str]:
        value = raw.get(key) or []
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if isinstance(item, str) and item]

    return {
        "new_modules": _names("new_modules"),
        "gone_modules": _names("gone_modules"),
        "changed_modules": _names("changed_modules"),
    }


def has_today_modules(facts: dict[str, Any] | None) -> bool:
    digest = digest_of(facts)
    return bool(digest["new_modules"] or digest["gone_modules"] or digest["changed_modules"])


def has_recent_commit(facts: dict[str, Any] | None, now: datetime | None = None) -> bool:
    days = idle_days((facts or {}).get("last_commit_at") if facts else None, now)
    return days is not None and days < 1


def facts_of_row(row: Any) -> dict[str, Any]:
    raw = row["facts_json"] if hasattr(row, "keys") else (row.get("facts_json") if isinstance(row, dict) else None)
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_capped_text(path: Path, *, under: Path, cap: int = DOC_CAP_CHARS) -> str:
    """Read one file under `under`. Never follows a symlink, never leaves the root."""
    try:
        if path.is_symlink() or not path.is_file():
            return ""
        resolved = path.resolve()
        root = under.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return ""
    if path_is_denied(path.name):
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if looks_like_secret(text):
        return ""
    return text[:cap]


def project_doc_path(cfg: AppConfig, project: ProjectSpec) -> Path | None:
    if not SAFE_PROJECT_ID.fullmatch(project.project_id):
        return None
    root = Path(cfg.briefing_docs_root).expanduser()
    return root / project.project_id / DOC_NAME


def collect_project_docs(cfg: AppConfig, project: ProjectSpec, facts: dict[str, Any] | None) -> str:
    """Only the authorized project's 00-项目说明.md and optional README.

    Never lists briefing_docs_root (company folders live there). Wallet docs
    are skipped when there is no today's module digest.
    """
    if project.project_id == WALLET_ID and not has_today_modules(facts):
        return ""
    chunks: list[str] = []
    doc = project_doc_path(cfg, project)
    if doc is not None:
        root = Path(cfg.briefing_docs_root).expanduser()
        text = _read_capped_text(doc, under=root)
        if text:
            chunks.append(text)
    readme = Path(project.approved_root).expanduser() / "README.md"
    text = _read_capped_text(readme, under=Path(project.approved_root).expanduser())
    if text:
        chunks.append(text)
    return "\n\n".join(chunks)[:DOC_CAP_CHARS]


def _join_modules(names: list[str], limit: int = 4) -> str:
    return "、".join(names[:limit])


def rule_sentence(
    project: ProjectSpec,
    facts: dict[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Return (section, sentence). section is 'today' or 'idle' or 'skip'."""
    digest = digest_of(facts)
    subjects = [s for s in (facts.get("recent_subjects") or []) if isinstance(s, str) and s]
    days = idle_days(facts.get("last_commit_at"), now)
    name = project.display_name or project.project_id
    quiet = "（仍在扫描，菜单已静音）" if project.menu_hide_dirty else ""

    if has_today_modules(facts):
        bits: list[str] = []
        if digest["changed_modules"]:
            bits.append("动了 " + _join_modules(digest["changed_modules"]))
        if digest["new_modules"]:
            bits.append("多了 " + _join_modules(digest["new_modules"], 3))
        if digest["gone_modules"]:
            bits.append("少了 " + _join_modules(digest["gone_modules"], 3))
        text = f"{name}：" + "；".join(bits) + "。"
        if subjects:
            text += "提交说明：" + "；".join(subjects[:2]) + "。"
        return "today", text

    if has_recent_commit(facts, now):
        if subjects:
            return "today", f"{name}：今天有提交（{subjects[0]}）。模块目录没有新增或消失。"
        return "today", f"{name}：今天有提交；模块目录没有新增或消失。"

    if days is None:
        return "skip", f"{name}：尚无提交记录"
    if days >= IDLE_AFTER_DAYS:
        return "idle", f"{name}：{idle_zh(days)}{quiet}"
    return "skip", f"{name}：{idle_zh(days)}"


def sections_from_projects(
    projects: list[ProjectSpec],
    facts_by_id: dict[str, dict[str, Any]],
    *,
    now: datetime | None = None,
) -> tuple[list[str], list[str]]:
    today: list[str] = []
    idle: list[str] = []
    for project in projects:
        if project_exclusion_reason(project):
            continue
        facts = facts_by_id.get(project.project_id) or {}
        section, sentence = rule_sentence(project, facts, now=now)
        if section == "today":
            today.append(sentence)
        elif section == "idle":
            idle.append(sentence)
    return today, idle


def facts_by_project(cfg: AppConfig, conn) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for project in cfg.projects:
        if project_exclusion_reason(project):
            continue
        merged: dict[str, Any] = {
            "last_commit_at": None,
            "recent_subjects": [],
            "module_digest": {"new_modules": [], "gone_modules": [], "changed_modules": []},
        }
        for row in authorized_rows(conn, project):
            if not row["scan_enabled"] or not row["observation_id"]:
                continue
            facts = facts_of_row(row)
            stamp = facts.get("last_commit_at")
            if stamp and (merged["last_commit_at"] is None or str(stamp) > str(merged["last_commit_at"])):
                merged["last_commit_at"] = stamp
            for subject in facts.get("recent_subjects") or []:
                if isinstance(subject, str) and subject and subject not in merged["recent_subjects"]:
                    merged["recent_subjects"].append(subject)
            digest = digest_of(facts)
            for key in ("new_modules", "gone_modules", "changed_modules"):
                bucket = merged["module_digest"][key]
                for name in digest[key]:
                    if name not in bucket:
                        bucket.append(name)
        out[project.project_id] = merged
    return out


def render_menu_briefing(
    today: list[str],
    idle: list[str],
    *,
    model_used: bool,
) -> str:
    lines = ["今天"]
    if today:
        lines.extend(f"-- {line}" for line in today)
    else:
        lines.append("-- 今天没有新的模块变化。")
    lines.append("很久没看")
    if idle:
        lines.extend(f"-- {line}" for line in idle)
    else:
        lines.append("-- 没有很久没提交的项目。")
    if not model_used:
        lines.append("本次没有模型解读")
    return "\n".join(lines) + "\n"


def parse_menu_briefing(text: str) -> tuple[list[str], list[str], bool]:
    today: list[str] = []
    idle: list[str] = []
    section: str | None = None
    model_used = "本次没有模型解读" not in text
    for raw in text.splitlines():
        line = raw.strip()
        if line == "今天":
            section = "today"
            continue
        if line == "很久没看":
            section = "idle"
            continue
        if line.startswith("-- "):
            item = line[3:].strip()
            if not item:
                continue
            if section == "today":
                today.append(item)
            elif section == "idle":
                idle.append(item)
    return today, idle, model_used


def contains_file_path(text: str) -> bool:
    return bool(_FILE_PATH.search(text or ""))


def latest_menu_briefing(home: Path) -> Path | None:
    folder = home / "reports" / "briefing"
    if not folder.is_dir():
        return None
    files = sorted(p for p in folder.glob("menu-*.txt") if p.is_file())
    return files[-1] if files else None


def write_menu_briefing(
    cfg: AppConfig,
    conn,
    *,
    date: str,
    client: Any | None = None,
    with_model: bool = False,
) -> Path:
    """Always write rule sentences. Optionally overlay a validated model briefing."""
    dest_dir = briefing_dir(cfg)
    dest_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    by_project = facts_by_project(cfg, conn)
    projects = [p for p in cfg.projects if not project_exclusion_reason(p)]
    today, idle = sections_from_projects(projects, by_project, now=now)
    model_used = False
    model_note = "本 briefing 由规则程序生成，没有调用本地模型。"

    if with_model and client is not None:
        overlay = _try_model_briefing(cfg, projects, by_project, client)
        if overlay is not None:
            today, idle = overlay
            model_used = True
            model_note = "本 briefing 含本地模型短句；模块名单仍以采集程序为准。"

    menu_text = render_menu_briefing(today, idle, model_used=model_used)
    dest = menu_path(cfg, date)
    _atomic_write(dest, menu_text)
    from xiaomao.report_access import bind_report

    bind_report(dest, menu_text, cfg, "briefing")

    for project in projects:
        facts = by_project.get(project.project_id) or {}
        section, sentence = rule_sentence(project, facts, now=now)
        body = (
            f"{project.display_name}（{project.project_id}）\n"
            f"生成时间（UTC）：{utc_now()}\n"
            f"上次提交：{facts.get('last_commit_at') or 'unknown'}\n"
            f"规则句：{sentence}\n"
            f"栏目：{section}\n"
            f"{model_note}\n"
        )
        path = project_briefing_path(cfg, project.project_id, date)
        _atomic_write(path, body)
        bind_report(path, body, cfg, "briefing", project.project_id)
    return dest


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _try_model_briefing(
    cfg: AppConfig,
    projects: list[ProjectSpec],
    by_project: dict[str, dict[str, Any]],
    client: Any,
) -> tuple[list[str], list[str]] | None:
    from xiaomao.summarize import menu_briefing_or_degrade

    allowed = {p.project_id for p in projects}
    docs: dict[str, str] = {}
    for project in projects:
        facts = by_project.get(project.project_id) or {}
        text = collect_project_docs(cfg, project, facts)
        if text:
            docs[project.project_id] = text
    payload = {
        "projects": [
            {
                "project_id": p.project_id,
                "display_name": p.display_name,
                "menu_hide_dirty": p.menu_hide_dirty,
                **(by_project.get(p.project_id) or {}),
            }
            for p in projects
        ]
    }
    result = menu_briefing_or_degrade(cfg, payload, docs=docs, client=client)
    if not result.get("ok"):
        return None
    today: list[str] = []
    idle: list[str] = []
    names = {p.project_id: (p.display_name or p.project_id) for p in projects}
    for item in result.get("today") or []:
        pid = item.get("project_id")
        text = (item.get("text") or "").strip()
        if pid not in allowed or not text or contains_file_path(text):
            continue
        facts = by_project.get(pid) or {}
        if not (has_today_modules(facts) or has_recent_commit(facts)):
            continue
        if not text.startswith(names[pid]):
            text = f"{names[pid]}：{text}"
        today.append(text)
    for item in result.get("idle") or []:
        pid = item.get("project_id")
        text = (item.get("text") or "").strip()
        if pid not in allowed or not text or contains_file_path(text):
            continue
        days = idle_days((by_project.get(pid) or {}).get("last_commit_at"))
        if days is None or days < IDLE_AFTER_DAYS:
            continue
        if not text.startswith(names[pid]):
            text = f"{names[pid]}：{text}"
        idle.append(text)
    if not today and not idle:
        return None
    return today, idle
