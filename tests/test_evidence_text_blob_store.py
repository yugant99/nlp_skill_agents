from hashlib import sha256

import pytest

from backend.storage.evidence_text_blob_store import (
    EvidenceTextBlobIntegrityError,
    EvidenceTextBlobStore,
    evidence_text_sha256,
)


def test_evidence_text_blob_store_round_trips_exact_unicode(tmp_path) -> None:
    store = EvidenceTextBlobStore(tmp_path)
    text = "Exact café 🧪\nsecond line"
    digest = evidence_text_sha256(text)

    first_path = store.store(text, digest)
    repeated_path = store.store(text, digest)

    assert first_path == repeated_path
    assert store.read_verified(digest) == text
    assert first_path.read_bytes() == text.encode("utf-8")


def test_evidence_text_blob_store_rejects_non_utf8_and_symlinks(tmp_path) -> None:
    store = EvidenceTextBlobStore(tmp_path)
    invalid_bytes = b"\xff\xfe"
    invalid_digest = sha256(invalid_bytes).hexdigest()
    invalid_path = store.blob_path(invalid_digest)
    invalid_path.parent.mkdir(parents=True)
    invalid_path.write_bytes(invalid_bytes)

    with pytest.raises(EvidenceTextBlobIntegrityError, match="valid UTF-8"):
        store.read_verified(invalid_digest)

    text = "safe"
    digest = evidence_text_sha256(text)
    path = store.blob_path(digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "target.txt"
    target.write_text(text, encoding="utf-8")
    path.symlink_to(target)

    with pytest.raises(EvidenceTextBlobIntegrityError, match="non-symlink"):
        store.read_verified(digest)
