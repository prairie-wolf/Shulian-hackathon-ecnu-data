"""Atomic file replacement and reentrant process/thread locks for local state."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import threading
import time

_guard = threading.Lock()
_locks = {}


@contextmanager
def file_lock(path, timeout=15):
    """Keep a stable lock file: unlinking it could split concurrent lock owners."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _guard:
        state = _locks.setdefault(str(path), {"lock": threading.RLock(), "depth": 0})
    with state["lock"]:
        if state["depth"]:
            state["depth"] += 1
            try:
                yield
            finally:
                state["depth"] -= 1
            return
        with open(path, "a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"等待文件锁超时：{path.name}")
                    time.sleep(0.05)
            state["depth"] = 1
            try:
                yield
            finally:
                state["depth"] = 0
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)


def atomic_write(path, data):
    """Serialize first, fsync a sibling temporary file, then replace the target."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
