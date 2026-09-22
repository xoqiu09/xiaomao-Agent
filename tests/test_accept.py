from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import accept


class AcceptanceTests(unittest.TestCase):
    def invoke(self, fault=None, *, test_returncode: int = 0, argv=None) -> tuple[int, dict]:
        """Keep the real Git/CLI processes; stub nested unittest to avoid recursion."""
        real_run = accept.run

        def execute(command, **kwargs):
            if command[1:3] == ["-m", "unittest"]:
                return subprocess.CompletedProcess(command, test_returncode, "", "Ran 1 test in 0.001s\n")
            proc = real_run(command, **kwargs)
            return fault(command, proc) if fault else proc

        output = io.StringIO()
        with patch.object(accept, "run", side_effect=execute), redirect_stdout(output):
            rc = accept.main([] if argv is None else argv)
        return rc, json.loads(output.getvalue())

    def test_default_isolated_worktree_and_real_cli_chain(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        live = root / "caller-home"
        live.mkdir()
        marker = live / "config.json"
        marker.write_text("do not read or change caller configuration\n")
        with patch.dict(os.environ, {"HOME": str(live), "XIAOMAO_HOME": str(live), "TMPDIR": str(live)}):
            rc, report = self.invoke()
        self.assertEqual(rc, 0, report)
        self.assertTrue(report["isolated_ok"])
        self.assertFalse(report["full_live_acceptance"])
        for key in accept.ISOLATED_GATES:
            self.assertEqual(report["gates"][key]["status"], "PASS", (key, report))
        for key in ("formal_home", "volume_match"):
            self.assertEqual(report["gates"][key]["status"], "NOT_RUN")
            self.assertEqual(report["gates"][key]["scope"], "real_runtime")
        fixture = report["fixture"]
        self.assertTrue(fixture["git_marker_is_file"])
        self.assertFalse(Path(fixture["home"]).exists(), "temporary home must be removed")
        self.assertEqual(list(live.iterdir()), [marker])
        self.assertEqual(marker.read_text(), "do not read or change caller configuration\n")
        before = report["gates"]["business_unchanged"]["before"]
        self.assertIn(".git/worktrees/", before["index_path"])
        self.assertTrue(before["index"]["sha256"])
        self.assertEqual(report["gates"]["scan_idempotent"]["database"]["observations"], 1)

    def test_scan_counterexamples_make_acceptance_fail(self) -> None:
        # These are faults in actual CLI responses after real subprocess scans,
        # including the old all([]) and inserted=False/error false positives.
        for failure in ("nonzero", "empty", "repeated_error", "skip", "invalid_json"):
            with self.subTest(failure=failure):
                scans = 0

                def corrupt(command, proc):
                    nonlocal scans
                    if "scan" not in command:
                        return proc
                    scans += 1
                    if failure == "nonzero" and scans == 1:
                        proc.returncode = 17
                    elif scans == 2:
                        payload = json.loads(proc.stdout)
                        if failure == "empty":
                            payload["results"] = []
                        elif failure == "repeated_error":
                            payload["results"][0].update(status="error", inserted=False, error="fixture scan failed")
                        elif failure == "skip":
                            payload.update(outcome="skip", error="lock_busy")
                        elif failure == "invalid_json":
                            proc.stdout = "not json"
                            return proc
                        proc.stdout = json.dumps(payload)
                    return proc

                rc, report = self.invoke(corrupt, argv=["--isolated"])
                self.assertEqual(rc, 1)
                self.assertFalse(report["isolated_ok"])
                self.assertEqual(report["gates"]["scan_idempotent"]["status"], "FAIL")
                self.assertEqual(report["gates"]["daily"]["status"], "FAIL")
                self.assertEqual(report["gates"]["handoff"]["status"], "FAIL")

    def test_report_path_alone_is_not_evidence(self) -> None:
        def missing_report(command, proc):
            if command[-1] == "daily":
                generated = Path(proc.stdout.strip())
                generated.unlink()
            return proc

        rc, report = self.invoke(missing_report)
        self.assertEqual(rc, 1)
        self.assertEqual(report["gates"]["daily"]["status"], "FAIL")
        self.assertFalse(report["gates"]["daily"]["generated"]["valid"])

    def test_dirty_content_change_is_detected_even_when_git_status_stays_dirty(self) -> None:
        scans = 0

        def mutate(command, proc):
            nonlocal scans
            if "scan" in command:
                scans += 1
                if scans == 2:
                    config = json.loads((Path(command[4]) / "config.json").read_text())
                    repo = Path(config["projects"][0]["approved_root"])
                    (repo / "README.md").write_text("Changed by a faulty scan.\n")
            return proc

        rc, report = self.invoke(mutate)
        self.assertEqual(rc, 1)
        unchanged = report["gates"]["business_unchanged"]
        self.assertEqual(unchanged["status"], "FAIL")
        self.assertEqual(unchanged["before"]["status"], unchanged["after"]["status"])
        self.assertIn("worktree/README.md", unchanged["changed_fixture_paths"])

    def test_unittest_failure_changes_exit_code(self) -> None:
        rc, report = self.invoke(test_returncode=3)
        self.assertEqual(rc, 1)
        self.assertEqual(report["gates"]["unittest"]["status"], "FAIL")
        self.assertEqual(report["gates"]["unittest"]["returncode"], 3)

    def test_live_mode_is_not_an_implicit_fallback(self) -> None:
        with patch.object(accept, "run") as run, patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as caught:
                accept.main(["--live"])
        self.assertEqual(caught.exception.code, 2)
        run.assert_not_called()
