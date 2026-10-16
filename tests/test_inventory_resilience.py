import tempfile
import unittest
from pathlib import Path

from tests.helpers import git, init_repo, website_fixture_config
from xiaomao.inventory import effective_config


class InventoryResilienceTests(unittest.TestCase):
    def test_broken_tree_does_not_hide_later_healthy_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_repo(root / 'main')
            broken, healthy = root / 'a-broken', root / 'z-healthy'
            git(repo, 'worktree', 'add', '--detach', str(broken))
            git(repo, 'worktree', 'add', '--detach', str(healthy))
            (broken / '.git').write_text('gitdir: ' + str(root / 'missing-metadata') + '\n')
            cfg = website_fixture_config(root / 'home', repo)
            cfg.projects[0].worktree_policy = 'all'
            effective = effective_config(cfg)
            paths = {Path(w.path).resolve() for w in effective.projects[0].worktrees}
            self.assertIn(healthy.resolve(), paths)
            self.assertNotIn(broken.resolve(), paths)
            self.assertTrue(any(str(broken) in error for error in effective._inventory_errors))

    def test_verified_registered_repository_tree_is_not_unknown_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_repo(root / 'main')
            linked = root / 'linked'
            git(repo, 'worktree', 'add', '--detach', str(linked))
            cfg = website_fixture_config(root / 'home', repo)
            cfg.projects[0].worktree_policy = 'all'
            cfg.personal_roots = [str(root)]
            effective = effective_config(cfg)
            self.assertIn(linked.resolve(), {Path(w.path).resolve() for w in effective.projects[0].worktrees})
            self.assertNotIn(str(linked.resolve()), {c['path'] for c in effective._inventory_candidates})
