from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from xiaomao import __version__
from xiaomao.config import VolumeIdentityError, load_config, save_config
from xiaomao.collect import register_project, scan_project
from xiaomao.doctor import collect_doctor, format_doctor
from xiaomao.paths import default_home, ensure_layout, layout
from xiaomao.render import default_report_path, status_text, write_project_report
from xiaomao.lock import ScanLock
from xiaomao.store import latest_by_project, open_db


def _home_from_args(ns: argparse.Namespace) -> Path:
    if getattr(ns, "home", None):
        return Path(ns.home).expanduser().resolve()
    return default_home()


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
    with ScanLock(layout(home)["lock"]), open_db(layout(home)["db"]) as conn:
        results = scan_project(conn, cfg, ns.project)
        if ns.write_report:
            rows = latest_by_project(conn, ns.project)
            dest = write_project_report(cfg, cfg.project(ns.project), rows, default_report_path(cfg, ns.project))
        else:
            dest = None
    json.dump({"project": ns.project, "results": results, "report": str(dest) if dest else None}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_status(ns: argparse.Namespace) -> int:
    home = _home_from_args(ns)
    cfg = load_config(home)
    db = layout(home)["db"]
    if not db.exists():
        sys.stderr.write("数据库不存在。先运行：xiaomao init 或 xiaomao scan\n")
        return 2
    with open_db(db) as conn:
        text = status_text(conn, cfg, ns.project)
        if ns.write_report:
            rows = latest_by_project(conn, ns.project)
            dest = write_project_report(cfg, cfg.project(ns.project), rows, default_report_path(cfg, ns.project))
            sys.stdout.write(text)
            sys.stdout.write(f"\n写入 {dest}\n")
        else:
            sys.stdout.write(text)
    return 0


def _maybe_model_note(ns: argparse.Namespace, cfg, conn, project_id: str | None) -> str | None:
    if not getattr(ns, "with_model", False):
        return None
    from xiaomao.reports import facts_payload
    from xiaomao.summarize import summarize_or_degrade

    pid = project_id or cfg.projects[0].project_id
    client = None
    try:
        from xiaomao.ollama_runtime import OllamaClient, inspect_running, models_dir_allowed

        models_dir_allowed(cfg)
        info = inspect_running(cfg)
        if info["reachable"]:
            client = OllamaClient(cfg)
    except Exception:
        client = None
    result = summarize_or_degrade(cfg, facts_payload(cfg, conn, pid), client=client)
    return result["model_note"]


def cmd_daily(ns: argparse.Namespace) -> int:
    from xiaomao.reports import local_today, write_daily

    home = _home_from_args(ns)
    ensure_layout(home)
    cfg = load_config(home)
    cfg.home = str(home)
    date = ns.date or local_today(cfg)
    with ScanLock(layout(home)["lock"]), open_db(layout(home)["db"]) as conn:
        note = _maybe_model_note(ns, cfg, conn, None)
        dest = write_daily(cfg, conn, date=date, model_note=note)
    sys.stdout.write(f"{dest}\n")
    return 0


def cmd_handoff(ns: argparse.Namespace) -> int:
    from xiaomao.reports import write_handoff

    home = _home_from_args(ns)
    ensure_layout(home)
    cfg = load_config(home)
    cfg.home = str(home)
    with ScanLock(layout(home)["lock"]), open_db(layout(home)["db"]) as conn:
        note = _maybe_model_note(ns, cfg, conn, ns.project)
        dest = write_handoff(cfg, conn, ns.project, model_note=note)
    sys.stdout.write(f"{dest}\n")
    return 0


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
    from xiaomao.schedule import install, kickstart, print_job, uninstall

    home = _home_from_args(ns)
    ensure_layout(home)
    logs = paths_of(home)["logs"]
    logs.mkdir(parents=True, exist_ok=True)
    if ns.action == "install":
        payload = install(home=home, log_dir=logs, project=ns.project)
    elif ns.action == "uninstall":
        payload = uninstall()
    elif ns.action == "kickstart":
        proc = kickstart()
        payload = {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    elif ns.action == "status":
        proc = print_job()
        payload = {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    else:
        sys.stderr.write(f"unknown schedule action: {ns.action}\n")
        return 2
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if payload.get("bootstrap_returncode", payload.get("returncode", 0)) in (0, None) else 1


def cmd_storage(ns: argparse.Namespace) -> int:
    from xiaomao.policy import dir_size_bytes
    from xiaomao.paths import STORAGE_BUDGET_BYTES

    home = _home_from_args(ns)
    used = dir_size_bytes(home)
    payload = {
        "home": str(home),
        "used_bytes": used,
        "budget_bytes": STORAGE_BUDGET_BYTES,
        "dry_run": True,
        "action": "none — 阶段 1 只统计，不删除",
    }
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
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
    s.add_argument("--project", required=True)
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
    daily.set_defaults(func=cmd_daily)

    ho = sub.add_parser("handoff", help="生成交接材料")
    ho.add_argument("--project", required=True)
    ho.add_argument("--with-model", action="store_true")
    ho.set_defaults(func=cmd_handoff)

    ev = sub.add_parser("eval", help="固定样本评测（默认 dry-run 校验器）")
    ev.add_argument("--model", help="覆盖模型标签")
    ev.add_argument("--dry-run", action="store_true", default=False, help="不调用模型，只走降级路径")
    ev.set_defaults(func=cmd_eval)

    sch = sub.add_parser("schedule", help="用户级 LaunchAgent（仅 scan，不加载模型）")
    sch.add_argument("action", choices=["install", "uninstall", "kickstart", "status"])
    sch.add_argument("--project", default="website")
    sch.set_defaults(func=cmd_schedule)
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
