from backend.evidence.identifiers import (
    cunit_evidence_id,
    passage_evidence_id,
    source_import_identity,
    transcript_evidence_identity,
)


def test_transcript_identity_is_content_addressed_and_stable() -> None:
    first = transcript_evidence_identity("P1: Same transcript.\n")
    repeated = transcript_evidence_identity("P1: Same transcript.\n")
    changed = transcript_evidence_identity("P1: Changed transcript.\n")

    assert first == repeated
    assert first != changed
    assert first.source_id.startswith("src_")
    assert first.transcript_revision_id.startswith("trv_")
    assert len(first.transcript_sha256) == 64
    assert int(first.transcript_sha256, 16) >= 0


def test_passage_and_cunit_ids_are_stable_within_a_revision() -> None:
    revision_id = transcript_evidence_identity("source").transcript_revision_id

    first_passage = passage_evidence_id(revision_id, 0)
    second_passage = passage_evidence_id(revision_id, 1)

    assert first_passage == passage_evidence_id(revision_id, 0)
    assert first_passage != second_passage
    assert cunit_evidence_id(first_passage, 0) == cunit_evidence_id(
        first_passage,
        0,
    )
    assert cunit_evidence_id(first_passage, 0) != cunit_evidence_id(
        first_passage,
        1,
    )


def test_source_import_identity_hashes_original_blob_bytes() -> None:
    content = "P1: extracted transcript"
    source_bytes = b"P1: extracted transcript\n"

    first = source_import_identity(
        content,
        source_bytes=source_bytes,
        source_media_type="text/plain",
    )
    repeated = source_import_identity(
        content,
        source_bytes=source_bytes,
        source_media_type="text/plain",
    )

    assert first.import_id.startswith("imp_")
    assert first.import_id != repeated.import_id
    assert first.source_blob_sha256 == repeated.source_blob_sha256
    assert first.source_blob_sha256 != transcript_evidence_identity(
        content
    ).transcript_sha256
    assert first.source_media_type == "text/plain"
