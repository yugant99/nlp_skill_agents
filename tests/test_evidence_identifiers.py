from backend.evidence.identifiers import (
    cunit_evidence_id,
    passage_evidence_id,
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
    assert len(first.source_sha256) == 64
    assert int(first.source_sha256, 16) >= 0


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
