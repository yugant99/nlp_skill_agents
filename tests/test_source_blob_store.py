from hashlib import sha256

import pytest

from backend.storage.source_blob_store import (
    SourceBlobIntegrityError,
    SourceBlobStore,
)


def test_source_blob_store_writes_deduplicates_and_verifies_content(tmp_path) -> None:
    store = SourceBlobStore(tmp_path)
    content = b"original source bytes\n"
    digest = sha256(content).hexdigest()

    first_path = store.store(content, digest)
    second_path = store.store(content, digest)

    assert first_path == second_path
    assert first_path == tmp_path / "source_blobs" / "sha256" / digest[:2] / (
        f"{digest}.blob"
    )
    assert store.read_verified(digest) == content


def test_source_blob_store_rejects_mismatch_invalid_hash_and_corruption(tmp_path) -> None:
    store = SourceBlobStore(tmp_path)
    content = b"original"
    digest = sha256(content).hexdigest()

    with pytest.raises(SourceBlobIntegrityError, match="does not match"):
        store.store(content, "0" * 64)
    with pytest.raises(SourceBlobIntegrityError, match="Invalid"):
        store.blob_path("../not-a-hash")

    path = store.store(content, digest)
    path.write_bytes(b"tampered")
    with pytest.raises(SourceBlobIntegrityError, match="failed"):
        store.read_verified(digest)
    with pytest.raises(SourceBlobIntegrityError, match="failed"):
        store.store(content, digest)
