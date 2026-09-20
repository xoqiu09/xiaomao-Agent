from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from xiaomao.config import (
    AppConfig,
    host_identity,
    ollama_reachable,
    volume_present,
    volume_report,
    which,
)
from xiaomao.paths import STORAGE_BUDGET_BYTES, layout
from xiaomao.policy import dir_size_bytes


def _df(path: Path) -> dict[str, Any] | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return {
        "path": str(path),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
    }


def _git_version() -> str | None:
    git = which("git")
    if not git:
        return None
    proc = subprocess.run([git, "--version"], capture_output=True, text=True, timeout=5, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _python_info() -> dict[str, Any]:
    import platform
    import sys

    return {
        "executable": sys.executable,
        "version": platform.python_version(),
    }


def _sqlite_info() -> dict[str, Any]:
    import sqlite3

    fts5 = False
    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        fts5 = True
        c.close()
    except sqlite3.Error:
        fts5 = False
    return {"sqlite_version": sqlite3.sqlite_version, "fts5": fts5}


def _ollama_info(cfg: AppConfig) -> dict[str, Any]:
    path = which("ollama")
    app = Path("/Applications/Ollama.app").exists()
    reachable = ollama_reachable(cfg.ollama_host)
    models_dir = Path(cfg.external.models_dir)
    return {
        "cli": path,
        "app_present": app,
        "host": cfg.ollama_host,
        "reachable": reachable,
        "models_dir": str(models_dir),
        "models_dir_exists": models_dir.exists(),
        "no_cloud_config": cfg.ollama_no_cloud,
        "note": "阶段 1 采集不依赖 Ollama。模型任务以外盘身份校验通过为前提，禁止回退 ~/.ollama/models。",
    }


def _worktree_checks(cfg: AppConfig) -> list[dict[str, Any]]:
    rows = []
    for project in cfg.projects:
        for wt in project.worktrees:
            p = Path(wt.path).expanduser()
            rows.append(
                {
                    "project_id": project.project_id,
                    "worktree_id": wt.worktree_id,
                    "path": str(p),
                    "scan": wt.scan,
                    "exists": p.exists(),
                    "is_dir": p.is_dir(),
                    "notes": wt.notes,
                }
            )
    return rows


def collect_doctor(cfg: AppConfig) -> dict[str, Any]:
    home = Path(cfg.home)
    paths = layout(home)
    data_bytes = dir_size_bytes(home) if home.exists() else 0
    return {
        "host": host_identity(),
        "timezone": cfg.timezone,
        "home": str(home),
        "storage": {
            "home_bytes": data_bytes,
            "budget_bytes": STORAGE_BUDGET_BYTES,
            "internal": _df(Path("/")),
            "external_mount_present": volume_present(cfg),
            "external": _df(Path(cfg.external.mount)) if volume_present(cfg) else None,
            "external_uuid_configured": cfg.external.volume_uuid,
            "external_volume": volume_report(cfg),
        },
        "runtime": {
            "git": _git_version(),
            "python": _python_info(),
            "sqlite": _sqlite_info(),
            "ollama": _ollama_info(cfg),
        },
        "worktrees": _worktree_checks(cfg),
        "layout_exists": {k: v.exists() for k, v in paths.items() if k != "lock"},
        "warnings": _warnings(cfg, data_bytes),
    }


def _warnings(cfg: AppConfig, data_bytes: int) -> list[str]:
    warns = []
    if data_bytes > STORAGE_BUDGET_BYTES:
        warns.append("内置 Xiaomao 数据已超过 2GB 预算")
    vol = volume_report(cfg)
    if not vol["mounted"]:
        warns.append("外盘未挂载：事实采集可继续，模型任务应 deferred，禁止改下到 ~/.ollama/models")
    elif vol["status"] == "mismatch":
        warns.append(
            f"外盘身份不符：期望 {vol['expected_uuid']}，实际 {vol['actual_uuid']}。"
            "拒绝把它当成原来那块盘使用。"
        )
    elif vol["status"] == "unverified":
        warns.append("尚未写入外盘 Volume UUID，卸载后仅靠路径名不够安全")
    elif vol["status"] == "unknown":
        warns.append(f"无法读取外盘 UUID：{vol['note']}")
    if which("ollama") is None:
        warns.append("Ollama 未安装（阶段 1 可接受）")
    for project in cfg.projects:
        for wt in project.worktrees:
            if not Path(wt.path).expanduser().exists():
                warns.append(f"工作树不存在：{wt.worktree_id}")
    return warns


def format_doctor(report: dict[str, Any]) -> str:
    host = report["host"]
    rt = report["runtime"]
    st = report["storage"]
    mem = host.get("memory_bytes")
    mem_s = f"{mem / 1024**3:.0f} GB" if isinstance(mem, int) else "unknown"
    lines = [
        "小猫 doctor",
        "===========",
        f"系统：{host.get('system')} {host.get('release')} {host.get('machine')}",
        f"内存：{mem_s}",
        f"Python：{rt['python']['version']} ({rt['python']['executable']})",
        f"Git：{rt['git'] or 'missing'}",
        f"SQLite：{rt['sqlite']['sqlite_version']}  FTS5={'yes' if rt['sqlite']['fts5'] else 'no'}",
        f"Ollama CLI：{rt['ollama']['cli'] or 'missing'}",
        f"Ollama 可达：{rt['ollama']['reachable']}",
        f"模型目录：{rt['ollama']['models_dir']} exists={rt['ollama']['models_dir_exists']}",
        f"数据目录：{report['home']}",
        f"数据占用：{st['home_bytes']} / {st['budget_bytes']} bytes",
        f"外盘挂载：{st['external_mount_present']}",
        f"外盘身份：{st['external_volume']['status']} "
        f"(actual={st['external_volume']['actual_uuid'] or '—'})",
        "",
        "工作树",
        "------",
    ]
    for wt in report["worktrees"]:
        flag = "scan" if wt["scan"] else "idle"
        exist = "ok" if wt["exists"] else "MISSING"
        lines.append(f"- {wt['worktree_id']} [{flag}] {exist}  {wt['path']}")
    if report["warnings"]:
        lines.append("")
        lines.append("警告")
        lines.append("----")
        for w in report["warnings"]:
            lines.append(f"- {w}")
    else:
        lines.append("")
        lines.append("警告：无")
    lines.append("")
    lines.append("本命令不安装、不下载、不改全局配置。")
    return "\n".join(lines) + "\n"
