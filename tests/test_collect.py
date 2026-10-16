from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xiaomao.config import AppConfig, ProjectSpec, WorktreeSpec, default_config, load_config, save_config
from xiaomao.collect import register_project, scan_authorized, scan_worktree
from xiaomao.handoff_view import scope_identity
from xiaomao.policy import looks_like_secret, path_is_denied, redact_text, safe_excerpt_from_bytes
from xiaomao.scope import project_exclusion_reason
from xiaomao.store import evidence_for_observation, latest_observation, open_db
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
    cfg.briefing_docs_root = str(home / "docs")
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

    def test_return_to_historical_state_creates_new_observation_then_deduplicates(self) -> None:
        root = self._tmp()
        repo = init_repo(root / "repo")
        home = root / "home"
        cfg, project, wt = _cfg(home, repo)
        with open_db(home / "xiaomao.sqlite") as conn, patch(
            "xiaomao.collect.utc_now", return_value="2026-09-22T12:00:00+00:00"
        ):
            register_project(conn, cfg, project)
            first = scan_worktree(conn, cfg, project, wt)
            original = dict(latest_observation(conn, wt.worktree_id))
            changed_file = repo / "new-file.txt"
            changed_file.write_text("temporary fixture change\n", encoding="utf-8")
            changed = scan_worktree(conn, cfg, project, wt)
            changed_file.unlink()
            restored = scan_worktree(conn, cfg, project, wt)
            repeated = scan_worktree(conn, cfg, project, wt)
            latest = dict(latest_observation(conn, wt.worktree_id))
            rows = list(conn.execute("SELECT * FROM observations ORDER BY rowid"))
        self.assertTrue(restored["inserted"])
        self.assertEqual(restored["fingerprint"], first["fingerprint"])
        self.assertNotIn(restored["observation_id"], {first["observation_id"], changed["observation_id"]})
        self.assertEqual(latest["observation_id"], restored["observation_id"])
        self.assertEqual(latest["untracked_count"], 0)
        self.assertEqual(repeated["observation_id"], restored["observation_id"])
        self.assertEqual(repeated["status"], "unchanged")
        self.assertFalse(repeated["inserted"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(dict(rows[0]), original)

    def test_error_after_recovery_creates_new_observation_then_deduplicates(self) -> None:
        root = self._tmp()
        repo = root / "repo"
        home = root / "home"
        cfg, project, wt = _cfg(home, repo)
        with open_db(home / "xiaomao.sqlite") as conn, patch(
            "xiaomao.collect.utc_now", return_value="2026-09-22T12:00:00+00:00"
        ):
            register_project(conn, cfg, project)
            first_error = scan_worktree(conn, cfg, project, wt)
            original = dict(latest_observation(conn, wt.worktree_id))
            init_repo(repo)
            recovered = scan_worktree(conn, cfg, project, wt)
            repo.rename(root / "moved-repo")
            new_error = scan_worktree(conn, cfg, project, wt)
            repeated = scan_worktree(conn, cfg, project, wt)
            latest = dict(latest_observation(conn, wt.worktree_id))
            rows = list(conn.execute("SELECT * FROM observations ORDER BY rowid"))
        self.assertTrue(recovered["inserted"])
        self.assertTrue(new_error["inserted"])
        self.assertNotEqual(new_error["observation_id"], first_error["observation_id"])
        self.assertEqual(latest["collection_status"], "error")
        self.assertEqual(latest["observation_id"], new_error["observation_id"])
        self.assertFalse(repeated["inserted"])
        self.assertEqual(repeated["status"], "error")
        self.assertEqual(repeated["observation_id"], new_error["observation_id"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(dict(rows[0]), original)

    def test_mixed_success_and_error_is_not_a_successful_project_scan(self) -> None:
        root = self._tmp()
        repo = init_repo(root / "repo")
        home = root / "home"
        cfg, project, _wt = _cfg(home, repo)
        project.worktrees.append(WorktreeSpec("t-missing", str(root / "missing-repo")))
        with open_db(home / "xiaomao.sqlite") as conn:
            results = scan_authorized(conn, cfg)[project.project_id]
            run = conn.execute("SELECT run_id, outcome, last_safe_error FROM scan_runs").fetchone()
            event = conn.execute("SELECT payload_json FROM events WHERE kind='scan_error'").fetchone()
            successful = conn.execute("SELECT COUNT(*) FROM scan_runs WHERE outcome='success'").fetchone()[0]
        self.assertEqual({result["status"] for result in results}, {"ok", "error"})
        self.assertEqual(run["outcome"], "error")
        self.assertTrue(run["last_safe_error"])
        self.assertEqual(successful, 0)
        proof = json.loads(event["payload_json"])
        self.assertEqual(proof["run_id"], run["run_id"])
        self.assertEqual(proof["scope_sha256"], scope_identity(project))
        self.assertEqual(
            {row["worktree_id"]: row["observation_id"] for row in proof["observation_ids"]},
            {row["worktree_id"]: row["observation_id"] for row in results},
        )

    def test_success_event_proves_current_run_scope_and_all_tree_observations(self) -> None:
        root = self._tmp()
        first_repo = init_repo(root / "first")
        second_repo = init_repo(root / "second")
        home = root / "home"
        cfg, project, _wt = _cfg(home, first_repo)
        second_tree = WorktreeSpec("t-second", str(second_repo))
        project.worktrees.append(second_tree)
        with open_db(home / "xiaomao.sqlite") as conn:
            for _ in range(2):
                results = scan_authorized(conn, cfg)[project.project_id]
                run = conn.execute("SELECT * FROM scan_runs ORDER BY rowid DESC LIMIT 1").fetchone()
                event = conn.execute("SELECT * FROM events WHERE kind='scan_success' ORDER BY rowid DESC LIMIT 1").fetchone()
                proof = json.loads(event["payload_json"])
                self.assertEqual(proof["run_id"], run["run_id"])
                self.assertEqual(proof["scope_sha256"], scope_identity(project))
                self.assertEqual(event["project_id"], project.project_id)
                self.assertEqual(
                    {row["worktree_id"]: row["observation_id"] for row in proof["observation_ids"]},
                    {row["worktree_id"]: row["observation_id"] for row in results},
                )
                self.assertEqual(len(proof["observation_ids"]), 2)
            second_tree.scan = False
            scan_authorized(conn, cfg)
            event = conn.execute("SELECT payload_json FROM events WHERE kind='scan_success' ORDER BY rowid DESC LIMIT 1").fetchone()
            proof = json.loads(event["payload_json"])
            self.assertEqual(proof["scope_sha256"], scope_identity(project))
            self.assertEqual([row["worktree_id"] for row in proof["observation_ids"]], ["t-main"])
            second_tree.scan = True
            self.assertNotEqual(proof["scope_sha256"], scope_identity(project))

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

    def _prefix_fixture(self):
        import shutil

        root = self._tmp()
        repo = init_repo(root / "repo")
        side = root / "tmpwt" / "p1"
        other = root / "tmpwt" / "misc"
        loose = root / "tmpwt" / "loose"
        git(repo, "worktree", "add", "-b", "codex/p1-supervisor", str(side))
        git(repo, "worktree", "add", "-b", "scratch", str(other))
        git(repo, "worktree", "add", "--detach", str(loose))
        home = root / "home"
        cfg, project, wt = _cfg(home, repo)
        project.branch_prefixes = ["codex/"]
        return root, repo, side, other, loose, home, cfg, project, shutil

    def test_branch_prefix_tree_is_observed_without_changing_scope(self) -> None:
        from xiaomao.collect import PREFIX_WORKTREE_NOTE, prefix_worktree_id, scan_project

        root, repo, side, other, loose, home, cfg, project, _ = self._prefix_fixture()
        scope_before = scope_identity(project)
        (side / "work.txt").write_text("wip\n", encoding="utf-8")
        with open_db(home / "xiaomao.sqlite") as conn:
            results = scan_project(conn, cfg, "t")
            trees = {r["worktree_id"]: dict(r) for r in conn.execute("SELECT * FROM worktrees")}
            run = conn.execute("SELECT outcome FROM scan_runs ORDER BY rowid DESC LIMIT 1").fetchone()
            ev = conn.execute(
                "SELECT payload_json FROM events WHERE kind='scan_success' ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            discovered = {r["path"]: r["authorized"] for r in conn.execute("SELECT * FROM discovered_worktrees")}
        side_id = prefix_worktree_id("t", str(side))
        self.assertEqual(run["outcome"], "success")
        self.assertIn(side_id, trees)
        self.assertEqual(trees[side_id]["notes"], PREFIX_WORKTREE_NOTE)
        # Non-matching branch and detached tree are not observed.
        self.assertEqual(set(trees), {"t-main", side_id})
        prefix_row = next(r for r in results if r["worktree_id"] == side_id)
        self.assertEqual(prefix_row["branch"], "codex/p1-supervisor")
        self.assertTrue(prefix_row["inserted"])
        # Registered scope and the scan-run coverage proof are unchanged.
        self.assertEqual(scope_identity(project), scope_before)
        self.assertEqual(
            [o["worktree_id"] for o in json.loads(ev["payload_json"])["observation_ids"]], ["t-main"]
        )
        self.assertEqual(project.worktrees[0].worktree_id, "t-main")
        self.assertEqual(len(project.worktrees), 1)
        self.assertTrue(discovered[str(side.resolve())])
        self.assertFalse(discovered[str(other.resolve())])
        # Same path keeps the same id; an unchanged second scan inserts nothing.
        with open_db(home / "xiaomao.sqlite") as conn:
            again = scan_project(conn, cfg, "t")
        self.assertFalse(next(r for r in again if r["worktree_id"] == side_id)["inserted"])

    def test_removed_prefix_tree_is_gone_not_error(self) -> None:
        from xiaomao.collect import prefix_worktree_id, scan_project

        root, repo, side, other, loose, home, cfg, project, shutil = self._prefix_fixture()
        with open_db(home / "xiaomao.sqlite") as conn:
            scan_project(conn, cfg, "t")
        side_id = prefix_worktree_id("t", str(side))
        # Directory deleted without `git worktree remove`: entry becomes prunable.
        shutil.rmtree(side)
        with open_db(home / "xiaomao.sqlite") as conn:
            results = scan_project(conn, cfg, "t")
            run = conn.execute("SELECT outcome FROM scan_runs ORDER BY rowid DESC LIMIT 1").fetchone()
            row = conn.execute("SELECT scan_enabled FROM worktrees WHERE worktree_id=?", (side_id,)).fetchone()
            gone = conn.execute("SELECT payload_json FROM events WHERE kind='worktree_gone'").fetchall()
            errors = conn.execute(
                "SELECT COUNT(*) AS n FROM observations WHERE worktree_id=? AND collection_status='error'",
                (side_id,),
            ).fetchone()["n"]
        self.assertEqual(run["outcome"], "success")
        self.assertEqual(next(r for r in results if r["worktree_id"] == side_id)["status"], "gone")
        self.assertEqual(row["scan_enabled"], 0)
        self.assertEqual(len(gone), 1)
        self.assertEqual(errors, 0)
        # Already gone: a later scan does not emit it again.
        with open_db(home / "xiaomao.sqlite") as conn:
            scan_project(conn, cfg, "t")
            self.assertEqual(
                conn.execute("SELECT COUNT(*) AS n FROM events WHERE kind='worktree_gone'").fetchone()["n"], 1
            )

    def test_daily_lists_prefix_tree_branch(self) -> None:
        from xiaomao.collect import scan_project
        from xiaomao.daily import build_bundle
        from xiaomao.feature_render import render_details

        root, repo, side, other, loose, home, cfg, project, _ = self._prefix_fixture()
        (side / "work.txt").write_text("wip\n", encoding="utf-8")
        with open_db(home / "xiaomao.sqlite") as conn:
            with patch("xiaomao.collect.utc_now", return_value="2026-10-01T12:00:00+00:00"):
                scan_project(conn, cfg, "t")
            text = render_details(build_bundle(cfg, conn, date="2026-10-01"), {})
        self.assertIn("其他工作树", text)
        self.assertIn("codex/p1-supervisor", text)
        self.assertIn("untracked 1", text)
        self.assertNotIn("scratch", text)

    def test_no_prefixes_means_no_extra_trees(self) -> None:
        from xiaomao.collect import scan_project

        root, repo, side, other, loose, home, cfg, project, _ = self._prefix_fixture()
        project.branch_prefixes = []
        with open_db(home / "xiaomao.sqlite") as conn:
            results = scan_project(conn, cfg, "t")
            ids = {r["worktree_id"] for r in conn.execute("SELECT worktree_id FROM worktrees")}
        self.assertEqual(ids, {"t-main"})
        self.assertEqual([r["worktree_id"] for r in results], ["t-main"])

    def test_prefix_tree_reappearing_under_excluded_name_is_refused(self) -> None:
        from xiaomao.collect import scan_project

        root = self._tmp()
        repo = init_repo(root / "repo")
        bad = root / "theaiapp-service-copy"
        git(repo, "worktree", "add", "-b", "codex/x", str(bad))
        home = root / "home"
        cfg, project, wt = _cfg(home, repo)
        project.branch_prefixes = ["codex/"]
        with open_db(home / "xiaomao.sqlite") as conn:
            results = scan_project(conn, cfg, "t")
            ids = {r["worktree_id"] for r in conn.execute("SELECT worktree_id FROM worktrees")}
        self.assertEqual(ids, {"t-main"})
        self.assertEqual([r["worktree_id"] for r in results], ["t-main"])

    def test_branch_prefixes_round_trip_config(self) -> None:
        root = self._tmp()
        home = root / "home"
        repo = init_repo(root / "repo")
        cfg, project, wt = _cfg(home, repo)
        project.branch_prefixes = ["codex/", "claude/"]
        home.mkdir(parents=True)
        save_config(cfg)
        loaded = load_config(home)
        self.assertEqual(loaded.project("t").branch_prefixes, ["codex/", "claude/"])

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

        root = self._tmp()
        with patch("xiaomao.config._WS", str(root / "workspace")), patch("xiaomao.config._HOME", str(root)):
            projects = default_projects()
        roots = {p.approved_root.rstrip("/") for p in projects}
        self.assertNotIn(str(root), roots)
        ids = {p.project_id for p in projects}
        self.assertNotIn("website", ids)
        self.assertIn("QAI", ids)
        self.assertIn("AI-Web3-Learning", ids)
        self.assertIn("Wallet-Infrastructure", ids)
        self.assertEqual(ids, {"QAI", "wallet-core", "xiaomao-Agent", "xiuqiu-site", "AI-Web3-Learning", "Wallet-Infrastructure"})
        self.assertTrue(all(project_exclusion_reason(p) is None for p in projects))
        # wallet-core stays scanned; only its menu dirty inventory is quieted.
        quiet = {p.project_id for p in projects if p.menu_hide_dirty}
        self.assertEqual(quiet, {"wallet-core"})
        self.assertTrue(all(wt.scan for p in projects for wt in p.worktrees))

    def test_menu_hide_dirty_round_trips_and_defaults_off(self) -> None:
        root = self._tmp()
        home = root / "home"
        cfg, project, _wt = _cfg(home, root / "repo")
        project.menu_hide_dirty = True
        save_config(cfg)
        self.assertTrue(load_config(home).project(project.project_id).menu_hide_dirty)
        raw = json.loads((home / "config.json").read_text(encoding="utf-8"))
        del raw["projects"][0]["menu_hide_dirty"]
        (home / "config.json").write_text(json.dumps(raw), encoding="utf-8")
        self.assertFalse(load_config(home).project(project.project_id).menu_hide_dirty)

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

    def test_saved_company_registration_cannot_reach_git_collector(self) -> None:
        root = self._tmp()
        home = root / "home"
        cfg, project, _wt = _cfg(home, root / "theAIapp-service-integration-keep")
        save_config(cfg)
        loaded = load_config(home)
        with open_db(home / "xiaomao.sqlite") as conn, patch("xiaomao.collect.collect_snapshot") as collect:
            result = scan_authorized(conn, loaded)
            collect.assert_not_called()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)
            run = conn.execute("SELECT outcome, last_safe_error FROM scan_runs").fetchone()
            self.assertEqual(run["outcome"], "skip")
            self.assertIn("company_project_excluded", run["last_safe_error"])
            self.assertEqual(conn.execute("SELECT scan_enabled FROM worktrees").fetchone()[0], 0)
        self.assertEqual(result[project.project_id][0]["status"], "excluded")
        self.assertEqual(loaded.scannable_worktrees(), [])

    def test_mixed_project_refuses_even_personal_tree_before_scan_helper(self) -> None:
        root = self._tmp()
        home = root / "home"
        cfg, project, _wt = _cfg(home, root / "personal")
        project.worktrees.append(
            WorktreeSpec("company", str(root / "event-services-chooseme-event"), scan=False)
        )
        with open_db(home / "xiaomao.sqlite") as conn, patch("xiaomao.collect.scan_worktree") as scan:
            result = scan_authorized(conn, cfg)
            scan.assert_not_called()
        self.assertEqual(result[project.project_id][0]["status"], "excluded")

    def test_saved_retired_or_third_party_registration_cannot_reach_git_collector(self) -> None:
        for path in ("/Users/xiuqiu/WorkSpace/agent-accord", "/Users/xiuqiu/WorkSpace/stats"):
            with self.subTest(path=path):
                root = self._tmp()
                cfg, project, _wt = _cfg(root / "home", Path(path))
                save_config(cfg)
                loaded = load_config(root / "home")
                with open_db(root / "home" / "xiaomao.sqlite") as conn, patch("xiaomao.collect.collect_snapshot") as collect:
                    result = scan_authorized(conn, loaded)
                    collect.assert_not_called()
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)
                self.assertEqual(loaded.project(project.project_id).approved_root, path)
                self.assertEqual(result[project.project_id][0]["status"], "excluded")

    def test_neutral_company_linked_worktree_cannot_reach_git_collector(self) -> None:
        root = self._tmp()
        company_fixture = init_repo(root / "theAIapp-service")
        neutral_tree = root / "neutral-tree"
        git(company_fixture, "worktree", "add", "-b", "neutral", str(neutral_tree))
        cfg, project, _wt = _cfg(root / "home", neutral_tree)
        with open_db(root / "home" / "xiaomao.sqlite") as conn, patch(
            "xiaomao.collect.collect_snapshot", side_effect=AssertionError("Git collector must not run")
        ) as collect:
            result = scan_authorized(conn, cfg)
            collect.assert_not_called()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)
        self.assertEqual(result[project.project_id][0]["status"], "excluded")
        self.assertIn("company_project_excluded", result[project.project_id][0]["reason"])

    def test_archived_compatibility_link_cannot_reach_git_collector(self) -> None:
        root = self._tmp()
        archive = root / "_待删除旧项目_2026-09-22" / "old-repo"
        archive.mkdir(parents=True)
        compatibility_link = root / "old-repo"
        compatibility_link.symlink_to(archive, target_is_directory=True)
        cfg, project, _wt = _cfg(root / "home", compatibility_link)
        with open_db(root / "home" / "xiaomao.sqlite") as conn, patch("xiaomao.collect.collect_snapshot") as collect:
            result = scan_authorized(conn, cfg, project.project_id)
            collect.assert_not_called()
        self.assertEqual(result[project.project_id][0]["status"], "excluded")
        self.assertIn("archived_project_excluded", result[project.project_id][0]["reason"])

    def test_direct_scan_helper_refuses_excluded_argument(self) -> None:
        root = self._tmp()
        cfg, project, _wt = _cfg(root / "home", root / "personal")
        company_wt = WorktreeSpec("other", str(root / "theAIapp-service-codex"))
        with patch("xiaomao.collect.collect_snapshot") as collect:
            result = scan_worktree(None, cfg, project, company_wt)
            collect.assert_not_called()
        self.assertEqual(result["status"], "excluded")

    def test_explicit_empty_projects_never_scan_or_fallback(self) -> None:
        root = self._tmp()
        cfg, _project, _wt = _cfg(root / "home", root / "unused")
        cfg.projects = []
        save_config(cfg)
        loaded = load_config(root / "home")
        with patch("xiaomao.collect.scan_project") as scan:
            self.assertEqual(scan_authorized(None, loaded), {})
            scan.assert_not_called()
        self.assertEqual(loaded.projects, [])

    def test_project_with_no_enabled_worktrees_does_not_record_success(self) -> None:
        root = self._tmp()
        home = root / "home"
        cfg, project, _wt = _cfg(home, root / "unused", scan=False)
        with open_db(home / "xiaomao.sqlite") as conn, patch("xiaomao.collect.collect_snapshot") as collect:
            result = scan_authorized(conn, cfg)
            collect.assert_not_called()
            run = conn.execute("SELECT outcome, last_safe_error FROM scan_runs").fetchone()
            self.assertEqual(run["outcome"], "skip")
            self.assertEqual(run["last_safe_error"], "no_enabled_worktrees")
        self.assertEqual(result[project.project_id], [])
