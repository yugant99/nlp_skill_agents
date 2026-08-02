from __future__ import annotations

import re
import stat
from hashlib import sha256
from pathlib import Path

from backend.storage.atomic import atomic_write_bytes
from backend.storage.workspace_lock import workspace_mutation_lock


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class EvidenceTextBlobIntegrityError(ValueError):
    pass


class EvidenceTextBlobStore:
    """Content-addressed storage for exact canonical UTF-8 evidence text."""

    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.blobs_dir = self.root / "evidence_text_blobs" / "sha256"

    def store(self, text: str, expected_sha256: str) -> Path:
        with workspace_mutation_lock(self.root):
            return self._store(text, expected_sha256)

    def _store(self, text: str, expected_sha256: str) -> Path:
        if not isinstance(text, str):
            raise EvidenceTextBlobIntegrityError(
                "Evidence text blob content must be a string"
            )
        path = self.blob_path(expected_sha256)
        try:
            content = text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise EvidenceTextBlobIntegrityError(
                "Evidence text blob content is not valid Unicode"
            ) from exc
        if sha256(content).hexdigest() != expected_sha256:
            raise EvidenceTextBlobIntegrityError(
                "Evidence text blob does not match expected SHA-256"
            )
        self._prepare_parent(path.parent)
        if path.exists() or path.is_symlink():
            stored = self.read_verified(expected_sha256)
            if stored != text:
                raise EvidenceTextBlobIntegrityError(
                    "Stored evidence text blob conflicts with expected content"
                )
            return path
        atomic_write_bytes(path, content)
        if self.read_verified(expected_sha256) != text:
            raise EvidenceTextBlobIntegrityError(
                "Stored evidence text blob conflicts with expected content"
            )
        return path

    def read_verified(self, expected_sha256: str) -> str:
        path = self.blob_path(expected_sha256)
        self._validate_parent(path.parent)
        try:
            file_stat = path.lstat()
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise EvidenceTextBlobIntegrityError(
                "Stored evidence text blob is unavailable"
            ) from exc
        if not stat.S_ISREG(file_stat.st_mode):
            raise EvidenceTextBlobIntegrityError(
                "Stored evidence text blob is not a non-symlink regular file"
            )
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise EvidenceTextBlobIntegrityError(
                "Stored evidence text blob is unavailable"
            ) from exc
        if sha256(content).hexdigest() != expected_sha256:
            raise EvidenceTextBlobIntegrityError(
                "Stored evidence text blob failed SHA-256 verification"
            )
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EvidenceTextBlobIntegrityError(
                "Stored evidence text blob is not valid UTF-8"
            ) from exc
        if text.encode("utf-8") != content:
            raise EvidenceTextBlobIntegrityError(
                "Stored evidence text blob is not canonical UTF-8"
            )
        return text

    def blob_path(self, expected_sha256: str) -> Path:
        if not isinstance(expected_sha256, str) or not _SHA256_PATTERN.fullmatch(
            expected_sha256
        ):
            raise EvidenceTextBlobIntegrityError(
                "Invalid evidence text blob SHA-256"
            )
        return (
            self.blobs_dir
            / expected_sha256[:2]
            / f"{expected_sha256}.utf8"
        )

    def _prepare_parent(self, parent: Path) -> None:
        for path in (
            self.root / "evidence_text_blobs",
            self.blobs_dir,
            parent,
        ):
            if path.exists() or path.is_symlink():
                try:
                    mode = path.lstat().st_mode
                except OSError as exc:
                    raise EvidenceTextBlobIntegrityError(
                        "Evidence text blob directory is unavailable"
                    ) from exc
                if not stat.S_ISDIR(mode):
                    raise EvidenceTextBlobIntegrityError(
                        "Evidence text blob directory is invalid"
                    )
            else:
                try:
                    path.mkdir()
                except FileExistsError:
                    if not stat.S_ISDIR(path.lstat().st_mode):
                        raise EvidenceTextBlobIntegrityError(
                            "Evidence text blob directory is invalid"
                        )
                except OSError as exc:
                    raise EvidenceTextBlobIntegrityError(
                        "Evidence text blob directory is unavailable"
                    ) from exc
        self._validate_parent(parent)

    def _validate_parent(self, parent: Path) -> None:
        for path in (
            self.root / "evidence_text_blobs",
            self.blobs_dir,
            parent,
        ):
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                raise
            except OSError as exc:
                raise EvidenceTextBlobIntegrityError(
                    "Evidence text blob directory is unavailable"
                ) from exc
            if not stat.S_ISDIR(mode):
                raise EvidenceTextBlobIntegrityError(
                    "Evidence text blob directory is invalid"
                )


def evidence_text_sha256(text: str) -> str:
    if not isinstance(text, str):
        raise EvidenceTextBlobIntegrityError(
            "Evidence text blob content must be a string"
        )
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise EvidenceTextBlobIntegrityError(
            "Evidence text blob content is not valid Unicode"
        ) from exc
    return sha256(encoded).hexdigest()
