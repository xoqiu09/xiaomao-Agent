from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from xiaomao.cli import main
from xiaomao.config import save_config
from tests.helpers import init_repo, website_fixture_config


class CliTests(unittest.TestCase):
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
