#!/usr/bin/env python3.11
"""Reproducible v0.1 acceptance. Prints evidence; exit 0 only if current gates pass.

Does not claim 3–7 day stability. Does not write business repos.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DEFAULT_HOME = Path.home() / "Library" / "Application Support" / "Xiaomao"
WEBSITE = Path("/Users/xiuqiu/WorkSpace/theAIapp-service")


def run(argv: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged["PYTHONPATH"] = str(SRC)
    if env:
        merged.update(env)
    return subprocess.run(argv, cwd=str(cwd or ROOT), text=True, capture_output=True, env=merged)


def git_state(repo: Path) -> dict[str, str]:
    def g(*args: str) -> str:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        return proc.stdout.strip()

    index = repo / ".git" / "index"
    return {
        "head": g("rev-parse", "HEAD"),
        "branch": g("symbolic-ref", "-q", "HEAD"),
        "status": g("status", "--porcelain"),
        "index_mtime": str(int(index.stat().st_mtime)) if index.exists() else "",
        "index_mtime_ns": str(index.stat().st_mtime_ns) if index.exists() else "",
    }


def main() -> int:
    report: dict = {"gates": {}, "ok": True}

    tests = run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    report["unittest"] = {
        "returncode": tests.returncode,
        "tail": "\n".join((tests.stderr + tests.stdout).splitlines()[-8:]),
    }
    report["gates"]["unittest"] = tests.returncode == 0
    if tests.returncode != 0:
        report["ok"] = False

    home = Path(os.environ.get("XIAOMAO_HOME") or DEFAULT_HOME)
    doctor = run([sys.executable, "-m", "xiaomao", "--home", str(home), "doctor", "--json"])
    doctor_json = {}
    try:
        doctor_json = json.loads(doctor.stdout)
    except json.JSONDecodeError:
        report["ok"] = False
    report["doctor"] = {
        "returncode": doctor.returncode,
        "home": doctor_json.get("home"),
        "volume": (doctor_json.get("storage") or {}).get("external_volume"),
        "warnings": doctor_json.get("warnings"),
        "db_exists": (home / "xiaomao.sqlite").exists(),
    }
    vol = report["doctor"]["volume"] or {}
    report["gates"]["volume_match"] = vol.get("status") == "match"
    report["gates"]["formal_home"] = str(home) == str(DEFAULT_HOME) and report["doctor"]["db_exists"]
    if not report["gates"]["volume_match"] or not report["gates"]["formal_home"]:
        report["ok"] = False

    before = git_state(WEBSITE) if WEBSITE.exists() else {"missing": "1"}
    scan1 = run([sys.executable, "-m", "xiaomao", "--home", str(home), "scan", "--project", "website"])
    scan2 = run([sys.executable, "-m", "xiaomao", "--home", str(home), "scan", "--project", "website"])
    after = git_state(WEBSITE) if WEBSITE.exists() else {"missing": "1"}
    report["scan"] = {"first": scan1.stdout, "second": scan2.stdout, "err": scan1.stderr + scan2.stderr}
    report["website_before"] = before
    report["website_after"] = after
    report["gates"]["business_unchanged"] = before == after
    try:
        second = json.loads(scan2.stdout)
        report["gates"]["scan_idempotent"] = all(
            item.get("status") == "unchanged" or item.get("inserted") is False
            for item in second.get("results") or []
        )
    except json.JSONDecodeError:
        report["gates"]["scan_idempotent"] = False
        report["ok"] = False
    if not report["gates"]["business_unchanged"] or not report["gates"]["scan_idempotent"]:
        report["ok"] = False

    daily = run([sys.executable, "-m", "xiaomao", "--home", str(home), "daily"])
    handoff = run(
        [sys.executable, "-m", "xiaomao", "--home", str(home), "handoff", "--project", "website"]
    )
    report["daily"] = daily.stdout.strip()
    report["handoff"] = handoff.stdout.strip()
    report["gates"]["daily"] = daily.returncode == 0 and bool(daily.stdout.strip())
    report["gates"]["handoff"] = handoff.returncode == 0 and bool(handoff.stdout.strip())
    if not report["gates"]["daily"] or not report["gates"]["handoff"]:
        report["ok"] = False

    git_dir = ROOT / ".git"
    report["gates"]["repo_git"] = git_dir.is_dir()
    if git_dir.is_dir():
        head = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True
        )
        report["commit"] = head.stdout.strip()
    else:
        report["commit"] = None
        report["ok"] = False
        report["blocker"] = "USER_ACTION_REQUIRED: git init blocked by sandbox protected .git path"

    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
