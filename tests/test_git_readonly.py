from __future__ import annotations

import unittest
from pathlib import Path

from xiaomao.git_readonly import collect_snapshot, parse_status_z, parse_worktree_porcelain
from tests.helpers import git, init_repo


class GitReadOnlyTests(unittest.TestCase):
    def test_unborn(self) -> None:
        repo = init_repo(Path(self.enterContext(__import__("tempfile").TemporaryDirectory())) / "unborn", initial_commit=False)
        snap = collect_snapshot(repo)
        self.assertTrue(snap.is_unborn)
        self.assertIsNone(snap.head_oid)
        self.assertEqual(snap.collection_status, "ok")
        self.assertIn(snap.branch_ref, {"refs/heads/main", "refs/heads/master"})

    def test_detached(self) -> None:
        repo = init_repo(Path(self.enterContext(__import__("tempfile").TemporaryDirectory())) / "det")
        oid = git(repo, "rev-parse", "HEAD").stdout.strip()
        git(repo, "checkout", "--detach", "HEAD")
        snap = collect_snapshot(repo)
        self.assertTrue(snap.is_detached)
        self.assertEqual(snap.head_oid, oid)
        self.assertIsNone(snap.branch_ref)

    def test_staged_unstaged_untracked(self) -> None:
        repo = init_repo(Path(self.enterContext(__import__("tempfile").TemporaryDirectory())) / "dirty")
        (repo / "README.md").write_text("hello\nworld\n", encoding="utf-8")
        (repo / "staged.txt").write_text("s\n", encoding="utf-8")
        git(repo, "add", "staged.txt")
        (repo / "untracked.txt").write_text("u\n", encoding="utf-8")
        snap = collect_snapshot(repo)
        self.assertEqual([e.path for e in snap.staged], ["staged.txt"])
        self.assertTrue(any(e.path == "README.md" for e in snap.unstaged))
        self.assertEqual([e.path for e in snap.untracked], ["untracked.txt"])

    def test_status_z_rename(self) -> None:
        blob = "R  old\0new\0?? extra\0"
        entries = parse_status_z(blob)
        self.assertEqual(entries[0].staged, "R")
        self.assertEqual(entries[0].orig_path, "old")
        self.assertEqual(entries[0].path, "new")
        self.assertTrue(entries[1].is_untracked)
        self.assertEqual(entries[1].path, "extra")

    def test_worktree_porcelain(self) -> None:
        text = (
            "worktree /tmp/a\nHEAD abc\nbranch refs/heads/main\n\n"
            "worktree /tmp/b\nHEAD def\ndetached\n"
        )
        wts = parse_worktree_porcelain(text)
        self.assertEqual(wts[0].branch, "refs/heads/main")
        self.assertTrue(wts[1].detached)
        self.assertEqual(wts[1].path, "/tmp/b")
