"""Explicitly install this project's versioned Git hooks without replacing others."""
import os
from pathlib import Path
import subprocess
import sys


def git(root, *args):
    return subprocess.run(["git", "-c", "safe.directory=" + str(root), "-C", str(root), *args], capture_output=True,
                          text=True, encoding="utf-8", check=True).stdout.strip()


def install(root):
    root = Path(root).resolve()
    hook = root / ".githooks" / "prepare-commit-msg"
    if not hook.is_file() or not (root / "scripts" / "commit_timestamp.py").is_file():
        raise ValueError("Missing project timestamp hook files.")
    result = subprocess.run(["git", "-c", "safe.directory=" + str(root), "-C", str(root), "config", "--get", "core.hooksPath"],
                            capture_output=True, text=True, encoding="utf-8")
    if result.returncode not in {0, 1}:
        raise ValueError("Cannot inspect existing hooksPath.")
    if result.returncode == 0 and result.stdout.strip() != ".githooks":
        raise ValueError("Existing hooksPath is configured; refusing to replace it.")
    if result.returncode == 1:
        old = Path(git(root, "rev-parse", "--git-path", "hooks"))
        if not old.is_absolute():
            old = root / old
        if old.exists() and any(p.is_file() and not p.name.endswith(".sample") for p in old.iterdir()):
            raise ValueError("Existing Git hooks found; integrate them before enabling timestamp hook.")
    if os.name != "nt":
        hook.chmod(hook.stat().st_mode | 0o111)
    git(root, "config", "--local", "core.hooksPath", ".githooks")
    return root


def main():
    try:
        install(Path(__file__).resolve().parents[1])
        print("Enabled project Git hooks: .githooks (prepare-commit-msg, Asia/Taipei).")
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Git hook installation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
