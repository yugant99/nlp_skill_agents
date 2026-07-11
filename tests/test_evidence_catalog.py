from dataclasses import replace
import sqlite3

import pytest

from backend.storage.evidence_catalog import EvidenceCatalog, EvidenceImportRecord


def test_evidence_catalog_records_imports_and_deduplicates_revisions(tmp_path) -> None:
    catalog = EvidenceCatalog(tmp_path)
    first = EvidenceImportRecord(
        import_id="imp_first",
        run_id="run_first",
        pipeline="analysis",
        source_id="src_same",
        source_filename="first.txt",
        source_media_type="text/plain",
        source_blob_sha256="a" * 64,
        transcript_revision_id="trv_same",
        transcript_sha256="b" * 64,
        imported_at="2026-07-10T10:00:00+00:00",
        project_source_id="psrc_demo",
        workspace_id="study_demo",
    )
    second = EvidenceImportRecord(
        import_id="imp_second",
        run_id="run_second",
        pipeline="study_batch",
        source_id="src_same",
        source_filename="renamed.txt",
        source_media_type="text/plain",
        source_blob_sha256="a" * 64,
        transcript_revision_id="trv_same",
        transcript_sha256="b" * 64,
        imported_at="2026-07-10T11:00:00+00:00",
        project_source_id="psrc_demo",
        workspace_id="study_demo",
    )

    catalog.record_import(first)
    catalog.record_import(first)
    catalog.record_import(second)

    imports = catalog.list_imports()
    assert [item["import_id"] for item in imports] == ["imp_second", "imp_first"]
    assert imports[0]["transcript_revision_id"] == "trv_same"

    with catalog.db_path.open("rb") as database_file:
        assert database_file.read(16) == b"SQLite format 3\x00"

    with pytest.raises(ValueError, match="Source import identity conflicts"):
        catalog.record_import(replace(first, source_blob_sha256="c" * 64))

    with pytest.raises(ValueError, match="Transcript revision identity conflicts"):
        catalog.record_import(
            replace(
                second,
                import_id="imp_conflicting_revision",
                transcript_sha256="d" * 64,
            )
        )


def test_evidence_catalog_records_validated_revision_lineage(tmp_path) -> None:
    catalog = EvidenceCatalog(tmp_path)
    first = EvidenceImportRecord(
        import_id="imp_v1",
        run_id="run_v1",
        pipeline="analysis",
        source_id="src_v1",
        source_filename="interview.txt",
        source_media_type="text/plain",
        source_blob_sha256="1" * 64,
        transcript_revision_id="trv_v1",
        transcript_sha256="2" * 64,
        imported_at="2026-07-10T10:00:00+00:00",
        project_source_id="psrc_interview",
        workspace_id="study_one",
    )
    second = EvidenceImportRecord(
        import_id="imp_v2",
        run_id="run_v2",
        pipeline="analysis",
        source_id="src_v2",
        source_filename="interview-revised.txt",
        source_media_type="text/plain",
        source_blob_sha256="3" * 64,
        transcript_revision_id="trv_v2",
        transcript_sha256="4" * 64,
        imported_at="2026-07-10T11:00:00+00:00",
        project_source_id="psrc_interview",
        parent_transcript_revision_id="trv_v1",
        workspace_id="study_one",
    )

    catalog.record_import(first)
    catalog.record_import(second)

    history = catalog.source_history("psrc_interview")
    assert history["source"]["workspace_id"] == "study_one"
    assert [item["transcript_revision_id"] for item in history["revisions"]] == [
        "trv_v1",
        "trv_v2",
    ]
    assert history["revisions"][1]["parent_transcript_revision_id"] == "trv_v1"

    with pytest.raises(ValueError, match="Parent revision does not belong"):
        catalog.record_import(
            replace(
                second,
                import_id="imp_missing_parent",
                transcript_revision_id="trv_v3",
                transcript_sha256="5" * 64,
                parent_transcript_revision_id="trv_missing",
            )
        )
    with pytest.raises(ValueError, match="cannot be its own parent"):
        catalog.record_import(
            replace(
                second,
                import_id="imp_self_parent",
                transcript_revision_id="trv_self",
                transcript_sha256="6" * 64,
                parent_transcript_revision_id="trv_self",
            )
        )


def test_evidence_catalog_migrates_imports_without_inventing_lineage(tmp_path) -> None:
    database_path = tmp_path / "evidence.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            create table transcript_revisions (
              transcript_revision_id text primary key,
              source_id text not null,
              transcript_sha256 text not null,
              created_at text not null
            )
            """
        )
        connection.execute(
            """
            create table source_imports (
              import_id text primary key,
              run_id text not null,
              pipeline text not null,
              source_id text not null,
              source_filename text not null,
              source_media_type text not null,
              source_blob_sha256 text not null,
              transcript_revision_id text not null,
              imported_at text not null
            )
            """
        )
        connection.execute(
            """
            insert into transcript_revisions values (
              'trv_old', 'src_old', ?, '2026-07-10T10:00:00+00:00'
            )
            """,
            ("a" * 64,),
        )
        connection.execute(
            """
            insert into source_imports values (
              'imp_old', 'run_old', 'analysis', 'src_old', 'old.txt',
              'text/plain', ?, 'trv_old', '2026-07-10T10:00:00+00:00'
            )
            """,
            ("b" * 64,),
        )

    catalog = EvidenceCatalog(tmp_path)
    migrated = catalog.list_imports()[0]
    history = catalog.source_history("psrc_legacy_imp_old")

    assert migrated["project_source_id"] == "psrc_legacy_imp_old"
    assert migrated["parent_transcript_revision_id"] == ""
    assert history["source"]["workspace_id"] == "legacy"
    assert history["revisions"][0]["parent_transcript_revision_id"] == ""
