from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from xiaomao import __version__
from xiaomao.config import VolumeIdentityError, load_config, save_config
from xiaomao.collect import register_project, scan_authorized
from xiaomao.doctor import collect_doctor, format_doctor
from xiaomao.paths import default_home, ensure_layout, layout
from xiaomao.render import default_report_path, status_text, write_project_report, authorized_rows
from xiaomao.scope import project_exclusion_reason
from xiaomao.lock import ScanLock
from xiaomao.store import (
    insert_event,
    insert_scan_run,
    last_scan_success,
    latest_by_project,
    latest_summary_for_range,
    open_db,
    utc_now,
)


def _home_from_args(ns: argparse.Namespace) -> Path:
    if getattr(ns, "home", None):
        return Path(ns.home).expanduser().resolve()
    return default_home()


def _is_formal_home(home: Path) -> bool:
    try:
        return home.resolve() == default_home().resolve()
    except OSError:
        return False


def cmd_doctor(ns: argparse.Namespace) -> int:
    home = _home_from_args(ns)
    cfg = load_config(home)
    report = collect_doctor(cfg)
    if ns.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(format_doctor(report))
    return 0 if not report["warnings"] else 0


def cmd_init(ns: argparse.Namespace) -> int:
    home = _home_from_args(ns)
    paths = ensure_layout(home)
    cfg = load_config(home)
    cfg.home = str(home)
    save_config(cfg)
    with open_db(paths["db"]) as conn:
        for project in cfg.projects:
            register_project(conn, cfg, project)
    os.chmod(home, 0o700)
    sys.stdout.write(f"initialized {home}\n")
    sys.stdout.write(f"config {paths['config']}\n")
    sys.stdout.write(f"db {paths['db']}\n")
    return 0


def cmd_scan(ns: argparse.Namespace) -> int:
    home = _home_from_args(ns)
    ensure_layout(home)
    cfg = load_config(home)
    cfg.home = str(home)
    save_config(cfg)
    dest = None
    project_id = ns.project
    try:
        with ScanLock(layout(home)["lock"]), open_db(layout(home)["db"]) as conn:
            by_project = scan_authorized(conn, cfg, project_id)
            if ns.write_report:
                if not project_id:
                    raise RuntimeError("--write-report 需要 --project")
                if reason := project_exclusion_reason(cfg.project(project_id)):
                    raise ValueError(reason)
                rows = authorized_rows(conn, cfg.project(project_id))
                dest = write_project_report(
                    cfg, cfg.project(project_id), rows, default_report_path(cfg, project_id)
                )
    except RuntimeError as exc:
        if "another xiaomao scan holds" not in str(exc):
            raise
        skip_project = project_id or (cfg.projects[0].project_id if cfg.projects else "website")
        try:
            with open_db(layout(home)["db"]) as conn:
                insert_scan_run(
                    conn,
                    {
                        "run_id": f"run_{uuid.uuid4().hex[:16]}",
                        "project_id": skip_project,
                        "started_at": utc_now(),
                        "finished_at": utc_now(),
                        "outcome": "skip",
                        "last_safe_error": "lock_busy",
                        "inserted": 0,
                        "unchanged": 0,
                        "gap_since_last_success_s": None,
                        "last_observation_id": None,
                    },
                )
                insert_event(
                    conn,
                    kind="scan_skip",
                    project_id=skip_project,
                    payload={"reason": "lock_busy"},
                )
        except Exception:
            pass
        json.dump(
            {
                "project": project_id,
                "results": [],
                "report": None,
                "outcome": "skip",
                "error": "lock_busy",
            },
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        sys.stdout.write("\n")
        return 3
    if project_id:
        payload = {
            "project": project_id,
            "results": by_project.get(project_id, []),
            "report": str(dest) if dest else None,
        }
    else:
        payload = {"projects": by_project, "report": str(dest) if dest else None}
    results = [row for rows in by_project.values() for row in rows]
    failed = not results or any(row.get("status") not in ("ok", "unchanged") for row in results)
    payload["outcome"] = "incomplete" if failed else "success"
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 3 if failed else 0


def cmd_status(ns: argparse.Namespace) -> int:
    home = _home_from_args(ns)
    cfg = load_config(home)
    if reason := project_exclusion_reason(cfg.project(ns.project)):
        sys.stderr.write(f"范围已排除：{reason}\n")
        return 3
    db = layout(home)["db"]
    if not db.exists():
        sys.stderr.write("数据库不存在。先运行：xiaomao init 或 xiaomao scan\n")
        return 2
    with open_db(db) as conn:
        text = status_text(conn, cfg, ns.project)
        if ns.write_report:
            rows = authorized_rows(conn, cfg.project(ns.project))
            dest = write_project_report(cfg, cfg.project(ns.project), rows, default_report_path(cfg, ns.project))
            sys.stdout.write(text)
            sys.stdout.write(f"\n写入 {dest}\n")
        else:
            sys.stdout.write(text)
    return 0


def _maybe_model_note(
    ns: argparse.Namespace,
    cfg,
    conn,
    project_id: str | None,
    *,
    scheduled: bool = False,
) -> tuple[str | None, bool]:
    with_model = bool(getattr(ns, "with_model", False))
    if not with_model and not scheduled:
        return None, False
    from pathlib import Path as P

    from xiaomao.eval_runner import host_pressure
    from xiaomao.ops import (
        evidence_snapshot_key,
        record_summary_row,
        reuse_summary_note,
        should_call_depth_model,
        storage_over_budget,
    )
    from xiaomao.reports import facts_payload
    from xiaomao.summarize import degrade_note, summarize_or_degrade

    eligible = [p for p in cfg.projects if not project_exclusion_reason(p)]
    if not eligible:
        return None, False
    pid = project_id or eligible[0].project_id
    if project_exclusion_reason(cfg.project(pid)):
        return None, False
    from xiaomao.handoff_view import snapshot, scan_covers_scope

    current = snapshot(conn, cfg.project(pid))
    if not current["scan"] or not scan_covers_scope(current["scan"], current["observations"], cfg.project(pid)):
        return None, False
    facts = facts_payload(cfg, conn, pid)
    if not any(w.get("observation_id") for w in facts["worktrees"]):
        return None, False
    call, reason = should_call_depth_model(conn, facts, with_model=with_model, scheduled=scheduled)
    if not call:
        if reason.startswith("dedup:"):
            prior = latest_summary_for_range(conn, pid, evidence_snapshot_key(facts))
            if prior is not None:
                return reuse_summary_note(prior), prior["validation_status"] == "pass"
        if reason == "infer_paused":
            return degrade_note("推理已暂停"), False
        if reason == "no_new_evidence":
            return None, False
        if reason == "rules_only":
            return None, False
        return degrade_note(reason), False

    if storage_over_budget(P(cfg.home)):
        insert_event(conn, kind="storage_over_budget", project_id=pid)
        return degrade_note("内置数据超过 2GB 预算，本轮不写新的模型正文"), False

    client = None
    try:
        from xiaomao.ollama_runtime import OllamaClient, inspect_running, models_dir_allowed

        models_dir_allowed(cfg)
        info = inspect_running(cfg)
        if info["reachable"]:
            client = OllamaClient(cfg)
    except Exception:
        client = None

    before = host_pressure()
    result = summarize_or_degrade(cfg, facts, client=client)
    after = host_pressure()
    loaded_after: list[str] | None = None
    try:
        from xiaomao.ollama_runtime import api_get

        ps = api_get(cfg, "/api/ps", timeout=2.0)
        models = (ps or {}).get("models") or []
        loaded_after = [m.get("name") or m.get("model") for m in models if isinstance(m, dict)]
    except Exception:
        loaded_after = None
    insert_event(
        conn,
        kind="host_pressure",
        project_id=pid,
        payload={"before": before, "after": after, "loaded_after": loaded_after},
    )
    insert_event(
        conn,
        kind="model_unload",
        project_id=pid,
        payload={"loaded_after": loaded_after, "keep_alive": 0},
    )
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    model = (raw.get("model") if raw else None) or (
        cfg.depth_candidates[0] if cfg.depth_candidates else cfg.lite_model
    )
    if client is not None:
        record_summary_row(conn, facts=facts, result=result, model=str(model))
    return result.get("model_note"), bool(result.get("ok"))


def cmd_daily(ns: argparse.Namespace) -> int:
    from xiaomao.reports import local_today, write_daily

    home = _home_from_args(ns)
    ensure_layout(home)
    cfg = load_config(home)
    cfg.home = str(home)
    cfg.projects = [p for p in cfg.projects if not project_exclusion_reason(p)]
    if not cfg.projects:
        sys.stderr.write("尚无可读取的个人项目；未生成日报、未加载模型。\n")
        return 3
    date = ns.date or local_today(cfg)
    scheduled = bool(getattr(ns, "scheduled", False))
    with ScanLock(layout(home)["lock"], retries=5, retry_s=1.0), open_db(layout(home)["db"]) as conn:
        note, model_ok = _maybe_model_note(ns, cfg, conn, None, scheduled=scheduled)
        dest = write_daily(cfg, conn, date=date, model_note=note, model_ok=model_ok)
    sys.stdout.write(f"{dest}\n")
    return 0


def cmd_handoff(ns: argparse.Namespace) -> int:
    from xiaomao.reports import write_handoff
    from xiaomao.scope import project_exclusion_reason
    from xiaomao.handoff_view import inspect_handoff

    home = _home_from_args(ns)
    ensure_layout(home)
    cfg = load_config(home)
    cfg.home = str(home)
    if reason := project_exclusion_reason(cfg.project(ns.project)):
        sys.stderr.write(f"范围已排除：{reason}\n")
        return 3
    with ScanLock(layout(home)["lock"], retries=5, retry_s=1.0), open_db(layout(home)["db"]) as conn:
        note, _ok = _maybe_model_note(ns, cfg, conn, ns.project, scheduled=False)
        dest = write_handoff(cfg, conn, ns.project, model_note=note)
    sys.stdout.write(f"{dest}\n")
    view = inspect_handoff(home, ns.project)
    if view.exit_code:
        sys.stderr.write(view.text())
    return view.exit_code


def cmd_eval(ns: argparse.Namespace) -> int:
    from xiaomao.eval_runner import run_eval
    from xiaomao.eval_samples import samples as load_samples

    home = _home_from_args(ns)
    cfg = load_config(home)
    cfg.home = str(home)
    client = None
    model = ns.model or (cfg.depth_candidates[0] if cfg.depth_candidates else cfg.lite_model)
    if not ns.dry_run:
        from xiaomao.ollama_runtime import OllamaClient, models_dir_allowed

        models_dir_allowed(cfg)
        client = OllamaClient(cfg)
    report = run_eval(cfg, client=client, model=model, selected=load_samples())
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    if report["serious_factual_errors"]:
        return 2
    return 0


def cmd_schedule(ns: argparse.Namespace) -> int:
    from xiaomao.paths import layout as paths_of
    from xiaomao.schedule import (
        install,
        install_daily,
        kickstart,
        kickstart_daily,
        print_daily_job,
        print_job,
        uninstall,
        uninstall_daily,
    )

    home = _home_from_args(ns)
    ensure_layout(home)
    logs = paths_of(home)["logs"]
    logs.mkdir(parents=True, exist_ok=True)
    if ns.action == "install":
        payload = install(home=home, log_dir=logs, project=ns.project)
    elif ns.action == "install-daily":
        payload = install_daily(home=home, log_dir=logs, project=ns.project)
    elif ns.action == "uninstall":
        payload = uninstall()
    elif ns.action == "uninstall-daily":
        payload = uninstall_daily()
    elif ns.action == "kickstart":
        proc = kickstart()
        payload = {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    elif ns.action == "kickstart-daily":
        proc = kickstart_daily()
        payload = {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    elif ns.action == "status":
        proc = print_job()
        payload = {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    elif ns.action == "status-daily":
        proc = print_daily_job()
        payload = {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    else:
        sys.stderr.write(f"unknown schedule action: {ns.action}\n")
        return 2
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if payload.get("bootstrap_returncode", payload.get("returncode", 0)) in (0, None) else 1


def cmd_storage(ns: argparse.Namespace) -> int:
    from xiaomao.ops import storage_over_budget
    from xiaomao.paths import STORAGE_BUDGET_BYTES
    from xiaomao.policy import dir_size_bytes

    home = _home_from_args(ns)
    used = dir_size_bytes(home)
    over = storage_over_budget(home)
    payload = {
        "home": str(home),
        "used_bytes": used,
        "budget_bytes": STORAGE_BUDGET_BYTES,
        "over_budget": over,
        "dry_run": True,
        "action": (
            "over_budget — 限制新的模型正文，不自动删除模型或历史，不碰业务文件"
            if over
            else "none — 只统计，不删除"
        ),
    }
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_pause(ns: argparse.Namespace) -> int:
    from xiaomao.ops import pause_infer, pause_scan_flag
    from xiaomao.schedule import pause_agents

    home = _home_from_args(ns)
    ensure_layout(home)
    with open_db(layout(home)["db"]) as conn:
        if ns.target == "infer":
            pause_infer(conn)
            sys.stdout.write("推理已暂停（不加载模型；扫描仍可运行）\n")
        else:
            pause_scan_flag(conn)
            extra = ""
            if _is_formal_home(home):
                info = pause_agents()
                extra = (
                    f" launchctl bootout scan={info.get('scan_bootout')} "
                    f"daily={info.get('daily_bootout')}"
                )
            sys.stdout.write(f"扫描已暂停（scan_paused=1）。{extra}\n")
    return 0


def cmd_resume(ns: argparse.Namespace) -> int:
    from xiaomao.ops import resume_infer, resume_scan_flag
    from xiaomao.schedule import resume_agents

    home = _home_from_args(ns)
    ensure_layout(home)
    with open_db(layout(home)["db"]) as conn:
        if ns.target == "infer":
            resume_infer(conn)
            sys.stdout.write("推理已恢复\n")
        else:
            resume_scan_flag(conn)
            extra = ""
            if _is_formal_home(home):
                info = resume_agents()
                extra = (
                    f" launchctl bootstrap scan={info.get('scan_bootstrap')} "
                    f"daily={info.get('daily_bootstrap')}"
                )
            sys.stdout.write(f"扫描已恢复（scan_paused=0）。{extra}\n")
    return 0


def cmd_latest(ns: argparse.Namespace) -> int:
    from xiaomao.ops import latest_report

    home = _home_from_args(ns)
    if ns.kind == "handoff":
        from xiaomao.handoff_view import inspect_handoff

        view = inspect_handoff(home, ns.project)
        sys.stdout.write(view.text(include_body=getattr(ns, "print", False)))
        return view.exit_code
    path = latest_report(home, ns.kind, getattr(ns, "project", "website") or "website")
    if path is None:
        sys.stderr.write(f"没有找到 {ns.kind} 报告\n")
        return 2
    from xiaomao.report_access import read_bound_report

    try:
        body = read_bound_report(path, home, load_config(home), ns.kind, ns.project if ns.kind == "status" else None)
    except (OSError, ValueError):
        sys.stderr.write("旧报告范围或来源未核实；请重新生成当前授权项目的报告。\n")
        return 3
    sys.stdout.write(f"{path}\n")
    if getattr(ns, "print", False):
        sys.stdout.write(body)
    return 0


def cmd_open(ns: argparse.Namespace) -> int:
    from xiaomao.ops import latest_report

    home = _home_from_args(ns)
    if ns.kind == "handoff":
        from xiaomao.handoff_view import inspect_handoff

        view = inspect_handoff(home, ns.project)
        sys.stdout.write(view.text())
        if view.exit_code:
            return view.exit_code
        path = view.path
    else:
        path = latest_report(home, ns.kind, getattr(ns, "project", "website") or "website")
        if path is not None:
            from xiaomao.report_access import read_bound_report

            try:
                read_bound_report(path, home, load_config(home), ns.kind, ns.project if ns.kind == "status" else None)
            except (OSError, ValueError):
                sys.stderr.write("旧报告范围或来源未核实；未打开。\n")
                return 3
    if path is None:
        sys.stderr.write(f"没有找到 {ns.kind} 报告\n")
        return 2
    sys.stdout.write(f"{path}\n")
    opener = "/usr/bin/open" if Path("/usr/bin/open").exists() else None
    if opener:
        proc = subprocess.run(
            [opener, str(path)], capture_output=True, text=True, timeout=10, check=False
        )
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr or "open failed\n")
            return 1
    return 0


def cmd_health(ns: argparse.Namespace) -> int:
    from xiaomao.ops import pilot_info, storage_over_budget
    from xiaomao.paths import STORAGE_BUDGET_BYTES
    from xiaomao.policy import dir_size_bytes
    from xiaomao.schedule import print_daily_job, print_job
    from xiaomao.store import infer_paused, scan_paused

    home = _home_from_args(ns)
    cfg = load_config(home)
    db = layout(home)["db"]
    lines = ["小猫 health", "===========", f"home：{home}", f"version：{__version__}"]
    if not db.exists():
        lines.append("数据库不存在。先运行 xiaomao init")
        sys.stdout.write("\n".join(lines) + "\n")
        return 2
    with open_db(db) as conn:
        last = last_scan_success(conn)
        lines.append(f"最近一次扫描成功：{last['finished_at'] if last else '尚无'}")
        lines.append(f"推理暂停：{'是' if infer_paused(conn) else '否'}")
        lines.append(f"扫描暂停：{'是' if scan_paused(conn) else '否'}")
        info = pilot_info(conn)
        lines.append(f"试运行：{info.get('status') or '未标记'}")
        lines.append(f"  开始：{info.get('started_at') or '—'}")
        lines.append(f"  第 3 天核对：{info.get('mid_date') or '—'}")
        lines.append(f"  第 7 天核对：{info.get('end_date') or '—'}")
        used = dir_size_bytes(home)
        lines.append(f"存储：{used} / {STORAGE_BUDGET_BYTES} bytes over={storage_over_budget(home)}")
    scan_job = print_job()
    daily_job = print_daily_job()
    lines.append(f"LaunchAgent scan：rc={scan_job.returncode}")
    for needle in ("state =", "runs =", "last exit code ="):
        for ln in scan_job.stdout.splitlines():
            if needle in ln:
                lines.append("  " + ln.strip())
    lines.append(f"LaunchAgent daily：rc={daily_job.returncode}")
    for needle in ("state =", "runs =", "last exit code ="):
        for ln in daily_job.stdout.splitlines():
            if needle in ln:
                lines.append("  " + ln.strip())
    try:
        from xiaomao.ollama_runtime import api_get, inspect_running

        running = inspect_running(cfg)
        lines.append(f"Ollama 可达：{running.get('reachable')} host={running.get('host')}")
        ps = api_get(cfg, "/api/ps", timeout=2.0) or {}
        loaded = [
            m.get("name") or m.get("model")
            for m in (ps.get("models") or [])
            if isinstance(m, dict)
        ]
        lines.append(f"当前已加载模型：{loaded or '无（已卸载或不可查）'}")
    except Exception as exc:
        lines.append(f"Ollama：未检查（{type(exc).__name__}）")
    lines.append("")
    lines.append("查看：xiaomao status --project website")
    lines.append("日报：xiaomao latest daily")
    lines.append("Handoff：xiaomao latest handoff --project website")
    lines.append("暂停推理：xiaomao pause infer")
    lines.append("暂停扫描：xiaomao pause scan")
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def cmd_pilot(ns: argparse.Namespace) -> int:
    from xiaomao.ops import mark_pilot_running, pilot_info

    home = _home_from_args(ns)
    ensure_layout(home)
    with open_db(layout(home)["db"]) as conn:
        if ns.action == "start":
            info = mark_pilot_running(conn)
        else:
            info = pilot_info(conn)
    json.dump(info, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="xiaomao", description="小猫：本机只读工程观察员")
    p.add_argument("--home", help="覆盖数据目录（测试用）")
    p.add_argument("--version", action="version", version=f"xiaomao {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="只读预检，不安装")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_doctor)

    i = sub.add_parser("init", help="创建数据目录与空库")
    i.set_defaults(func=cmd_init)

    s = sub.add_parser("scan", help="只读扫描已授权且 scan=true 的工作树")
    s.add_argument("--project", default=None, help="省略则扫描全部已授权项目")
    s.add_argument("--write-report", action="store_true")
    s.set_defaults(func=cmd_scan)

    t = sub.add_parser("status", help="规则渲染当前状态（不调用模型）")
    t.add_argument("--project", required=True)
    t.add_argument("--write-report", action="store_true")
    t.set_defaults(func=cmd_status)

    st = sub.add_parser("storage", help="存储占用（dry-run）")
    st.set_defaults(func=cmd_storage)

    daily = sub.add_parser("daily", help="生成日报（默认规则；--with-model 才尝试本地模型）")
    daily.add_argument("--date", help="YYYY-MM-DD，默认本机时区今天")
    daily.add_argument("--with-model", action="store_true")
    daily.add_argument("--scheduled", action="store_true", help="21:30 调度：无新证据不加载模型")
    daily.set_defaults(func=cmd_daily)

    ho = sub.add_parser("handoff", help="生成交接材料")
    ho.add_argument("--project", required=True)
    ho.add_argument("--with-model", action="store_true")
    ho.set_defaults(func=cmd_handoff)

    ev = sub.add_parser("eval", help="固定样本评测（默认 dry-run 校验器）")
    ev.add_argument("--model", help="覆盖模型标签")
    ev.add_argument("--dry-run", action="store_true", default=False, help="不调用模型，只走降级路径")
    ev.set_defaults(func=cmd_eval)

    sch = sub.add_parser("schedule", help="用户级 LaunchAgent（scan 不加载模型；daily 21:30）")
    sch.add_argument(
        "action",
        choices=[
            "install",
            "uninstall",
            "kickstart",
            "status",
            "install-daily",
            "uninstall-daily",
            "kickstart-daily",
            "status-daily",
        ],
    )
    sch.add_argument("--project", default="website")
    sch.set_defaults(func=cmd_schedule)

    pause = sub.add_parser("pause", help="暂停推理或扫描")
    pause.add_argument("target", choices=["infer", "scan"])
    pause.set_defaults(func=cmd_pause)

    resume = sub.add_parser("resume", help="恢复推理或扫描")
    resume.add_argument("target", choices=["infer", "scan"])
    resume.set_defaults(func=cmd_resume)

    lat = sub.add_parser("latest", help="查最新报告；交接会核对来源、时效与未核实项")
    lat.add_argument("kind", choices=["daily", "handoff", "status"])
    lat.add_argument("--project", help="handoff 多项目时必填")
    lat.add_argument("--print", action="store_true", dest="print")
    lat.set_defaults(func=cmd_latest)

    op = sub.add_parser("open", help="用系统 open 打开最新报告")
    op.add_argument("kind", choices=["daily", "handoff", "status"])
    op.add_argument("--project", help="handoff 多项目时必填")
    op.set_defaults(func=cmd_open)

    he = sub.add_parser("health", help="运行健康与调度状态")
    he.set_defaults(func=cmd_health)

    pi = sub.add_parser("pilot", help="试运行标记")
    pi.add_argument("action", choices=["start", "status"])
    pi.set_defaults(func=cmd_pilot)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    try:
        return int(ns.func(ns))
    except KeyError as exc:
        sys.stderr.write(f"未知项目或记录：{exc}\n")
        return 2
    except VolumeIdentityError as exc:
        sys.stderr.write(f"error: VolumeIdentityError: {exc}\n")
        return 3
    except Exception as exc:
        sys.stderr.write(f"error: {type(exc).__name__}: {exc}\n")
        return 1
