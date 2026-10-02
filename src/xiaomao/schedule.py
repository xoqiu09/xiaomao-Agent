"""User-level LaunchAgent for xiaomao scans. Never a system daemon.

Approved cadence (architecture handoff §9): light collection every 5 minutes.
The scheduled job only runs `scan` (rules). It does not load a model.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path
from typing import Any

from xiaomao.ops import DAILY_HOUR, DAILY_MINUTE

LABEL = "ai.xiaomao.scan"
START_INTERVAL_SECONDS = 300  # 5 minutes; architecture handoff §9
PLIST_NAME = f"{LABEL}.plist"
DAILY_LABEL = "ai.xiaomao.daily"
DAILY_PLIST_NAME = f"{DAILY_LABEL}.plist"


def default_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / PLIST_NAME


def python_executable() -> str:
    return os.environ.get("XIAOMAO_PYTHON") or "/Users/xiuqiu/.local/bin/python3.11"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def scan_program_argv(*, home: Path, project: str = "website") -> list[str]:
    script = repo_root() / "scripts" / "xiaomao-scan.sh"
    return ["/bin/sh", str(script), project]


def daily_program_argv(*, home: Path, project: str = "website") -> list[str]:
    script = repo_root() / "scripts" / "xiaomao-daily.sh"
    return ["/bin/sh", str(script), project]


def default_daily_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / DAILY_PLIST_NAME


def plist_payload(*, home: Path, log_dir: Path, project: str = "website") -> dict[str, Any]:
    stdout = str(log_dir / "scan.out.log")
    stderr = str(log_dir / "scan.err.log")
    return {
        "Label": LABEL,
        "ProgramArguments": scan_program_argv(home=home, project=project),
        "StartInterval": START_INTERVAL_SECONDS,
        "RunAtLoad": False,
        "WorkingDirectory": str(repo_root()),
        "StandardOutPath": stdout,
        "StandardErrorPath": stderr,
        "ProcessType": "Background",
        "Nice": 10,
        "EnvironmentVariables": {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/Users/xiuqiu/.local/bin:/opt/homebrew/bin",
            "PYTHONPATH": str(repo_root() / "src"),
            "XIAOMAO_HOME": str(home),
        },
    }


def write_plist(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".plist.tmp")
    with tmp.open("wb") as fh:
        plistlib.dump(payload, fh)
    os.replace(tmp, path)
    os.chmod(path, 0o644)
    return path


def bootout(path: Path) -> subprocess.CompletedProcess[str]:
    uid = os.getuid()
    return subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}", str(path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def bootstrap(path: Path) -> subprocess.CompletedProcess[str]:
    uid = os.getuid()
    return subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def daily_plist_payload(*, home: Path, log_dir: Path, project: str = "website",
                        daily_time: str = "21:30") -> dict[str, Any]:
    stdout = str(log_dir / "daily.out.log")
    stderr = str(log_dir / "daily.err.log")
    return {
        "Label": DAILY_LABEL,
        "ProgramArguments": daily_program_argv(home=home, project=project),
        "StartCalendarInterval": {"Hour": int(daily_time[:2]), "Minute": int(daily_time[3:])},
        "StartInterval": START_INTERVAL_SECONDS,
        "RunAtLoad": True,
        "WorkingDirectory": str(repo_root()),
        "StandardOutPath": stdout,
        "StandardErrorPath": stderr,
        "ProcessType": "Background",
        "Nice": 10,
        "EnvironmentVariables": {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/Users/xiuqiu/.local/bin:/opt/homebrew/bin",
            "PYTHONPATH": str(repo_root() / "src"),
            "XIAOMAO_HOME": str(home),
        },
    }


def _launchctl(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=15, check=False)


def kickstart() -> subprocess.CompletedProcess[str]:
    uid = os.getuid()
    return _launchctl(["launchctl", "kickstart", "-k", f"gui/{uid}/{LABEL}"])


def kickstart_daily() -> subprocess.CompletedProcess[str]:
    uid = os.getuid()
    return _launchctl(["launchctl", "kickstart", "-k", f"gui/{uid}/{DAILY_LABEL}"])


def print_job() -> subprocess.CompletedProcess[str]:
    uid = os.getuid()
    return _launchctl(["launchctl", "print", f"gui/{uid}/{LABEL}"])


def print_daily_job() -> subprocess.CompletedProcess[str]:
    uid = os.getuid()
    return _launchctl(["launchctl", "print", f"gui/{uid}/{DAILY_LABEL}"])


def install(*, home: Path, log_dir: Path, project: str = "website", path: Path | None = None) -> dict[str, Any]:
    path = path or default_plist_path()
    payload = plist_payload(home=home, log_dir=log_dir, project=project)
    write_plist(path, payload)
    bootout(path)  # idempotent; ignore not-loaded
    loaded = bootstrap(path)
    return {
        "path": str(path),
        "label": LABEL,
        "interval": START_INTERVAL_SECONDS,
        "bootstrap_returncode": loaded.returncode,
        "bootstrap_stderr": loaded.stderr.strip()[:400],
        "bootstrap_stdout": loaded.stdout.strip()[:400],
    }


def uninstall(path: Path | None = None) -> dict[str, Any]:
    path = path or default_plist_path()
    result = bootout(path)
    existed = path.exists()
    if existed:
        path.unlink()
    return {
        "path": str(path),
        "bootout_returncode": result.returncode,
        "removed": existed,
    }


def install_daily(*, home: Path, log_dir: Path, project: str = "website", path: Path | None = None) -> dict[str, Any]:
    from xiaomao.config import load_config
    path = path or default_daily_plist_path()
    payload = daily_plist_payload(home=home, log_dir=log_dir, project=project,
                                  daily_time=load_config(home).daily_time)
    write_plist(path, payload)
    bootout(path)
    loaded = bootstrap(path)
    return {
        "path": str(path),
        "label": DAILY_LABEL,
        "calendar": payload["StartCalendarInterval"],
        "bootstrap_returncode": loaded.returncode,
        "bootstrap_stderr": loaded.stderr.strip()[:400],
        "bootstrap_stdout": loaded.stdout.strip()[:400],
    }


def uninstall_daily(path: Path | None = None) -> dict[str, Any]:
    path = path or default_daily_plist_path()
    result = bootout(path)
    existed = path.exists()
    if existed:
        path.unlink()
    return {
        "path": str(path),
        "bootout_returncode": result.returncode,
        "removed": existed,
    }


def pause_agents() -> dict[str, Any]:
    """Unload scan + daily without deleting plists."""
    scan = bootout(default_plist_path())
    daily = bootout(default_daily_plist_path())
    return {
        "scan_bootout": scan.returncode,
        "daily_bootout": daily.returncode,
    }


def resume_agents() -> dict[str, Any]:
    """Load scan + daily from existing plists. Does not rewrite them."""
    out: dict[str, Any] = {}
    scan_path = default_plist_path()
    daily_path = default_daily_plist_path()
    if scan_path.exists():
        loaded = bootstrap(scan_path)
        out["scan_bootstrap"] = loaded.returncode
        out["scan_stderr"] = loaded.stderr.strip()[:200]
    else:
        out["scan_bootstrap"] = None
        out["scan_missing"] = str(scan_path)
    if daily_path.exists():
        loaded = bootstrap(daily_path)
        out["daily_bootstrap"] = loaded.returncode
        out["daily_stderr"] = loaded.stderr.strip()[:200]
    else:
        out["daily_bootstrap"] = None
        out["daily_missing"] = str(daily_path)
    return out
