from __future__ import annotations

import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from xiaomao.cli import main
from xiaomao.config import save_config
from xiaomao.schedule import DAILY_PLIST_NAME, PLIST_NAME
from xiaomao.store import get_meta, open_db_readonly
from tests.helpers import init_repo, website_fixture_config


class CliTests(unittest.TestCase):
    def test_pause_and_resume_scan_with_xiaomao_home_skip_scheduler(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        user_home = root / "user"
        launch_agents = user_home / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True)
        for name in (PLIST_NAME, DAILY_PLIST_NAME):
            (launch_agents / name).touch()
        trial_home = root / "trial"
        db = trial_home / "xiaomao.sqlite"
        output = io.StringIO()

        with (
            patch.dict(os.environ, {"HOME": str(user_home), "XIAOMAO_HOME": str(trial_home)}),
            patch("xiaomao.schedule.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as scheduler,
            redirect_stdout(output),
        ):
            self.assertEqual(main(["pause", "scan"]), 0)
            with open_db_readonly(db) as conn:
                self.assertEqual(get_meta(conn, "scan_paused"), "1")
            self.assertEqual(main(["resume", "scan"]), 0)
            with open_db_readonly(db) as conn:
                self.assertEqual(get_meta(conn, "scan_paused"), "0")

        self.assertEqual([call.args[0][1] for call in scheduler.call_args_list], [])
        self.assertEqual(output.getvalue().count("仅修改当前数据目录的扫描状态"), 2)

    def test_pause_and_resume_scan_with_case_alias_of_formal_home_use_scheduler(self) -> None:
        user_home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        installed_home = user_home / "Library" / "Application Support" / "Xiaomao"
        installed_home.mkdir(parents=True)
        alias = installed_home.with_name("xiaomao")
        if not alias.is_dir():
            self.skipTest("requires a case-insensitive filesystem")

        launch_agents = user_home / "Library" / "LaunchAgents"
        launch_agents.mkdir()
        for name in (PLIST_NAME, DAILY_PLIST_NAME):
            (launch_agents / name).touch()

        with (
            patch.dict(os.environ, {"HOME": str(user_home), "XIAOMAO_HOME": str(alias)}),
            patch("xiaomao.schedule.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as scheduler,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(["pause", "scan"]), 0)
            self.assertEqual(main(["resume", "scan"]), 0)

        self.assertEqual(
            [(call.args[0][1], Path(call.args[0][-1]).name) for call in scheduler.call_args_list],
            [
                ("bootout", PLIST_NAME),
                ("bootout", DAILY_PLIST_NAME),
                ("bootstrap", PLIST_NAME),
                ("bootstrap", DAILY_PLIST_NAME),
            ],
        )

    def test_cli_init_doctor_scan_status(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repo = init_repo(root / "repo")
        home = root / "home"
        cfg = website_fixture_config(home, repo, extra_root=root)
        home.mkdir(parents=True)
        save_config(cfg)

        buf = io.StringIO()
        # This is a CLI regression fixture, not acceptance of the installed
        # LaunchAgents, external disk, or running Ollama process.
        unavailable_job = subprocess.CompletedProcess([], 1, "", "NOT_RUN: isolated fixture")
        volume = {
            "mount": cfg.external.mount, "mounted": False, "status": "unknown",
            "expected_uuid": None, "actual_uuid": None, "note": "isolated fixture",
        }
        ollama = {
            "cli": None, "host": "isolated", "reachable": False,
            "models_dir": cfg.external.models_dir, "models_dir_exists": False,
        }
        with (
            redirect_stdout(buf),
            patch("xiaomao.doctor.volume_present", return_value=False),
            patch("xiaomao.doctor.volume_report", return_value=volume),
            patch("xiaomao.doctor._ollama_info", return_value=ollama),
            patch("xiaomao.ollama_runtime.ollama_bin", return_value=None),
            patch("xiaomao.schedule.print_job", return_value=unavailable_job),
            patch("xiaomao.schedule.print_daily_job", return_value=unavailable_job),
            patch("xiaomao.ollama_runtime.inspect_running", return_value={"reachable": False, "host": "isolated"}),
            patch("xiaomao.ollama_runtime.api_get", return_value=None),
            patch("xiaomao.ollama_runtime.OllamaClient") as model_client,
        ):
            self.assertEqual(main(["--home", str(home), "init"]), 0)
            self.assertEqual(main(["--home", str(home), "doctor"]), 0)
            self.assertEqual(main(["--home", str(home), "scan", "--project", "website"]), 0)
            self.assertEqual(main(["--home", str(home), "status", "--project", "website"]), 0)
            self.assertEqual(main(["--home", str(home), "daily", "--date", "2026-09-20"]), 0)
            self.assertEqual(main(["--home", str(home), "latest", "daily"]), 0)
            self.assertEqual(main(["--home", str(home), "pause", "infer"]), 0)
            self.assertEqual(main(["--home", str(home), "resume", "infer"]), 0)
            self.assertEqual(main(["--home", str(home), "health"]), 0)
            model_client.assert_not_called()
        out = buf.getvalue()
        self.assertIn("unknown", out)
        self.assertIn("website-main", out)
        self.assertIn("本报告由规则程序生成", out)
        self.assertIn("最近一次扫描成功", out)
        self.assertIn("推理已暂停", out)
        self.assertIn("推理已恢复", out)
