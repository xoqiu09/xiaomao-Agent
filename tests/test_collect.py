from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xiaomao.config import AppConfig, ProjectSpec, WorktreeSpec, default_config
from xiaomao.collect import register_project, scan_authorized, scan_worktree
from xiaomao.policy import looks_like_secret, path_is_denied, redact_text, safe_excerpt_from_bytes
from xiaomao.store import evidence_for_observation, open_db
from tests.helpers import git, init_repo


def _cfg(home: Path, repo: Path, scan: bool = True) -> tuple[AppConfig, ProjectSpec, WorktreeSpec]:
    cfg = default_config(home)
    cfg.home = str(home)
    wt = WorktreeSpec(worktree_id="t-main", path=str(repo), scan=scan)
    project = ProjectSpec(
        project_id="t",
        display_name="fixture",
        approved_root=str(repo),
        worktrees=[wt],
    )
    cfg.projects = [project]
    return cfg, project, wt


class CollectTests(unittest.TestCase):
    def _tmp(self) -> Path:
        return Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_repeat_scan_is_idempotent(self) -> None:
        root = self._tmp()
        repo = init_repo(root / "repo")
        home = root / "home"
        cfg, project, wt = _cfg(home, repo)
        with open_db(home / "xiaomao.sqlite") as conn:
            register_project(conn, cfg, project)
            first = scan_worktree(conn, cfg, project, wt)
            second = scan_worktree(conn, cfg, project, wt)
            n = conn.execute("SELECT COUNT(*) AS c FROM observations").fetchone()["c"]
        self.assertTrue(first["inserted"])
        self.assertFalse(second["inserted"])
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(n, 1)

    def test_denied_env_is_not_excerpted(self) -> None:
        root = self._tmp()
        repo = init_repo(root / "repo")
        (repo / ".env").write_text("SECRET_KEY=super-secret-value\n", encoding="utf-8")
        home = root / "home"
        cfg, project, wt = _cfg(home, repo)
        with open_db(home / "xiaomao.sqlite") as conn:
            register_project(conn, cfg, project)
            result = scan_worktree(conn, cfg, project, wt)
            ev = evidence_for_observation(conn, result["observation_id"])
            facts = conn.execute(
                "SELECT facts_json FROM observations WHERE observation_id=?",
                (result["observation_id"],),
            ).fetchone()["facts_json"]
        self.assertTrue(path_is_denied(".env"))
        self.assertTrue(path_is_denied(".git-credentials"))
        self.assertTrue(path_is_denied(".ssh/id_ed25519"))
        self.assertTrue(any(row["evidence_kind"] == "denied_path" for row in ev))
        self.assertTrue(all("super-secret" not in (row["safe_excerpt"] or "") for row in ev))
        self.assertNotIn("super-secret-value", facts)

    def test_discovered_worktree_is_not_auto_authorized(self) -> None:
        root = self._tmp()
        repo = init_repo(root / "repo")
        extra = root / "extra"
        git(repo, "worktree", "add", "-b", "feature", str(extra))
        home = root / "home"
        cfg, project, wt = _cfg(home, repo)
        with open_db(home / "xiaomao.sqlite") as conn:
            register_project(conn, cfg, project)
            result = scan_worktree(conn, cfg, project, wt)
            discovered = list(conn.execute("SELECT * FROM discovered_worktrees").fetchall())
            scan_flags = list(conn.execute("SELECT worktree_id FROM worktrees").fetchall())
        self.assertTrue(any(not row["authorized"] for row in discovered))
        self.assertIn(str(extra.resolve()), result["discovered_unauthorized"])
        self.assertEqual({row["worktree_id"] for row in scan_flags}, {"t-main"})

    def test_out_of_tree_symlink_is_listed_not_followed_as_content(self) -> None:
        root = self._tmp()
        repo = init_repo(root / "repo")
        outside = root / "secret-outside.txt"
        outside.write_text("outside-secret-42\n", encoding="utf-8")
        (repo / "link.txt").symlink_to(outside)
        home = root / "home"
        cfg, project, wt = _cfg(home, repo)
        with open_db(home / "xiaomao.sqlite") as conn:
            register_project(conn, cfg, project)
            result = scan_worktree(conn, cfg, project, wt)
            facts = conn.execute(
                "SELECT facts_json FROM observations WHERE observation_id=?",
                (result["observation_id"],),
            ).fetchone()["facts_json"]
            ev = evidence_for_observation(conn, result["observation_id"])
        self.assertIn("link.txt", facts)
        self.assertNotIn("outside-secret-42", facts)
        blob = "".join((row["safe_excerpt"] or "") + row["source_locator"] for row in ev)
        self.assertNotIn("outside-secret-42", blob)

    def test_secret_helpers(self) -> None:
        self.assertTrue(looks_like_secret("-----BEGIN OPENSSH PRIVATE KEY-----"))
        self.assertTrue(looks_like_secret("AKIA" + "A" * 16))
        excerpt, status = safe_excerpt_from_bytes(b"-----BEGIN RSA PRIVATE KEY-----\nabc")
        self.assertIsNone(excerpt)
        self.assertEqual(status, "secret")
        self.assertNotIn("PRIVATE", redact_text("-----BEGIN RSA PRIVATE KEY-----"))

    def test_default_projects_never_authorize_home_as_one_tree(self) -> None:
        from xiaomao.config import default_projects

        projects = default_projects()
        self.assertEqual(projects[0].project_id, "website")
        roots = {p.approved_root.rstrip("/") for p in projects}
        self.assertNotIn("/Users/xiuqiu", roots)
        ids = {p.project_id for p in projects}
        self.assertIn("QAI", ids)
        self.assertIn("AI-Web3-Learning", ids)
        self.assertIn("Wallet-Infrastructure", ids)
        website = projects[0]
        by_id = {w.worktree_id: w for w in website.worktrees}
        self.assertEqual(set(by_id), {"website-main"})
        self.assertTrue(by_id["website-main"].scan)
        self.assertIn("theAIapp-service-integration-keep", by_id["website-main"].path)
        scannable = [w for p in projects for w in p.worktrees if w.scan]
        self.assertGreaterEqual(len(scannable), 30)
        self.assertTrue(all("/Documents/" not in w.path for w in scannable))

    def test_scan_authorized_covers_every_fixture_project(self) -> None:
        root = self._tmp()
        repo_a = init_repo(root / "a")
        repo_b = init_repo(root / "b")
        home = root / "home"
        cfg, project_a, _wt = _cfg(home, repo_a)
        project_b = ProjectSpec(
            project_id="u",
            display_name="other",
            approved_root=str(repo_b),
            worktrees=[WorktreeSpec(worktree_id="u-main", path=str(repo_b), scan=True)],
        )
        cfg.projects = [project_a, project_b]
        with open_db(home / "xiaomao.sqlite") as conn:
            by_project = scan_authorized(conn, cfg)
            n = conn.execute("SELECT COUNT(*) AS c FROM observations").fetchone()["c"]
        self.assertEqual(set(by_project), {"t", "u"})
        self.assertTrue(by_project["t"][0]["inserted"])
        self.assertTrue(by_project["u"][0]["inserted"])
        self.assertEqual(n, 2)
