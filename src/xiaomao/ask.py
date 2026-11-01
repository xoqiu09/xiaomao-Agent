"""Answer a question from the stored daily records, with a local model.

Facts come only from saved daily windows: no scan, no source code, no project
documents. The answer must name the days it rests on; an answer without that
is not shown. Nothing is written to the database and no notification is sent.
"""
from __future__ import annotations

import json
import sys
from datetime import date as Date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ASK_DEFAULT_DAYS = 7
ASK_MAX_DAYS = 14
PACKET_CHAR_BUDGET = 12_000
MAX_COMMITS_PER_PROJECT = 12

SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
        "not_in_records": {"type": "boolean"},
    },
    "required": ["answer", "sources", "not_in_records"],
}

SYSTEM = (
    "你是小猫，用户的个人项目进度秘书。只根据用户消息里给出的晚报记录回答问题，"
    "不要猜测，不要补充记录以外的内容。用简体中文白话回答，不超过 8 句，"
    "说出具体做了哪些事，不要只说「有更新」；提到代码时说明它在哪：未提交、仅本机、已推送或已合并。"
    "未提交、未推送的数量以最新一天的记录为准。"
    "sources 填你用到的记录日期（YYYY-MM-DD，必须来自记录）。"
    "记录里没有答案时，not_in_records 填 true，answer 简短说明没有记录。只输出 JSON。"
)


def recent_bundles(conn, *, days: int, until: str) -> list[dict]:
    """The latest stored bundle of each window date in [until - days + 1, until]."""
    start = (Date.fromisoformat(until) - timedelta(days=days - 1)).isoformat()
    latest = {}
    for day, raw in conn.execute(
            "SELECT date, bundle_json FROM daily_windows WHERE date BETWEEN ? AND ? ORDER BY completed_at",
            (start, until)):
        latest[day] = raw
    return [json.loads(latest[day]) for day in sorted(latest)]


def _matches(project: dict, wanted: str | None) -> bool:
    if not wanted:
        return True
    wanted = wanted.casefold()
    return wanted in {str(project.get("project_id", "")).casefold(), str(project.get("display_name", "")).casefold()}


def project_facts(project: dict) -> dict:
    from xiaomao.feature_render import _commit_text, _state_label
    info = project.get("delivery_state") or {}
    states = info.get("commits", {})
    commits = [{"title": _commit_text(c), "where": _state_label(states.get(c["sha"]), info.get("has_remote"))}
               for c in reversed(project.get("commits", []))]
    facts = {"project": project["display_name"], "commits": commits[:MAX_COMMITS_PER_PROJECT],
             "uncommitted_files": info.get("dirty_files", 0), "unpushed_commits": len(info.get("unpushed", []))}
    if len(commits) > MAX_COMMITS_PER_PROJECT:
        facts["more_commits"] = len(commits) - MAX_COMMITS_PER_PROJECT
    if info.get("has_remote") is False:
        facts["no_remote"] = True
    return facts


def build_packet(bundles: list[dict], project: str | None = None) -> list[dict]:
    """Per-day facts the model may use; oldest days drop first to fit the budget."""
    from xiaomao.feature_render import plain_next_steps, plain_summary_line
    days = []
    for bundle in bundles:
        chosen = [p for p in bundle["projects"] if _matches(p, project)]
        if project and not chosen:
            continue
        days.append({"date": bundle["window"]["date"], "summary": plain_summary_line(bundle),
                     "projects": [project_facts(p) for p in chosen if p["changed"]],
                     "no_change": [p["display_name"] for p in chosen if not p["changed"]],
                     "next_steps": [s for s in plain_next_steps(bundle)
                                    if not project or any(s.startswith(p["display_name"] + "：") for p in chosen)][:3]})
    while len(days) > 1 and len(json.dumps(days, ensure_ascii=False)) > PACKET_CHAR_BUDGET:
        days.pop(0)
    return days


def validated(result: dict | None, dates: set[str]) -> dict | None:
    data = (result or {}).get("json")
    if not isinstance(data, dict) or not isinstance(data.get("answer"), str) or not data["answer"].strip():
        return None
    sources = sorted({d for d in data.get("sources") or [] if isinstance(d, str) and d in dates})
    not_found = data.get("not_in_records") is True
    if not sources and not not_found:
        return None
    return {"answer": data["answer"].strip()[:2000], "sources": sources, "not_in_records": not_found}


def answer_question(cfg, question: str, packet: list[dict], client, model: str) -> dict:
    user = json.dumps({"question": question, "records": packet}, ensure_ascii=False)
    try:
        result = client.generate_json(model=model, system=SYSTEM, user=user, schema=SCHEMA)
    except Exception as exc:  # model failure never turns into an answer
        return {"ok": False, "error": f"本地模型调用失败（{type(exc).__name__}）"}
    checked = validated(result, {d["date"] for d in packet})
    if checked is None:
        return {"ok": False, "error": "模型的回答没有注明依据，没有采用"}
    return {"ok": True, "model": model, **checked}


def format_answer(result: dict) -> str:
    if result["not_in_records"] and not result["sources"]:
        return result["answer"] + "\n"
    days = "、".join(f"{int(d[5:7])}月{int(d[8:10])}日" for d in result["sources"])
    return f"{result['answer']}\n\n依据：{days}的晚报 · 模型 {result['model']} · 只用小猫已存的记录\n"


def ask_command(ns, home: Path, *, client_factory=None, today=None, out=sys.stdout, err=sys.stderr) -> int:
    from datetime import datetime, timezone
    from xiaomao.config import load_config
    from xiaomao.paths import layout
    from xiaomao.store import infer_paused, open_db_readonly
    cfg = load_config(home)
    cfg.home = str(home)
    allowed = list(dict.fromkeys([cfg.ask_model, *cfg.depth_candidates, cfg.lite_model]))
    model = ns.model or cfg.ask_model
    if model not in allowed:
        err.write(f"只能使用配置里登记的本地模型：{'、'.join(allowed)}\n")
        return 2
    days = max(1, min(ns.days or ASK_DEFAULT_DAYS, ASK_MAX_DAYS))
    today = today or datetime.now(timezone.utc).astimezone(ZoneInfo(cfg.timezone)).date().isoformat()
    db = layout(home)["db"]
    if not db.exists():
        err.write("还没有小猫的记录。\n")
        return 2
    with open_db_readonly(db) as conn:
        if infer_paused(conn):
            err.write("推理已暂停，没有调用模型。可用 xiaomao resume infer 恢复。\n")
            return 3
        packet = build_packet(recent_bundles(conn, days=days, until=today), ns.project)
    if not packet:
        err.write(f"最近 {days} 天还没有{'这个项目的' if ns.project else ''}晚报记录。\n")
        return 2
    started = None
    if client_factory is None:
        client, started = _local_client(cfg)
        if client is None:
            err.write("本地模型没有运行，也没能从外盘启动。可以先运行 scripts/user_serve_ollama.sh。\n")
            return 3
    else:
        client = client_factory(cfg)
    try:
        result = answer_question(cfg, ns.question, packet, client, model)
    finally:
        if started is not None:
            started.terminate()
    if not result["ok"]:
        err.write(result["error"] + "。可以看晚报：xiaomao latest daily --print\n")
        return 3
    out.write(format_answer(result))
    return 0


def _local_client(cfg):
    """A client for the running Ollama, or one started just for this question."""
    from xiaomao.ollama_runtime import OllamaClient, inspect_running, models_dir_allowed, start_serve
    try:
        models_dir_allowed(cfg)
        if inspect_running(cfg)["reachable"]:
            return OllamaClient(cfg), None
        started = start_serve(cfg)
    except Exception:
        return None, None
    return (OllamaClient(cfg), started) if started is not None else (None, None)
