from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xiaomao.config import ProjectSpec, WorktreeSpec, default_projects, load_config
from xiaomao.scope import project_exclusion_reason


def _project(root: Path, worktree: Path | None = None) -> ProjectSpec:
    return ProjectSpec(
        project_id="fixture",
        display_name="fixture",
        approved_root=str(root),
        worktrees=[WorktreeSpec("fixture-main", str(worktree or root))],
    )


class ScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_company_roots_and_every_named_worktree_are_refused(self) -> None:
        for name in (
            "theAIapp-service",
            "theAIapp-service-integration-keep",
            "THEAIAPP-SERVICE-worktree",
            "event-services-chooseme-event",
            "event-services-chooseme-event-feature",
        ):
            with self.subTest(name=name):
                project = _project(self.root / name, self.root / "anonymous-tree")
                self.assertIn("company_project_excluded", project_exclusion_reason(project))
                project = _project(self.root / "personal", self.root / name)
                self.assertIn("company_project_excluded", project_exclusion_reason(project))

    def test_link_into_company_path_is_refused_without_reading_content(self) -> None:
        target = self.root / "theAIapp-service" / "nested"
        target.mkdir(parents=True)
        alias = self.root / "innocent-name"
        alias.symlink_to(target, target_is_directory=True)
        with patch.object(Path, "read_text") as read, patch("subprocess.run") as run:
            reason = project_exclusion_reason(_project(alias))
            read.assert_not_called()
            run.assert_not_called()
        self.assertIn("company_project_excluded", reason)

    def test_excluded_named_link_pointing_to_personal_path_stays_refused(self) -> None:
        target = self.root / "personal"
        target.mkdir()
        alias = self.root / "theAIapp-service"
        alias.symlink_to(target, target_is_directory=True)
        self.assertIn("company_project_excluded", project_exclusion_reason(_project(alias)))

    def test_git_directory_symlink_to_company_is_refused_without_opening_target(self) -> None:
        tree = self.root / "neutral"
        tree.mkdir()
        target = self.root / "theAIapp-service" / ".git"
        target.mkdir(parents=True)
        (tree / ".git").symlink_to(target, target_is_directory=True)
        with patch.object(Path, "open", side_effect=AssertionError("Excluded target must not be read")):
            self.assertIn("company_project_excluded", project_exclusion_reason(_project(tree)))

    def test_neutral_gitdir_checks_common_dir_before_reading_excluded_target(self) -> None:
        tree = self.root / "neutral"
        tree.mkdir()
        metadata = self.root / "metadata"
        metadata.mkdir()
        dotgit = tree / ".git"
        common_pointer = metadata / "commondir"
        dotgit.write_text("gitdir: ../metadata\n", encoding="utf-8")
        common_pointer.write_text("../theAIapp-service/.git\n", encoding="utf-8")
        original_open = Path.open
        allowed = {dotgit.resolve(), common_pointer.resolve()}

        def only_pointer_files(path, *args, **kwargs):
            self.assertIn(path.resolve(), allowed)
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", autospec=True, side_effect=only_pointer_files):
            reason = project_exclusion_reason(_project(tree))
        self.assertIn("company_project_excluded", reason)
        self.assertIn("commondir", reason)

    def test_oversized_git_pointer_fails_closed(self) -> None:
        tree = self.root / "neutral"
        tree.mkdir()
        (tree / ".git").write_text("gitdir: " + "x" * 5000, encoding="utf-8")
        self.assertIn("invalid_git_metadata", project_exclusion_reason(_project(tree)))

    def test_mixed_project_is_rejected_even_when_company_tree_is_disabled(self) -> None:
        project = _project(self.root / "personal")
        project.worktrees.append(WorktreeSpec("disabled", str(self.root / "theAIapp-service"), scan=False))
        reason = project_exclusion_reason(project)
        self.assertIn("company_project_excluded", reason)
        self.assertIn("worktree:disabled", reason)

    def test_archive_path_and_broken_compatibility_link_are_refused(self) -> None:
        archive = self.root / "_待删除旧项目_2026-09-22" / "old-project"
        alias = self.root / "old-project"
        alias.symlink_to(archive, target_is_directory=True)
        for path in (archive, alias):
            with self.subTest(path=path):
                self.assertIn("archived_project_excluded", project_exclusion_reason(_project(path)))

    def test_unresolvable_link_and_relative_registration_fail_closed(self) -> None:
        loop = self.root / "loop"
        loop.symlink_to(loop)
        for path in (loop, Path("relative-project")):
            with self.subTest(path=path):
                self.assertIn("invalid_project_path", project_exclusion_reason(_project(path)))

    def test_personal_registered_paths_remain_allowed_without_discovery(self) -> None:
        project = _project(self.root / "QAI", self.root / "explicit-QAI-worktree")
        with patch.object(Path, "read_text") as read, patch("subprocess.run") as run:
            self.assertIsNone(project_exclusion_reason(project))
            read.assert_not_called()
            run.assert_not_called()
        self.assertEqual(len(project.worktrees), 1)

    def test_defaults_disable_archived_registration_without_retargeting(self) -> None:
        workspace = self.root / "workspace"
        workspace.mkdir()
        original = workspace / "QAI"
        archive = workspace / "_待删除旧项目_2026-09-22" / "QAI"
        original.symlink_to(archive, target_is_directory=True)
        with patch("xiaomao.config._WS", str(workspace)), patch("xiaomao.config._HOME", str(self.root)):
            projects = default_projects()
        project = next(p for p in projects if p.project_id == "QAI")
        self.assertEqual(project.approved_root, str(original))
        self.assertTrue(all(not wt.scan for wt in project.worktrees))
        self.assertIn("archived_project_excluded", project.worktrees[0].notes)
        self.assertNotIn("website", {p.project_id for p in projects})

    def test_retired_original_registrations_refuse_without_filesystem_lookup(self) -> None:
        retired = (
            "agent-accord", "agent-stablecoin-wallet", "dolphinode", "event-watcher",
            "stableflow", "wallet", "wallet-mpc-sign", "wallet-reliability-lab",
            "web3-wallet-engineer-lab", "xiuqiu-hermes-skills", "xiuqiu-token",
        )
        for name in retired:
            for suffix in ("", "/nested-worktree"):
                with self.subTest(name=name, suffix=suffix), patch.object(Path, "resolve") as resolve:
                    project = _project(Path(f"/Users/xiuqiu/WorkSpace/{name}{suffix}"))
                    self.assertIn("archived_project_excluded", project_exclusion_reason(project))
                    resolve.assert_not_called()

    def test_third_party_application_registrations_are_refused(self) -> None:
        paths = [Path("/Users/xiuqiu/WorkSpace/stats")]
        paths.extend(self.root / name for name in ("Stats", "AgentNotch", "TokenMonitor", "Stats.app"))
        for path in paths:
            with self.subTest(path=path), patch.object(Path, "resolve") as resolve:
                self.assertIn("third_party_project_excluded", project_exclusion_reason(_project(path)))
                resolve.assert_not_called()

    def test_default_registrations_are_exactly_the_six_existing_personal_paths(self) -> None:
        workspace = self.root / "workspace"
        with patch("xiaomao.config._WS", str(workspace)), patch("xiaomao.config._HOME", str(self.root)):
            projects = default_projects()
        expected = {
            "QAI": str(workspace / "QAI"),
            "wallet-core": str(workspace / "wallet-core"),
            "xiaomao-Agent": str(workspace / "xiaomao-Agent"),
            "xiuqiu-site": str(workspace / "xiuqiu-site"),
            "AI-Web3-Learning": str(self.root / "AI-Web3-Learning"),
            "Wallet-Infrastructure": str(self.root / "Wallet-Infrastructure"),
        }
        self.assertEqual({p.project_id: p.approved_root for p in projects}, expected)
        self.assertTrue(all(len(p.worktrees) == 1 and p.worktrees[0].path == p.approved_root for p in projects))

    def test_null_and_empty_project_config_do_not_reinstate_defaults(self) -> None:
        for value in (None, []):
            with self.subTest(projects=value):
                (self.root / "config.json").write_text(json.dumps({"projects": value}), encoding="utf-8")
                with patch("xiaomao.config.default_projects") as defaults:
                    cfg = load_config(self.root)
                    defaults.assert_not_called()
                self.assertEqual(cfg.projects, [])
