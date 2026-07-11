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
    )

    catalog.record_import(first)
    catalog.record_import(second)

    imports = catalog.list_imports()
    assert [item["import_id"] for item in imports] == ["imp_second", "imp_first"]
    assert imports[0]["transcript_revision_id"] == "trv_same"

    with catalog.db_path.open("rb") as database_file:
        assert database_file.read(16) == b"SQLite format 3\x00"
