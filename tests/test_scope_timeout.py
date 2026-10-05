import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import git, init_repo, website_fixture_config
from xiaomao import scope
from xiaomao.inventory import effective_config


class ScopeTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_git_pointer_read_has_time_and_byte_bounds(self):
        pointer = self.root / '.git'
        pointer.write_text('gitdir: /tmp/synthetic\n')
        with patch('xiaomao.scope.subprocess.run', return_value=subprocess.CompletedProcess(
                [], 0, b'gitdir: /tmp/synthetic\n', b'')) as run:
            self.assertEqual(scope._read_git_pointer(pointer), 'gitdir: /tmp/synthetic')
        self.assertEqual(run.call_args.kwargs['timeout'], 2)
        self.assertFalse(run.call_args.kwargs['shell'])
        self.assertIn('4097', run.call_args.args[0][3])

    def test_timed_out_pointer_is_refused_before_git(self):
        repo = self.root / 'blocked'
        repo.mkdir()
        (repo / '.git').write_text('gitdir: /tmp/synthetic\n')
        with patch('xiaomao.scope.subprocess.run', side_effect=subprocess.TimeoutExpired([], 2)):
            self.assertIn('git_metadata_timeout', scope.worktree_exclusion_reason(str(repo)))

    def test_inaccessible_discovered_tree_is_visible_and_healthy_tree_remains(self):
        repo = init_repo(self.root / 'repo')
        blocked, healthy = self.root / 'blocked', self.root / 'healthy'
        git(repo, 'worktree', 'add', '--detach', str(blocked))
        git(repo, 'worktree', 'add', '--detach', str(healthy))
        cfg = website_fixture_config(self.root / 'home', repo)
        cfg.projects[0].worktree_policy = 'all'
        original = scope._read_git_pointer
        def read(path):
            if path.resolve() == (blocked / '.git').resolve():
                raise TimeoutError('synthetic permission wait')
            return original(path)
        with patch('xiaomao.scope._read_git_pointer', side_effect=read):
            result = effective_config(cfg)
        self.assertIn(str(healthy.resolve()), [t.path for p in result.projects for t in p.worktrees])
        self.assertNotIn(str(blocked.resolve()), [t.path for p in result.projects for t in p.worktrees])
        self.assertTrue(any(str(blocked.resolve()) in e and 'git_metadata_timeout' in e
                            for e in result._inventory_errors))

    def test_permission_wait_does_not_become_worktree_disappearance(self):
        import json
        from xiaomao.collect import scan_authorized
        from xiaomao.store import open_db
        repo = init_repo(self.root / 'repo')
        blocked = self.root / 'blocked'
        git(repo, 'worktree', 'add', '--detach', str(blocked))
        cfg = website_fixture_config(self.root / 'home', repo)
        cfg.projects[0].worktree_policy = 'all'
        original = scope._read_git_pointer
        def read(path):
            if path.resolve() == (blocked / '.git').resolve():
                raise TimeoutError('synthetic permission wait')
            return original(path)
        with open_db(self.root / 'home/xiaomao.sqlite') as conn:
            scan_authorized(conn, cfg)
            with patch('xiaomao.scope._read_git_pointer', side_effect=read):
                rows = scan_authorized(conn, cfg)
            self.assertEqual(conn.execute('SELECT gone FROM daily_trees WHERE path=?',
                             (str(blocked.resolve()),)).fetchone()[0], 0)
            self.assertTrue(any(r.get('status') == 'error' and 'git_metadata_timeout' in r.get('error', '')
                                for rs in rows.values() for r in rs))
            latest = conn.execute('SELECT results_json FROM daily_checks ORDER BY rowid DESC LIMIT 1').fetchone()[0]
            self.assertTrue(any(r['status'] == 'error' for r in json.loads(latest)))
