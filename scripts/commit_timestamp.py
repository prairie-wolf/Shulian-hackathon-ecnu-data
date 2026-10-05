"""Add an identifiable Taipei timestamp to a Git commit subject."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import sys
import tempfile


TAIPEI = timezone(timedelta(hours=8), name="Asia/Taipei")
PREFIX = re.compile(r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00\] ")


def stamp(message, now=None):
    """Preserve subject/body and keep repeat hook invocations idempotent."""
    lines = message.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith(("#", ";")) or not line.strip():
            continue
        if PREFIX.match(line):
            return message
        instant = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
        lines[index] = f"[{instant.isoformat(timespec='seconds')}] " + line
        break
    return "".join(lines)


def main():
    try:
        path = Path(sys.argv[1])
        # Git owns the message file. Replace it atomically, using its directory
        # so failures never truncate an existing message; Git serializes commits.
        with path.open("r", encoding="utf-8", newline="") as handle:
            original = handle.read()
        updated = stamp(original)
        if updated != original:
            descriptor, name = tempfile.mkstemp(prefix="commit-msg-", dir=path.parent)
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                    handle.write(updated)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return 0
    except (IndexError, OSError, UnicodeError) as error:
        print(f"Commit timestamp hook failed ({type(error).__name__}); commit aborted.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
