import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


class CheckGateTests(unittest.TestCase):
    def test_unreachable_gateway_fails_gate(self):
        bash = os.environ.get("BASH_EXE") or (
            str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe")
            if os.name == "nt" else shutil.which("bash"))
        if not bash or not Path(bash).exists():
            self.skipTest("Bash unavailable")
        env = dict(os.environ, PYTHON=sys.executable.replace("\\", "/"))
        result = subprocess.run([bash, "check.sh", "http://127.0.0.1:1"],
                                cwd=Path(__file__).resolve().parents[1], env=env,
                                capture_output=True, timeout=90)
        self.assertNotEqual(result.returncode, 0, result.stdout.decode("utf-8", "replace"))
        self.assertIn(b"FAIL=", result.stdout)


if __name__ == "__main__":
    unittest.main()
