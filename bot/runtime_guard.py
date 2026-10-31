"""Single-instance guard for the JobBlast polling process."""

from __future__ import annotations

import json
import os
from pathlib import Path


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class SingleInstanceGuard:
    """Acquire a lock file so only one local polling instance can run."""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.pid = os.getpid()
        self.acquired = False

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                owner_pid = self._read_owner_pid()
                if owner_pid and _pid_exists(owner_pid):
                    raise RuntimeError(f"JobBlast is already running locally with PID {owner_pid}. Stop that process before starting another instance.")
                self._remove_stale_lock()
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"pid": self.pid}, handle)
            self.acquired = True
            return

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            owner_pid = self._read_owner_pid()
            if owner_pid in {None, self.pid} and self.lock_path.exists():
                self.lock_path.unlink()
        finally:
            self.acquired = False

    def _read_owner_pid(self) -> int | None:
        if not self.lock_path.exists():
            return None
        try:
            payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
            return int(payload.get("pid") or 0) or None
        except Exception:
            return None

    def _remove_stale_lock(self) -> None:
        try:
            if self.lock_path.exists():
                self.lock_path.unlink()
        except FileNotFoundError:
            return
