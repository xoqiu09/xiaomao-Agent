"""Four delivery states on synthetic Git repositories with local bare remotes."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.helpers import git, init_repo
from xiaomao.delivery_state import annotate_project, commit_state, remote_view, unpushed_commits


def commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name + "\n")
    git(repo, "add", name)
    git(repo, "commit", "-m", name)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def clone_with_default(root: Path, branch: str) -> Path:
    seed = init_repo(root / "seed")
    git(seed, "branch", "-m", branch)
    bare = root / "remote.git"
    git(root, "clone", "--bare", str(seed), str(bare))
    git(bare, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
    work = root / "work"
    git(root, "clone", str(bare), str(work))
    git(work, "config", "user.name", "xiaomao-test")
    git(work, "config", "user.email", "xiaomao-test@example.invalid")
    return work


def project_for(repo: Path, commits: list[str], ongoing_files: list[str]) -> dict:
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    return {"trees": [{"tree_id": "t", "path": str(repo), "head": head, "gone": False}],
            "commits": [{"sha": sha, "tree_ids": ["t"]} for sha in commits],
            "ongoing": [{"tree_id": "t", "files": [{"path": p} for p in ongoing_files]}] if ongoing_files else []}


class DeliveryStateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_four_states_with_main_default(self):
        work = clone_with_default(self.root, "main")
        merged = commit(work, "merged.txt")
        git(work, "push", "origin", "main")
        git(work, "switch", "-c", "feat/x")
        pushed = commit(work, "pushed.txt")
        git(work, "push", "origin", "feat/x")
        local = commit(work, "local.txt")
        (work / "dirty.txt").write_text("dirty\n")
        view = remote_view(work)
        self.assertEqual(view["default_ref"], "refs/remotes/origin/main")
        self.assertEqual(view["default_basis"], "remote_head")
        self.assertEqual(commit_state(work, merged, view), "merged")
        self.assertEqual(commit_state(work, pushed, view), "pushed")
        self.assertEqual(commit_state(work, local, view), "local")
        self.assertEqual(unpushed_commits(work, view), [local])
        info = annotate_project(project_for(work, [merged, pushed, local], ["dirty.txt"]))
        self.assertEqual(info["commits"], {merged: "merged", pushed: "pushed", local: "local"})
        self.assertEqual((info["dirty_files"], info["unpushed"]), (1, [local]))
        self.assertEqual(info["errors"], [])

    def test_default_branch_not_main(self):
        work = clone_with_default(self.root, "trunk")
        git(work, "switch", "-c", "main")  # a branch *named* main is not the default
        on_main = commit(work, "on-main.txt")
        git(work, "push", "origin", "main")
        git(work, "switch", "trunk")
        on_trunk = commit(work, "on-trunk.txt")
        git(work, "push", "origin", "trunk")
        view = remote_view(work)
        self.assertEqual(view["default_ref"], "refs/remotes/origin/trunk")
        self.assertEqual(commit_state(work, on_trunk, view), "merged")
        self.assertEqual(commit_state(work, on_main, view), "pushed")

    def test_unknown_default_never_claims_merged(self):
        repo = init_repo(self.root / "repo")
        git(repo, "branch", "-m", "develop")
        bare = self.root / "r.git"
        git(self.root, "init", "--bare", str(bare))
        git(repo, "remote", "add", "origin", str(bare))
        sha = commit(repo, "a.txt")
        git(repo, "push", "origin", "develop")  # no origin/HEAD, no main/master
        view = remote_view(repo)
        self.assertIsNone(view["default_ref"])
        self.assertEqual(commit_state(repo, sha, view), "pushed")

    def test_no_remote_shows_only_first_two_states(self):
        repo = init_repo(self.root / "solo")
        sha = commit(repo, "a.txt")
        (repo / "b.txt").write_text("b\n")
        view = remote_view(repo)
        self.assertFalse(view["has_remote"])
        self.assertEqual(commit_state(repo, sha, view), "local")
        info = annotate_project(project_for(repo, [sha], ["b.txt"]))
        self.assertFalse(info["has_remote"])
        self.assertEqual(set(info["commits"].values()), {"local"})
        self.assertEqual(info["dirty_files"], 1)

    def test_reading_state_changes_nothing_in_repository(self):
        work = clone_with_default(self.root, "main")
        sha = commit(work, "x.txt")
        before = (git(work, "for-each-ref").stdout, git(work, "status", "--porcelain").stdout,
                  sorted(p.name for p in (work / ".git").iterdir()))
        annotate_project(project_for(work, [sha], []))
        after = (git(work, "for-each-ref").stdout, git(work, "status", "--porcelain").stdout,
                 sorted(p.name for p in (work / ".git").iterdir()))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
