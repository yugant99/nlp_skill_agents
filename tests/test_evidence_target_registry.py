from dataclasses import replace
import sqlite3

import pytest

from backend.evidence.identifiers import (
    cunit_evidence_id,
    passage_evidence_id,
    transcript_evidence_identity,
)
from backend.storage.evidence_catalog import EvidenceCatalog, EvidenceImportRecord
from backend.storage.evidence_target_registry import (
    EvidenceCUnitInput,
    EvidencePassageInput,
    EvidenceTargetBlobConflict,
    EvidenceTargetConflictError,
    EvidenceTargetNotFoundError,
    EvidenceTargetRegistry,
    EvidenceTargetValidationError,
)


def _registered_set(tmp_path):
    transcript_text = "P1: I came and I stayed."
    identity = transcript_evidence_identity(transcript_text)
    record = EvidenceImportRecord(
        import_id="imp_registry",
        run_id="run_registry",
        pipeline="segmentation",
        source_id=identity.source_id,
        source_filename="registry.txt",
        source_media_type="text/plain",
        source_blob_sha256="a" * 64,
        transcript_revision_id=identity.transcript_revision_id,
        transcript_sha256=identity.transcript_sha256,
        imported_at="2026-08-01T12:00:00+00:00",
        project_source_id="psrc_registry",
        workspace_id="study-registry",
    )
    EvidenceCatalog(tmp_path).record_import(record)
    passage_id = passage_evidence_id(identity.transcript_revision_id, 0)
    registry = EvidenceTargetRegistry(tmp_path)
    prepared = registry.prepare_complete_set(
        import_id=record.import_id,
        workspace_id=record.workspace_id,
        project_source_id=record.project_source_id,
        transcript_revision_id=record.transcript_revision_id,
        transcript_text=transcript_text,
        producer_kind="cunit_segmentation",
        producer_version=1,
        producer_status="verified",
        review_status="not_domain_validated",
        passages=(
            EvidencePassageInput(
                passage_id=passage_id,
                passage_ordinal=0,
                role="participant",
                text="I came and I stayed.",
                cunits=(
                    EvidenceCUnitInput(
                        cunit_id=cunit_evidence_id(passage_id, 0),
                        cunit_ordinal=0,
                        text="I came",
                    ),
                    EvidenceCUnitInput(
                        cunit_id=cunit_evidence_id(passage_id, 1),
                        cunit_ordinal=1,
                        text="and I stayed.",
                    ),
                ),
            ),
        ),
    )
    return registry, record, prepared


def test_registry_prepares_registers_resolves_and_replays_exactly(tmp_path) -> None:
    registry, record, prepared = _registered_set(tmp_path)

    stored = registry.register_complete_set(prepared)
    repeated = registry.register_complete_set(prepared)
    passage = registry.resolve(
        record.workspace_id,
        record.project_source_id,
        record.transcript_revision_id,
        prepared.evidence_set_id,
        prepared.passages[0].passage_id,
    )
    cunit = registry.resolve(
        record.workspace_id,
        record.project_source_id,
        record.transcript_revision_id,
        prepared.evidence_set_id,
        prepared.passages[0].passage_id,
        prepared.passages[0].cunits[1].cunit_id,
    )

    assert stored == repeated
    assert stored.evidence_set_id == prepared.evidence_set_id
    assert stored.created_at == record.imported_at
    assert stored.to_manifest() == prepared.to_manifest()
    assert passage.target_kind == "passage"
    assert passage.text == "I came and I stayed."
    assert cunit.target_kind == "cunit"
    assert cunit.text == "and I stayed."
    assert registry.workspace_snapshot(record.workspace_id) == (stored,)


def test_registry_rejects_cunits_that_do_not_match_current_producer(tmp_path) -> None:
    registry, record, prepared = _registered_set(tmp_path)
    passage = prepared.passages[0]

    with pytest.raises(
        EvidenceTargetValidationError,
        match="current producer",
    ):
        registry.prepare_complete_set(
            import_id=record.import_id,
            workspace_id=record.workspace_id,
            project_source_id=record.project_source_id,
            transcript_revision_id=record.transcript_revision_id,
            transcript_text=prepared.transcript_text,
            producer_kind="cunit_segmentation",
            producer_version=1,
            producer_status="verified",
            review_status="not_domain_validated",
            passages=(
                EvidencePassageInput(
                    passage_id=passage.passage_id,
                    passage_ordinal=passage.passage_ordinal,
                    role=passage.role,
                    text=passage.text,
                    cunits=(
                        EvidenceCUnitInput(
                            cunit_id=passage.cunits[0].cunit_id,
                            cunit_ordinal=0,
                            text="FORGED ONE",
                        ),
                        EvidenceCUnitInput(
                            cunit_id=passage.cunits[1].cunit_id,
                            cunit_ordinal=1,
                            text="FORGED TWO",
                        ),
                    ),
                ),
            ),
        )


def test_registry_rejects_fabricated_ids_and_wrong_ownership(tmp_path) -> None:
    registry, record, prepared = _registered_set(tmp_path)

    with pytest.raises(EvidenceTargetValidationError, match="passage_id"):
        registry.prepare_complete_set(
            import_id=record.import_id,
            workspace_id=record.workspace_id,
            project_source_id=record.project_source_id,
            transcript_revision_id=record.transcript_revision_id,
            transcript_text="P1: I came and I stayed.",
            producer_kind="analysis_turns",
            producer_version=1,
            producer_status="verified",
            review_status="not_applicable",
            passages=(
                EvidencePassageInput(
                    passage_id=f"psg_{'0' * 32}",
                    passage_ordinal=0,
                    role="participant",
                    text="fabricated",
                ),
            ),
        )

    registry.register_complete_set(prepared)
    with pytest.raises(EvidenceTargetConflictError, match="ownership"):
        registry.resolve(
            "another-study",
            record.project_source_id,
            record.transcript_revision_id,
            prepared.evidence_set_id,
            prepared.passages[0].passage_id,
        )
    with pytest.raises(EvidenceTargetNotFoundError, match="C-unit"):
        registry.resolve(
            record.workspace_id,
            record.project_source_id,
            record.transcript_revision_id,
            prepared.evidence_set_id,
            prepared.passages[0].passage_id,
            cunit_evidence_id(prepared.passages[0].passage_id, 9),
        )


def test_registry_retry_refuses_missing_blob_instead_of_repairing_it(tmp_path) -> None:
    registry, _, prepared = _registered_set(tmp_path)
    registry.register_complete_set(prepared)
    passage_digest = prepared.passages[0].text_sha256
    passage_path = registry.text_blobs.blob_path(passage_digest)
    passage_path.unlink()

    with pytest.raises(EvidenceTargetBlobConflict, match="incomplete"):
        registry.register_complete_set(prepared)

    assert not passage_path.exists()


def test_registry_schema_prevents_append_update_delete_and_crossed_import(tmp_path) -> None:
    registry, record, prepared = _registered_set(tmp_path)
    registry.register_complete_set(prepared)
    passage = prepared.passages[0]

    with sqlite3.connect(registry.catalog.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                """
                insert into evidence_passages values (?, ?, ?, ?, ?, ?)
                """,
                (
                    prepared.evidence_set_id,
                    passage_evidence_id(record.transcript_revision_id, 1),
                    1,
                    "participant",
                    "b" * 64,
                    1,
                ),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                """
                update evidence_sets set review_status = review_status
                where evidence_set_id = ?
                """,
                (prepared.evidence_set_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "delete from evidence_sets where evidence_set_id = ?",
                (prepared.evidence_set_id,),
            )

    other_text = "P2: Different source."
    other_identity = transcript_evidence_identity(other_text)
    other_record = replace(
        record,
        import_id="imp_other",
        run_id="run_other",
        project_source_id="psrc_other",
        source_id=other_identity.source_id,
        source_blob_sha256="c" * 64,
        transcript_revision_id=other_identity.transcript_revision_id,
        transcript_sha256=other_identity.transcript_sha256,
    )
    EvidenceCatalog(tmp_path).record_import(other_record)
    with sqlite3.connect(registry.catalog.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        connection.execute("begin")
        connection.execute(
            """
            insert into evidence_sets values (
              ?, ?, ?, ?, 'analysis_turns', 1, 'verified', 'not_applicable',
              ?, ?, 0, 0, ?
            )
            """,
            (
                f"evs_{'f' * 32}",
                other_record.import_id,
                record.project_source_id,
                record.transcript_revision_id,
                record.transcript_sha256,
                "f" * 64,
                record.imported_at,
            ),
        )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.commit()
        connection.rollback()
