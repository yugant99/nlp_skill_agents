from __future__ import annotations

import re
import stat
from hashlib import sha256
from pathlib import Path

from backend.storage.atomic import atomic_write_bytes
from backend.storage.workspace_lock import workspace_mutation_lock


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class SourceBlobIntegrityError(ValueError):
    pass


class SourceBlobStore:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.blobs_dir = self.root / "source_blobs" / "sha256"

    def store(self, content: bytes, expected_sha256: str) -> Path:
        with workspace_mutation_lock(self.root):
            return self._store(content, expected_sha256)

    def _store(self, content: bytes, expected_sha256: str) -> Path:
        path = self.blob_path(expected_sha256)
        actual_sha256 = sha256(content).hexdigest()
        if actual_sha256 != expected_sha256:
            raise SourceBlobIntegrityError("Source blob does not match expected SHA-256")
        if path.exists():
            self.read_verified(expected_sha256)
            return path
        atomic_write_bytes(path, content)
        self.read_verified(expected_sha256)
        return path

    def read_verified(self, expected_sha256: str) -> bytes:
        path = self.blob_path(expected_sha256)
        file_stat = path.lstat()
        if not stat.S_ISREG(file_stat.st_mode):
            raise SourceBlobIntegrityError(
                "Stored source blob is not a non-symlink regular file"
            )
        content = path.read_bytes()
        if sha256(content).hexdigest() != expected_sha256:
            raise SourceBlobIntegrityError("Stored source blob failed SHA-256 verification")
        return content

    def blob_path(self, expected_sha256: str) -> Path:
        if not _SHA256_PATTERN.fullmatch(expected_sha256):
            raise SourceBlobIntegrityError("Invalid source blob SHA-256")
        return self.blobs_dir / expected_sha256[:2] / f"{expected_sha256}.blob"
