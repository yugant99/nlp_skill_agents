from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from uuid import uuid4


@dataclass(frozen=True)
class TranscriptEvidenceIdentity:
    source_id: str
    transcript_sha256: str
    transcript_revision_id: str


@dataclass(frozen=True)
class SourceImportIdentity:
    import_id: str
    source_blob_sha256: str
    source_media_type: str


def transcript_evidence_identity(content: str) -> TranscriptEvidenceIdentity:
    transcript_sha256 = sha256(content.encode("utf-8")).hexdigest()
    return TranscriptEvidenceIdentity(
        source_id=f"src_{transcript_sha256[:32]}",
        transcript_sha256=transcript_sha256,
        transcript_revision_id=f"trv_{transcript_sha256[:32]}",
    )


def source_import_identity(
    content: str,
    *,
    source_bytes: bytes | None = None,
    source_media_type: str = "text/plain",
) -> SourceImportIdentity:
    blob = source_bytes if source_bytes is not None else content.encode("utf-8")
    return SourceImportIdentity(
        import_id=f"imp_{uuid4().hex}",
        source_blob_sha256=sha256(blob).hexdigest(),
        source_media_type=source_media_type or "application/octet-stream",
    )


def passage_evidence_id(transcript_revision_id: str, passage_index: int) -> str:
    return _derived_id("psg", transcript_revision_id, passage_index)


def cunit_evidence_id(passage_id: str, cunit_ordinal: int) -> str:
    return _derived_id("cun", passage_id, cunit_ordinal)


def _derived_id(prefix: str, parent_id: str, ordinal: int) -> str:
    if not parent_id:
        raise ValueError("parent_id must be non-empty")
    if ordinal < 0:
        raise ValueError("ordinal must be non-negative")
    digest = sha256(f"{parent_id}\0{ordinal}".encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:32]}"
