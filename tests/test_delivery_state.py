"""Four delivery states on synthetic Git repositories with local bare remotes."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.helpers import git, init_repo
from xiaomao.delivery_state import (annotate_project, commit_state, merged_by_content, remote_view,
                                    unpushed_commits)
from xiaomao.git_readonly import GitReadError
from xiaomao.inventory import metadata


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
    return {"repo_id": metadata(str(repo))["repo_id"],
            "trees": [{"tree_id": "t", "path": str(repo), "head": head, "gone": False}],
            "commits": [{"sha": sha, "tree_ids": ["t"]} for sha in commits],
            "ongoing": [{"tree_id": "t", "files": [{"path": p} for p in ongoing_files]}] if ongoing_files else []}


class DeliveryStateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def independent_copies(self):
        fresh = clone_with_default(self.root, "main")
        stale = self.root / "stale"
        bare = self.root / "remote.git"
        git(self.root, "clone", str(bare), str(stale))
        git(fresh, "remote", "set-url", "--push", "origin", str(bare))
        for checkout in (fresh, stale):
            git(checkout, "remote", "set-url", "origin", "https://github.com/xoqiu09/fixture.git")
        return fresh, stale

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

    def test_evening_report_reconciles_fresh_and_stale_copies_in_either_order(self):
        from xiaomao.delivery_state import annotate_bundle, unpushed_count
        from xiaomao.feature_render import plain_next_steps, render_plain_summary

        fresh, stale = self.independent_copies()
        now = datetime(2026, 11, 28, 13, 30, tzinfo=timezone.utc)
        with patch.dict(os.environ, {"GIT_COMMITTER_DATE": (now - timedelta(hours=1)).isoformat()}):
            sha = commit(fresh, "notes.txt")
        git(stale, "fetch", str(fresh), "main")
        git(stale, "reset", "--hard", sha)
        self.assertEqual(unpushed_commits(stale, remote_view(stale)), [sha])

        for branch, state, label in (("notes", "pushed", "🔵 已推送"), ("main", "merged", "🟢 已合并")):
            git(fresh, "push", "origin", f"HEAD:refs/heads/{branch}")
            reports = []
            for copies in ((fresh, stale), (stale, fresh)):
                with self.subTest(state=state, first=copies[0].name):
                    trees = [dict(project_for(copy, [], [])["trees"][0], tree_id=copy.name) for copy in copies]
                    project = {
                        "project_id": "notes", "repo_id": metadata(str(fresh))["repo_id"],
                        "display_name": "笔记", "changed": True,
                        "trees": trees, "activity": [], "ongoing": [], "gaps": [], "limitations": [],
                        "commits": [{"sha": sha, "subject": "添加读书笔记", "evidence_id": f"commit:notes:{sha}",
                                     "tree_ids": [copy.name for copy in copies]}],
                    }
                    bundle = {"window": {"date": "2026-11-28", "end": now.isoformat()},
                              "inventory_errors": [], "projects": [project]}
                    annotate_bundle(bundle)
                    text = render_plain_summary(bundle, {})
                    reports.append(text)
                    self.assertEqual(unpushed_count(project["delivery_state"]), (0, False))
                    self.assertIn(f"添加读书笔记  {label}", text)
                    self.assertNotIn("提交还没推送", text)
                    self.assertFalse(any("推送本地" in step for step in plain_next_steps(bundle)))

                    project["commits"] = []
                    annotate_bundle(bundle)
                    self.assertEqual(unpushed_count(project["delivery_state"]), (0, False))
            with self.subTest(state=state, check="copy order"):
                self.assertEqual(reports[0], reports[1])

    def test_fork_in_registered_slot_does_not_lend_remote_refs(self):
        work, fork = self.independent_copies()
        sha = commit(work, "notes.txt")
        git(fork, "fetch", str(work), "main")
        git(fork, "update-ref", "refs/remotes/origin/main", "FETCH_HEAD")
        git(fork, "remote", "set-url", "origin", "https://github.com/xoqiu09/fixture-fork.git")
        self.assertEqual(commit_state(fork, sha, remote_view(fork)), "merged")

        project = project_for(work, [sha], [])
        # Daily bundles keep a placeholder for registered paths whose identity has changed.
        project["trees"].append(dict(tree_id="fork", path=str(fork), head=None, status="unobserved"))
        info = annotate_project(project)

        self.assertEqual((info["commits"][sha], info["unpushed"]), ("local", [sha]))

    def test_content_merge_uses_each_copys_default_branch_in_either_order(self):
        fresh, stale = self.independent_copies()
        git(stale, "switch", "-c", "notes")
        sha = commit(stale, "notes.txt")
        git(fresh, "fetch", str(stale), "notes")
        git(fresh, "switch", "-c", "trunk")
        git(fresh, "cherry-pick", "-x", sha)
        git(fresh, "update-ref", "refs/remotes/origin/trunk", "HEAD")
        git(fresh, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        self.assertFalse(merged_by_content(stale, sha, remote_view(stale)))
        self.assertTrue(merged_by_content(fresh, sha, remote_view(fresh)))

        for first, second in ((stale, fresh), (fresh, stale)):
            with self.subTest(first=first.name):
                project = project_for(first, [sha], [])
                project["trees"].append(dict(project_for(second, [], [])["trees"][0], tree_id="second"))
                info = annotate_project(project)
                self.assertEqual((info["commits"][sha], info["unpushed"], info["merged_by_content"]),
                                 ("merged", [], [sha]))
                with (patch("xiaomao.delivery_state.MAX_CONTENT_CHECKS", 1),
                      patch("xiaomao.delivery_state.merged_by_content", wraps=merged_by_content) as check):
                    limited = annotate_project(project)
                self.assertEqual(check.call_count, 1)
                self.assertEqual(limited["unpushed_unchecked"], [sha])
                self.assertIn("content_check_truncated", limited["errors"])

    def test_remote_read_failure_in_one_copy_keeps_other_copys_merge_evidence(self):
        fresh, stale = self.independent_copies()
        sha = commit(fresh, "notes.txt")
        git(stale, "fetch", str(fresh), "main")
        git(stale, "reset", "--hard", sha)
        git(fresh, "push", "origin", "main")
        self.assertEqual(unpushed_commits(stale, remote_view(stale)), [sha])

        def fail_on_stale_copy(path):
            if path.resolve() == stale.resolve():
                raise GitReadError("temporary remote read failure")
            return remote_view(path)

        for first, second in ((stale, fresh), (fresh, stale)):
            with self.subTest(first=first.name):
                project = project_for(first, [sha], [])
                project["trees"][0]["tree_id"] = first.name
                project["trees"].append(dict(project_for(second, [], [])["trees"][0], tree_id=second.name))
                project["commits"][0]["tree_ids"] = [first.name, second.name]
                with patch("xiaomao.delivery_state.remote_view", side_effect=fail_on_stale_copy):
                    info = annotate_project(project)
                self.assertEqual((info["commits"].get(sha), info["unpushed"]), ("merged", []))
                self.assertIn("remote_view_unavailable:stale", info["errors"])

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

    def test_rebase_or_squash_merge_reads_as_merged(self):
        """GitHub squash/rebase lands the same change on main under a new SHA."""
        work = clone_with_default(self.root, "main")
        git(work, "switch", "-c", "feat/a")
        pushed = commit(work, "a.txt")
        git(work, "push", "origin", "feat/a")
        git(work, "switch", "-c", "feat/b", "main")
        gone = commit(work, "b.txt")          # pushed, merged, remote branch deleted
        git(work, "switch", "main")
        git(work, "cherry-pick", "-x", pushed, gone)
        git(work, "push", "origin", "main")
        landed = git(work, "rev-parse", "HEAD~1", "HEAD").stdout.split()
        git(work, "switch", "feat/b")
        view = remote_view(work)
        self.assertEqual(commit_state(work, pushed, view), "pushed")   # ancestry alone
        self.assertTrue(merged_by_content(work, pushed, view))
        info = annotate_project(project_for(work, [*landed, pushed], []))
        self.assertEqual(info["commits"][pushed], "merged")
        self.assertEqual(info["unpushed"], [])                         # gone is not "push this"
        self.assertEqual(sorted(info["merged_by_content"]), sorted([pushed, gone]))

    def test_squash_of_several_commits_keeps_ancestry_state(self):
        work = clone_with_default(self.root, "main")
        git(work, "switch", "-c", "feat/many")
        first, second = commit(work, "m1.txt"), commit(work, "m2.txt")
        git(work, "push", "origin", "feat/many")
        git(work, "switch", "main")
        git(work, "merge", "--squash", "feat/many")
        git(work, "commit", "-m", "squashed (#7)")
        git(work, "push", "origin", "main")
        info = annotate_project(project_for(work, [first, second], []))
        self.assertEqual(set(info["commits"].values()), {"pushed"})    # recorded limit

    def test_old_unpushed_commits_do_not_drive_the_push_reminder(self):
        work = clone_with_default(self.root, "main")
        git(work, "switch", "-c", "old/idea")
        with patch.dict(os.environ, {"GIT_COMMITTER_DATE": "2026-09-01T10:00:00+08:00"}):
            old = commit(work, "old.txt")
        git(work, "switch", "-c", "new/idea", "main")
        new = commit(work, "new.txt")
        old_tree = self.root / "old-tree"
        git(work, "worktree", "add", str(old_tree), "old/idea")
        project = project_for(work, [], [])
        project["trees"].append({"tree_id": "o", "path": str(old_tree), "head": old, "gone": False})
        info = annotate_project(project, now=datetime.now(timezone.utc))
        self.assertEqual(info["unpushed"], [new])
        self.assertEqual(info["unpushed_stale"], [old])

    def test_capped_branch_queries_keep_each_worktree_head(self):
        work = clone_with_default(self.root, "main")
        first_tree = self.root / "first-tree"
        git(work, "worktree", "add", "-b", "first", str(first_tree))
        now = datetime.now(timezone.utc)
        with patch.dict(os.environ, {"GIT_COMMITTER_DATE": (now - timedelta(minutes=2)).isoformat()}):
            first = commit(first_tree, "first.txt")
        git(work, "switch", "-c", "second")
        second = commit(work, "second.txt")
        project = project_for(work, [], [])
        project["trees"].append({"tree_id": "first", "path": str(first_tree), "head": first})

        with patch("xiaomao.delivery_state.MAX_UNPUSHED", 1):
            info = annotate_project(project)

        self.assertEqual(info["unpushed"], sorted([first, second]))

    def test_truncated_branch_query_marks_push_reminder_as_a_lower_bound(self):
        from xiaomao.daily_jobs import notification_text
        from xiaomao.feature_render import plain_next_steps

        work = clone_with_default(self.root, "main")
        git(work, "switch", "-c", "notes")
        commit(work, "first.txt")
        second = commit(work, "second.txt")
        git(work, "switch", "main")
        project = dict(project_for(work, [], []), display_name="笔记", changed=False)
        bundle = {"projects": [project]}
        with patch("xiaomao.delivery_state.MAX_UNPUSHED", 1):
            info = annotate_project(project)

        self.assertEqual(info["unpushed"], [second])
        with self.subTest(surface="通知"):
            self.assertIn("至少 1 个提交还没推送", notification_text(bundle)[0])
        with self.subTest(surface="明天从这里继续"):
            self.assertEqual(plain_next_steps(bundle), ["笔记：推送本地的至少 1 个提交"])

    def test_merge_checks_prioritize_recent_push_candidates_and_record_unchecked_ones(self):
        from xiaomao.daily import delivery_lines
        from xiaomao.daily_jobs import notification_text

        work = clone_with_default(self.root, "main")
        now = datetime.now(timezone.utc)
        old_a, old_b, recent_merged, recent_local, recent_unchecked = [ch * 40 for ch in "abcde"]
        candidates = [old_a, old_b, recent_merged, recent_local, recent_unchecked]
        times = {sha: now - timedelta(days=30) if sha in (old_a, old_b) else now for sha in candidates}
        project = dict(project_for(work, [], []), display_name="笔记", changed=False)
        with (patch("xiaomao.delivery_state.MAX_CONTENT_CHECKS", 2),
              patch("xiaomao.delivery_state.unpushed_commits",
                    side_effect=lambda path, view, **kwargs: [] if kwargs.get("other_branches") else candidates),
              patch("xiaomao.delivery_state.commit_times", side_effect=lambda _, shas: {s: times[s] for s in shas}),
              patch("xiaomao.delivery_state.merged_by_content",
                    side_effect=lambda _, sha, view: sha == recent_merged) as check):
            info = annotate_project(project, now)

        with self.subTest(check="先核对近期提交"):
            self.assertEqual([call.args[1] for call in check.call_args_list], [recent_merged, recent_local])
        with self.subTest(check="只提醒已核对且仍未推送的提交"):
            self.assertEqual(info["unpushed"], [recent_local])
            self.assertIn("至少 1 个提交还没推送", notification_text({"projects": [project]})[0])
        with self.subTest(check="未核对的提交留在依据"):
            self.assertEqual(info.get("unpushed_unchecked"), [old_a, old_b, recent_unchecked])
            self.assertIn(recent_unchecked[:12], "\n".join(delivery_lines(info)))

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
