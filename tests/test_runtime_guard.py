import shutil
import unittest
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from bot.runtime_guard import SingleInstanceGuard

TEST_TMP_DIR = Path(__file__).resolve().parent / ".tmp"
TEST_TMP_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def temp_dir():
    path = TEST_TMP_DIR / f"guard-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class RuntimeGuardTests(unittest.TestCase):
    def test_guard_blocks_second_live_instance(self):
        with temp_dir() as temp_path:
            lock_path = temp_path / "jobblast.lock"
            first = SingleInstanceGuard(lock_path)
            first.acquire()
            second = SingleInstanceGuard(lock_path)
            try:
                with self.assertRaises(RuntimeError):
                    second.acquire()
            finally:
                first.release()

    def test_guard_replaces_stale_lock(self):
        with temp_dir() as temp_path:
            lock_path = temp_path / "jobblast.lock"
            lock_path.write_text('{"pid": 999999}', encoding="utf-8")
            guard = SingleInstanceGuard(lock_path)
            guard.acquire()
            try:
                self.assertTrue(lock_path.exists())
            finally:
                guard.release()


if __name__ == "__main__":
    unittest.main()
