from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock, local
from typing import BinaryIO


_LOCKS_GUARD = Lock()
_THREAD_LOCKS: dict[str, RLock] = {}
_LOCK_STATE = local()


@contextmanager
def workspace_mutation_lock(root: Path | str) -> Iterator[None]:
    """Serialize writes that span shared workspace stores and study files."""

    lock_path = Path(root).resolve() / ".workspace-mutation.lock"
    lock_key = str(lock_path)
    with _LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(lock_key, RLock())

    with thread_lock:
        held_locks = getattr(_LOCK_STATE, "held_locks", None)
        if held_locks is None:
            held_locks = {}
            _LOCK_STATE.held_locks = held_locks
        held = held_locks.get(lock_key)
        if held is not None:
            held[0] += 1
            try:
                yield
            finally:
                held[0] -= 1
            return

        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = lock_path.open("a+b")
        try:
            _acquire_process_lock(lock_file)
            held_locks[lock_key] = [1, lock_file]
            try:
                yield
            finally:
                held_locks.pop(lock_key, None)
                _release_process_lock(lock_file)
        finally:
            lock_file.close()


def _acquire_process_lock(lock_file: BinaryIO) -> None:
    if lock_file.seek(0, os.SEEK_END) == 0:
        lock_file.write(b"\0")
        lock_file.flush()
        os.fsync(lock_file.fileno())
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)


def _release_process_lock(lock_file: BinaryIO) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
