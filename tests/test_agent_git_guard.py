import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "agent_git_guard.py"
spec = importlib.util.spec_from_file_location("agent_git_guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GitGuardTests(unittest.TestCase):
    def test_local_commit_checks_target_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '--quiet', directory], check=True, capture_output=True)
            (root / 'example.txt').write_text('safe example', encoding='utf-8')
            subprocess.run(['git', '-C', directory, 'add', 'example.txt'], check=True, capture_output=True)
            for command in ['git commit', 'git commit -m "normal commit"', 'git commit --message=example --signoff']:
                with self.subTest(command=command):
                    self.assertIsNone(guard.check(command, directory))
            self.assertIsNone(guard.check('git -C "' + root.as_posix() + '" commit -m test'))
            (root / 'ai_clients.json').write_text('{}', encoding='utf-8')
            subprocess.run(['git', '-C', directory, 'add', 'ai_clients.json'], check=True, capture_output=True)
            self.assertIn('暂存区', guard.check('git commit -m test', directory))
            # The inspected commit was never executed.
            result = subprocess.run(['git', '-C', directory, 'rev-parse', '--verify', 'HEAD'], capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_commit_scope_options_denied(self):
        for command in ['git commit --amend', 'git commit --no-verify', 'git commit -am test',
                        'git commit -m test example.txt', 'git commit --only example.txt',
                        'git commit -m']:
            self.assertIsNotNone(guard.check(command))
        with tempfile.TemporaryDirectory() as directory:
            self.assertIn('无法读取', guard.check('git commit -m test', directory))

    def test_sensitive_paths(self):
        for path in ['data/private/u/graph.ttl', 'data/raw/uploads/a.csv', '.env', '.env.local',
                     '.streamlit/secrets.toml', 'data/users.json', 'private.key', '.pr_review/a.txt']:
            self.assertTrue(guard.sensitive_path(path), path)
        for path in ['.env.example', '.env.sample', '.codex/hooks.json', 'aiplatform/query.py']:
            self.assertFalse(guard.sensitive_path(path), path)

    def test_explicit_add(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '--quiet', directory], check=True, capture_output=True)
            (root / 'example.txt').write_text('safe fixture', encoding='utf-8')
            for command in ['git add example.txt', 'git add -- example.txt', 'git add --chmod=+x example.txt']:
                self.assertIsNone(guard.check(command, directory))
            for command in ['git add .', 'git add -A', 'git add -f example.txt',
                            'git add ai_clients.json', 'git add ../outside.txt', 'git add *.txt']:
                self.assertIsNotNone(guard.check(command, directory))

    def test_process_scoped_directory_trust(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '--quiet', directory], check=True, capture_output=True)
            self.assertIsNone(guard.check('git -c "safe.directory=' + root.as_posix() + '" commit -m test', directory))
        self.assertIsNotNone(guard.check('git -c safe.directory=* commit -m test'))
        self.assertIsNotNone(guard.check('git -c core.hooksPath=other commit -m test'))

    def test_read_only(self):
        for command in ['git status --short', 'git diff --check', 'git log -1 --oneline',
                        'git -C "a directory" show HEAD', 'git branch --show-current',
                        'git clean -n', 'git remote -v', 'python -m unittest']:
            with self.subTest(command=command):
                self.assertIsNone(guard.check(command))

    def test_reject_risky_and_ambiguous(self):
        for command in ['git reset --hard', 'git clean -fdx', 'git push --force-with-lease',
                        'git restore .', 'git checkout -- .', 'git branch -D main',
                        'git commit --amend', 'git merge other', 'git rebase main',
                        'git config alias.erase "reset --hard"', 'git erase',
                        'git status; git reset --hard', 'git status && git push',
                        '& git reset --hard', 'git -c alias.x=status x',
                        'git diff --output=somefile', 'git diff --ext-diff',
                        'git status > result.txt', 'git.exe reset --hard',
                        'git -C', 'git status "', 'git clean -n -f',
                        'python -c "import os; os.system(\'git reset --hard\')"']:
            with self.subTest(command=command):
                self.assertIsNotNone(guard.check(command))

    def test_protocol_and_no_secret_echo(self):
        payload = {'tool_input': {'cmd': 'git push https://secret-token@example.invalid/repo'}}
        result = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload),
                                text=True, capture_output=True, check=True)
        reply = json.loads(result.stdout)
        self.assertEqual(reply['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertIn('Git', reply['systemMessage'])
        self.assertNotIn('secret-token', result.stdout + result.stderr)

    def test_invalid_input_denied(self):
        for payload in ['{bad json', '{}', '{"tool_input": null}']:
            result = subprocess.run([sys.executable, str(SCRIPT)], input=payload,
                                    text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_allow_has_no_override(self):
        result = subprocess.run([sys.executable, str(SCRIPT)],
                                input=json.dumps({'tool_input': {'command': 'git status'}}),
                                text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
