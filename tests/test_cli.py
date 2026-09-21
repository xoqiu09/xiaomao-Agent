from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from xiaomao.cli import main
from xiaomao.config import default_config, save_config
from tests.helpers import init_repo


class CliTests(unittest.TestCase):
    def test_cli_init_doctor_scan_status(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repo = init_repo(root / "repo")
        home = root / "home"
        cfg = default_config(home)
        cfg.home = str(home)
        cfg.projects[0].approved_root = str(repo)
        cfg.projects[0].worktrees[0].path = str(repo)
        cfg.projects[0].worktrees[0].worktree_id = "website-main"
        for extra in cfg.projects[0].worktrees[1:]:
            extra.path = str(root / extra.worktree_id)
            extra.scan = False
        home.mkdir(parents=True)
        save_config(cfg)

        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["--home", str(home), "init"]), 0)
            self.assertEqual(main(["--home", str(home), "doctor"]), 0)
            self.assertEqual(main(["--home", str(home), "scan", "--project", "website"]), 0)
            self.assertEqual(main(["--home", str(home), "status", "--project", "website"]), 0)
            self.assertEqual(main(["--home", str(home), "daily", "--date", "2026-09-20"]), 0)
            self.assertEqual(main(["--home", str(home), "latest", "daily"]), 0)
            self.assertEqual(main(["--home", str(home), "pause", "infer"]), 0)
            self.assertEqual(main(["--home", str(home), "resume", "infer"]), 0)
            self.assertEqual(main(["--home", str(home), "health"]), 0)
        out = buf.getvalue()
        self.assertIn("unknown", out)
        self.assertIn("website-main", out)
        self.assertIn("本报告由规则程序生成", out)
        self.assertIn("最近一次扫描成功", out)
        self.assertIn("推理已暂停", out)
        self.assertIn("推理已恢复", out)
