from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


timestamp = load("commit_timestamp")
installer = load("install_git_hooks")


class TimestampTests(unittest.TestCase):
    def test_timezone_body_and_repeat(self):
        original = 'feat: 增加版本记录\r\n\r\nNotes: 已验证。\r\n'
        now = datetime(2026, 10, 5, 12, 34, 56, tzinfo=timezone.utc)
        result = timestamp.stamp(original, now)
        self.assertEqual(result, '[2026-10-05T20:34:56+08:00] ' + original)
        self.assertEqual(timestamp.stamp(result), result)
        self.assertEqual(timestamp.stamp('\n# comments\n'), '\n# comments\n')

    def make_repository(self, root):
        subprocess.run(['git', 'init', '--quiet', str(root)], check=True, capture_output=True)
        (root / '.githooks').mkdir()
        (root / 'scripts').mkdir()
        shutil.copy(ROOT / '.githooks' / 'prepare-commit-msg', root / '.githooks')
        shutil.copy(ROOT / 'scripts' / 'commit_timestamp.py', root / 'scripts')

    def test_real_git_hook_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            installer.install(root)
            installer.install(root)  # Safe to enable again.
            (root / 'example.txt').write_text('safe fixture', encoding='utf-8')
            subprocess.run(['git', '-C', str(root), 'add', 'example.txt'], check=True, capture_output=True)
            for title in ['feat: timestamp fixture', 'test: second commit']:
                # Separate processes exercise Git's actual hook discovery.
                result = subprocess.run(['git', '-C', str(root), '-c', 'user.name=Hook Test',
                    '-c', 'user.email=hook-test@example.invalid', 'commit', '--allow-empty',
                    '-m', title, '-m', 'Notes: temporary fixture only.'], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                message = subprocess.run(['git', '-C', str(root), 'log', '-1', '--format=%B'],
                    capture_output=True, text=True, check=True).stdout
                self.assertRegex(message, r'^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00\] ')
                self.assertIn(title, message)
                self.assertIn('Notes: temporary fixture only.', message)
                self.assertEqual(len(timestamp.PREFIX.findall(message)), 1)

    def test_installer_preserves_existing_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_repository(root)
            existing = root / '.git' / 'hooks' / 'pre-commit'
            existing.write_text('existing hook', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Existing Git hooks'):
                installer.install(root)
            self.assertEqual(existing.read_text(), 'existing hook')
            existing.unlink()
            subprocess.run(['git', '-C', str(root), 'config', '--local', 'core.hooksPath', 'other-hooks'],
                           check=True, capture_output=True)
            with self.assertRaisesRegex(ValueError, 'Existing hooksPath'):
                installer.install(root)
            self.assertEqual(installer.git(root, 'config', '--get', 'core.hooksPath'), 'other-hooks')


if __name__ == '__main__':
    unittest.main()
