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

LABEL = "ai.xiaomao.scan"
START_INTERVAL_SECONDS = 300  # 5 minutes; architecture handoff §9
PLIST_NAME = f"{LABEL}.plist"


def default_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / PLIST_NAME


def python_executable() -> str:
    return os.environ.get("XIAOMAO_PYTHON") or "/Users/xiuqiu/.local/bin/python3.11"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def scan_program_argv(*, home: Path, project: str = "website") -> list[str]:
    script = repo_root() / "scripts" / "xiaomao-scan.sh"
    return ["/bin/sh", str(script), project]


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


def kickstart() -> subprocess.CompletedProcess[str]:
    uid = os.getuid()
    return subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{uid}/{LABEL}"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def print_job() -> subprocess.CompletedProcess[str]:
    uid = os.getuid()
    return subprocess.run(
        ["launchctl", "print", f"gui/{uid}/{LABEL}"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


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
