import stat

import pytest

from backend.storage.workspace_lock import (
    WorkspaceLockError,
    workspace_mutation_lock,
)


def test_workspace_lock_is_reentrant_and_uses_a_regular_file(tmp_path) -> None:
    with workspace_mutation_lock(tmp_path):
        with workspace_mutation_lock(tmp_path):
            pass

    lock_path = tmp_path / ".workspace-mutation.lock"
    assert stat.S_ISREG(lock_path.lstat().st_mode)
    assert lock_path.read_bytes() == b"\0"


def test_workspace_lock_rejects_symlink_without_external_write(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    external_lock = tmp_path / "external-lock"
    external_lock.write_bytes(b"external sentinel")
    (root / ".workspace-mutation.lock").symlink_to(external_lock)

    with pytest.raises(WorkspaceLockError, match="non-symlink regular file"):
        with workspace_mutation_lock(root):
            pass

    assert external_lock.read_bytes() == b"external sentinel"


def test_workspace_lock_rejects_non_regular_leaf_before_open(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / ".workspace-mutation.lock").mkdir()

    with pytest.raises(WorkspaceLockError, match="non-symlink regular file"):
        with workspace_mutation_lock(root):
            pass

    assert (root / ".workspace-mutation.lock").is_dir()
