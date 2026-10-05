#!/usr/bin/env python3.11
"""Run isolated regression acceptance, never the installed pilot's live gates.

Both no arguments and --isolated create a disposable HOME, Xiaomao home and
sample Git worktree. Exit 0 means the isolated gates passed. Formal-home and
external-volume acceptance remain NOT_RUN; this is not stability evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PROJECT = "acceptance-sample"
WORKTREE = "acceptance-sample-linked"
ISOLATED_GATES = ("unittest", "business_unchanged", "scan_idempotent", "daily", "handoff", "repo_git")
GIT_OPTIONS = [
    "--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
    "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
]


def isolated_env(root: Path) -> dict[str, str]:
    """Do not inherit live homes, Git overrides, model paths or Python hooks."""
    user = root / "user"
    scratch = root / "tmp"
    user.mkdir()
    scratch.mkdir()
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": "en_US.UTF-8",
        "HOME": str(user),
        "XDG_CONFIG_HOME": str(user / ".config"),
        "XDG_CACHE_HOME": str(user / ".cache"),
        "TMPDIR": str(scratch),
        "XIAOMAO_HOME": str(root / "home"),
        "PYTHONPATH": str(SRC),
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "xiaomao-acceptance",
        "GIT_AUTHOR_EMAIL": "acceptance@example.invalid",
        "GIT_COMMITTER_NAME": "xiaomao-acceptance",
        "GIT_COMMITTER_EMAIL": "acceptance@example.invalid",
    }


def run(argv: list[str], *, env: dict[str, str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    timeout = 600 if argv[1:3] == ["-m", "unittest"] else 180
    try:
        return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", f"command timed out after {timeout} seconds")
    except OSError as exc:
        return subprocess.CompletedProcess(argv, 127, "", f"{type(exc).__name__}: {exc}")


def git(repo: Path, *args: str, env: dict[str, str], check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = run(["git", *GIT_OPTIONS, "-C", str(repo), *args], env=env)
    if check and proc.returncode:
        raise RuntimeError(f"git {args[0]} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc


def file_state(path: Path) -> dict:
    st = path.lstat()
    return {
        "mode": st.st_mode,
        "mtime_ns": st.st_mtime_ns,
        "size": st.st_size,
        "sha256": hashlib.sha256(
            os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
        ).hexdigest(),
    }


def git_state(repo: Path, *, env: dict[str, str]) -> dict:
    if git(repo, "rev-parse", "--is-inside-work-tree", env=env).stdout.strip() != "true":
        raise RuntimeError(f"not a Git worktree: {repo}")
    index_s = git(repo, "rev-parse", "--git-path", "index", env=env).stdout.strip()
    index = (repo / index_s).resolve()
    git_dir = (repo / git(repo, "rev-parse", "--git-dir", env=env).stdout.strip()).resolve()
    branch = git(repo, "symbolic-ref", "-q", "HEAD", env=env, check=False)
    if branch.returncode not in (0, 1):
        raise RuntimeError(f"cannot read branch: {branch.stderr.strip()}")
    return {
        "head": git(repo, "rev-parse", "--verify", "HEAD", env=env).stdout.strip(),
        "branch": branch.stdout.strip() or None,
        "status": git(repo, "status", "--porcelain=v1", "--untracked-files=all", env=env).stdout,
        "git_dir": str(git_dir),
        "index_path": str(index),
        "index": file_state(index),
    }


def tree_state(repo: Path) -> dict:
    """Only used on the synthetic repo, including its index, config and hooks."""
    return {
        str(path.relative_to(repo)): file_state(path)
        for path in sorted(repo.rglob("*"))
        if path.is_file() or path.is_symlink()
    }


def new_report() -> dict:
    return {
        "mode": "isolated",
        "ok_scope": "isolated_regression_only",
        "isolated_ok": False,
        "full_live_acceptance": False,
        "gates": {
            **{key: {"status": "NOT_RUN", "scope": "isolated_regression"} for key in ISOLATED_GATES},
            "formal_home": {"status": "NOT_RUN", "scope": "real_runtime", "reason": "正式 home 未读取或验收"},
            "volume_match": {"status": "NOT_RUN", "scope": "real_runtime", "reason": "真实外盘及模型运行环境未检查"},
        },
        "errors": [],
        "limitations": ["临时样本回归，不代表已安装应用、正式 Pilot 或 3–7 天稳定性通过。"],
    }


def gate(report: dict, name: str, passed: bool, **evidence) -> None:
    report["gates"][name].update(status="PASS" if passed else "FAIL", **evidence)


def command_evidence(proc: subprocess.CompletedProcess[str]) -> dict:
    return {"argv": proc.args, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def create_sample(root: Path, env: dict[str, str]) -> tuple[Path, Path]:
    repo = root / "sample"
    linked = root / "sample-linked"
    repo.mkdir()
    git(repo, "init", "-b", "main", env=env)
    (repo / "README.md").write_text("Personal acceptance fixture.\n", encoding="utf-8")
    git(repo, "add", "README.md", env=env)
    git(repo, "commit", "-m", "Create acceptance fixture", env=env)
    git(repo, "worktree", "add", "-b", "acceptance-linked", str(linked), env=env)
    (linked / "README.md").write_text("Uncommitted fixture edit.\n", encoding="utf-8")
    (linked / "staged.txt").write_text("Staged fixture.\n", encoding="utf-8")
    git(linked, "add", "staged.txt", env=env)
    (linked / "untracked.txt").write_text("Untracked fixture.\n", encoding="utf-8")
    home = root / "home"
    home.mkdir()
    # Every path is explicit: no default project list or external-volume lookup.
    config = {
        "schema_version": 1, "timezone": "UTC", "policy_version": "1", "home": str(home),
        "ollama_host": "127.0.0.1:1", "ollama_no_cloud": True,
        "external": {
            "mount": str(root / "unused-external"),
            "models_dir": str(root / "unused-external" / "models"),
            "data_dir": str(root / "unused-external" / "data"),
            "volume_uuid": None,
        },
        "projects": [{
            "project_id": PROJECT, "display_name": "Personal acceptance sample",
            "approved_root": str(linked), "report_dirs": [],
            "worktrees": [{"worktree_id": WORKTREE, "path": str(linked), "scan": True}],
        }],
    }
    (home / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return repo, linked


def scan_errors(first: subprocess.CompletedProcess[str], second: subprocess.CompletedProcess[str], head: str) -> list[str]:
    errors = []
    rows = []
    for label, proc, status, inserted in (("first", first, "ok", True), ("second", second, "unchanged", False)):
        if proc.returncode:
            errors.append(f"{label}: nonzero returncode {proc.returncode}")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            errors.append(f"{label}: invalid JSON")
            continue
        if not isinstance(payload, dict) or payload.get("project") != PROJECT:
            errors.append(f"{label}: unexpected project/payload")
            continue
        if payload.get("error") or payload.get("outcome") not in (None, "success"):
            errors.append(f"{label}: scan reported error/skip")
        results = payload.get("results")
        if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
            errors.append(f"{label}: expected one nonempty authorized result")
            continue
        row = results[0]
        rows.append(row)
        if (row.get("worktree_id") != WORKTREE or row.get("status") != status
                or row.get("inserted") is not inserted or row.get("error")
                or row.get("head_oid") != head or not row.get("observation_id") or not row.get("fingerprint")):
            errors.append(f"{label}: invalid observation/status/evidence")
    if len(rows) == 2 and any(rows[0].get(key) != rows[1].get(key) for key in ("observation_id", "fingerprint")):
        errors.append("second scan did not reuse the first observation")
    return errors


def report_evidence(proc: subprocess.CompletedProcess[str], folder: Path, required: tuple[str, ...]) -> dict:
    evidence = command_evidence(proc)
    evidence["valid"] = False
    lines = proc.stdout.strip().splitlines()
    if proc.returncode or len(lines) != 1:
        return evidence
    path = Path(lines[0])
    # Do not read an arbitrary path emitted by a broken command.
    if not path.is_absolute() or path.parent.resolve() != folder.resolve() or path.is_symlink() or not path.is_file():
        return evidence
    body = path.read_text(encoding="utf-8")
    evidence.update(path=str(path), sha256=hashlib.sha256(body.encode()).hexdigest(), size=len(body.encode()))
    evidence["valid"] = bool(body) and all(fragment in body for fragment in required)
    return evidence


def isolated_checks(root: Path, env: dict[str, str], report: dict) -> None:
    source = git_state(ROOT, env=env)
    gate(report, "repo_git", True, source=source)
    repo, linked = create_sample(root, env)
    home = root / "home"
    cli = [sys.executable, "-m", "xiaomao", "--home", str(home)]
    before = git_state(linked, env=env)
    tree_before = {"repository": tree_state(repo), "worktree": tree_state(linked)}
    report["fixture"] = {
        "home": str(home), "repo": str(repo), "worktree": str(linked),
        "git_marker_is_file": (linked / ".git").is_file(), "retained": False,
    }
    initialized = run([*cli, "init"], env=env)
    first = run([*cli, "scan", "--project", PROJECT], env=env)
    second = run([*cli, "scan", "--project", PROJECT], env=env)
    errors = scan_errors(first, second, before["head"])
    if initialized.returncode:
        errors.append(f"init: nonzero returncode {initialized.returncode}")
    db_counts = {}
    db = home / "xiaomao.sqlite"
    if db.is_file():
        with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as conn:
            db_counts["observations"] = conn.execute("SELECT count(*) FROM observations").fetchone()[0]
            db_counts["scan_runs"] = conn.execute(
                "SELECT outcome, inserted, unchanged FROM scan_runs ORDER BY rowid"
            ).fetchall()
        if db_counts != {"observations": 1, "scan_runs": [("success", 1, 0), ("success", 0, 1)]}:
            errors.append("database does not contain one observation and two successful idempotent scans")
    else:
        errors.append("scan database missing")
    gate(report, "scan_idempotent", not errors, errors=errors, init=command_evidence(initialized),
         first=command_evidence(first), second=command_evidence(second), database=db_counts)

    required = (WORKTREE, before["head"][:12], "unknown", "生成时间（UTC）", "本报告由规则程序生成，没有调用本地模型。")
    for kind in ("daily", "handoff"):
        args = [kind] if kind == "daily" else [kind, "--project", PROJECT]
        generated = run([*cli, *args], env=env)
        summary_required = ("各项目功能变化", "仍在推进的功能", "待确认与采集缺口", *required[2:])
        evidence = report_evidence(generated, home / "reports" / kind, summary_required if kind == "daily" else required)
        latest = run([*cli, "latest", kind, "--project", PROJECT], env=env)
        latest_lines = latest.stdout.strip().splitlines()
        if kind == "handoff":
            latest_matches = (latest.returncode == 0 and evidence.get("path") is not None
                              and f"来源文件：{evidence['path']}" in latest.stdout
                              and "资料状态：交接可读" in latest.stdout)
        else:
            latest_matches = latest.returncode == 0 and bool(latest_lines) and latest_lines[0] == evidence.get("path")
            details = run([*cli, "latest", "daily", "--details", "--print"], env=env)
            detail_lines = details.stdout.splitlines()
            detail_path = Path(detail_lines[0]) if detail_lines else None
            details_valid = (details.returncode == 0 and detail_path is not None
                             and detail_path.parent.resolve() == (home / "reports/daily-evidence").resolve()
                             and all(fragment in details.stdout for fragment in required))
            evidence["details"] = dict(command_evidence(details), valid=details_valid)
            evidence["valid"] = evidence["valid"] and details_valid
        gate(report, kind, evidence["valid"] and latest_matches and not errors,
             generated=evidence, latest=command_evidence(latest), latest_matches=latest_matches,
             scan_prerequisite_passed=not errors)

    after = git_state(linked, env=env)
    tree_after = {"repository": tree_state(repo), "worktree": tree_state(linked)}
    changed = [
        f"{tree}/{name}"
        for tree in tree_before
        for name in sorted(set(tree_before[tree]) | set(tree_after[tree]))
        if tree_before[tree].get(name) != tree_after[tree].get(name)
    ]
    gate(report, "business_unchanged", before == after and not changed,
         before=before, after=after, changed_fixture_paths=changed)


def run_unit_tests(env: dict[str, str], report: dict) -> None:
    tests = run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], env=env)
    output = tests.stderr + tests.stdout
    count = re.search(r"Ran (\d+) tests? in", output)
    gate(report, "unittest", tests.returncode == 0 and count is not None and int(count[1]) > 0,
         **command_evidence(tests), count=int(count[1]) if count else 0,
         tail="\n".join(tests.stderr.splitlines()[-18:]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated", action="store_true", help="explicitly select the default isolated mode")
    parser.parse_args(argv)
    report = new_report()
    # Isolated fixtures stay in the process TMPDIR (sandbox-safe). Do not reuse
    # caller XIAOMAO_HOME or the live Application Support tree.
    scratch = os.environ.get("TMPDIR") or None
    with tempfile.TemporaryDirectory(prefix="xiaomao-accept-", dir=scratch) as tmp:
        env = isolated_env(Path(tmp))
        for check in (lambda: isolated_checks(Path(tmp), env, report), lambda: run_unit_tests(env, report)):
            try:
                check()
            except Exception as exc:
                report["errors"].append(f"{type(exc).__name__}: {exc}")
    report["isolated_ok"] = not report["errors"] and all(
        report["gates"][key]["status"] == "PASS" for key in ISOLATED_GATES
    )
    report["ok"] = report["isolated_ok"]
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if report["isolated_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
