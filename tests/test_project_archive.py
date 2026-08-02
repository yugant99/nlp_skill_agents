import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

import backend.storage.project_archive as project_archive_module
from backend.evidence.identifiers import (
    cunit_evidence_id,
    passage_evidence_id,
    transcript_evidence_identity,
)
from backend.qualitative.cases import CaseService
from backend.qualitative.codebooks import CodebookService
from backend.qualitative.coding_references import CodingReferenceService
from backend.qualitative.database import QualitativeProjectDatabase
from backend.qualitative.notes import NoteService
from backend.qualitative.research_reviews import ResearchReviewService
from backend.storage.audit_log import AuditLogStore
from backend.storage.evidence_catalog import EvidenceCatalog, EvidenceImportRecord
from backend.storage.evidence_target_registry import (
    EvidenceCUnitInput,
    EvidencePassageInput,
    EvidenceTargetRegistry,
)
from backend.storage.evidence_text_blob_store import EvidenceTextBlobStore
from backend.storage.project_archive import (
    ProjectArchiveConflict,
    ProjectArchiveError,
    ProjectArchiveStore,
)
from backend.storage.source_blob_store import SourceBlobStore
from backend.storage.study_batch_operation_store import StudyBatchOperationStore
from backend.storage.study_store import StudyWorkspaceStore


def _build_study(root: Path) -> tuple[str, str]:
    store = StudyWorkspaceStore(root)
    study = store.create_study({"name": "Archive Study"})
    version = store.add_skill_pack_version(
        study.id,
        {
            "id": "archive_pack",
            "name": "Archive Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    batch = store.run_text_batch(
        study.id,
        version.version_id,
        [{"source_filename": "session.txt", "content": "P1_c: One.\nP1_p: Two."}],
    )
    run = store.list_batch_runs(study.id, batch.batch_id)[0]
    return study.id, run["source_blob_sha256"]


def _build_qualitative_archive(root: Path):
    study_id, _ = _build_study(root)
    researcher_id = "res_archive_researcher"
    QualitativeProjectDatabase(root, study_id).initialize(
        researcher_id=researcher_id,
        researcher_name="Archive Researcher",
    )
    service = CaseService(root, study_id)
    case = service.create_case(
        researcher_id=researcher_id,
        case_kind="participant",
        label="Participant 1",
        description="Round-trip participant",
    )
    definition = service.create_attribute_definition(
        researcher_id=researcher_id,
        attribute_key="session_count",
        label="Session count",
        value_type="number",
        required=True,
    )
    service.set_attribute_value(
        researcher_id=researcher_id,
        case_id=case.case_id,
        attribute_definition_id=definition.attribute_definition_id,
        value=3,
    )
    project_source_id = EvidenceCatalog(root).workspace_import_records(study_id)[
        0
    ].project_source_id
    service.link_source(
        researcher_id=researcher_id,
        case_id=case.case_id,
        project_source_id=project_source_id,
    )
    exported = ProjectArchiveStore(root).create_archive(study_id)
    return (
        study_id,
        case.case_id,
        definition.attribute_definition_id,
        project_source_id,
        exported,
    )


def _add_cunit_evidence(root: Path, study_id: str):
    transcript_text = "P: I came and I stayed."
    passage_text = "I came and I stayed."
    identity = transcript_evidence_identity(transcript_text)
    source_digest = sha256(transcript_text.encode("utf-8")).hexdigest()
    import_record = EvidenceImportRecord(
        import_id="imp_archive_cunit",
        run_id="run_archive_cunit",
        pipeline="study_segmentation",
        source_id=identity.source_id,
        source_filename="cunit-session.txt",
        source_media_type="text/plain",
        source_blob_sha256=source_digest,
        transcript_revision_id=identity.transcript_revision_id,
        transcript_sha256=identity.transcript_sha256,
        imported_at="2026-08-01T12:00:00+00:00",
        project_source_id="psrc_archive_cunit",
        workspace_id=study_id,
    )
    SourceBlobStore(root).store(transcript_text.encode("utf-8"), source_digest)
    EvidenceCatalog(root).record_import(import_record)
    passage_id = passage_evidence_id(identity.transcript_revision_id, 0)
    cunit_ids = (
        cunit_evidence_id(passage_id, 0),
        cunit_evidence_id(passage_id, 1),
    )
    registry = EvidenceTargetRegistry(root)
    prepared = registry.prepare_complete_set(
        import_id=import_record.import_id,
        workspace_id=study_id,
        project_source_id=import_record.project_source_id,
        transcript_revision_id=identity.transcript_revision_id,
        transcript_text=transcript_text,
        producer_kind="cunit_segmentation",
        producer_version=1,
        producer_status="verified",
        review_status="not_domain_validated",
        passages=(
            EvidencePassageInput(
                passage_id=passage_id,
                passage_ordinal=0,
                role="P",
                text=passage_text,
                cunits=(
                    EvidenceCUnitInput(
                        cunit_id=cunit_ids[0],
                        cunit_ordinal=0,
                        text="I came",
                    ),
                    EvidenceCUnitInput(
                        cunit_id=cunit_ids[1],
                        cunit_ordinal=1,
                        text="and I stayed.",
                    ),
                ),
            ),
        ),
    )
    registry.register_complete_set(prepared)
    return import_record, prepared, passage_id, cunit_ids


def _build_coding_reference_archive(root: Path):
    study_id, _ = _build_study(root)
    import_record, prepared, passage_id, cunit_ids = _add_cunit_evidence(
        root,
        study_id,
    )
    researcher_id = "res_archive_coder"
    QualitativeProjectDatabase(root, study_id).initialize(
        researcher_id=researcher_id,
        researcher_name="Archive Coder",
    )
    codebooks = CodebookService(root, study_id)
    codebook = codebooks.create_codebook(
        researcher_id=researcher_id,
        title="Archive coding",
    )
    draft = codebooks.create_draft(
        researcher_id=researcher_id,
        codebook_id=codebook.codebook_id,
    )
    code = codebooks.add_code(
        researcher_id=researcher_id,
        codebook_id=codebook.codebook_id,
        codebook_version_id=draft.version.codebook_version_id,
        stable_code_key="arrival",
        label="Arrival",
    )
    codebooks.freeze_version(
        researcher_id=researcher_id,
        codebook_id=codebook.codebook_id,
        codebook_version_id=draft.version.codebook_version_id,
    )
    reference = CodingReferenceService(root, study_id).create_reference(
        researcher_id=researcher_id,
        project_source_id=import_record.project_source_id,
        transcript_revision_id=import_record.transcript_revision_id,
        evidence_set_id=prepared.evidence_set_id,
        target_kind="cunit",
        passage_id=passage_id,
        cunit_id=cunit_ids[0],
        start_offset=0,
        end_offset=len("I came"),
        codebook_version_id=draft.version.codebook_version_id,
        code_id=code.code_id,
    )
    exported = ProjectArchiveStore(root).create_archive(study_id)
    return study_id, import_record, prepared, reference, exported


def _build_note_archive(root: Path):
    (
        study_id,
        import_record,
        prepared,
        reference,
        _,
    ) = _build_coding_reference_archive(root)
    researcher_id = reference.created_by
    case = CaseService(root, study_id).create_case(
        researcher_id=researcher_id,
        case_kind="participant",
        label="Memo participant",
    )
    service = NoteService(root, study_id)
    study_memo = service.create_note(
        note_kind="memo",
        researcher_id=researcher_id,
        title="Reflexive memo",
        body="Initial analytic reflection.",
        target={"kind": "study"},
    )
    source_annotation = service.create_note(
        note_kind="annotation",
        researcher_id=researcher_id,
        title="",
        body="Source context.",
        target={
            "kind": "source",
            "project_source_id": import_record.project_source_id,
        },
    )
    case_memo = service.create_note(
        note_kind="memo",
        researcher_id=researcher_id,
        title="Case memo",
        body="Case-level interpretation.",
        target={"kind": "case", "case_id": case.case_id},
    )
    code_annotation = service.create_note(
        note_kind="annotation",
        researcher_id=researcher_id,
        title="",
        body="Code context.",
        target={
            "kind": "code",
            "codebook_version_id": reference.codebook_version_id,
            "code_id": reference.code_id,
        },
    )
    excerpt_memo = service.create_note(
        note_kind="memo",
        researcher_id=researcher_id,
        title="Excerpt memo",
        body="Exact evidence interpretation.",
        target={
            "kind": "excerpt",
            "project_source_id": import_record.project_source_id,
            "transcript_revision_id": import_record.transcript_revision_id,
            "evidence_set_id": prepared.evidence_set_id,
            "excerpt_target_kind": "cunit",
            "passage_id": reference.passage_id,
            "cunit_id": reference.cunit_id,
            "start_offset": 0,
            "end_offset": len("I came"),
        },
    )
    study_memo = service.revise_note(
        note_kind="memo",
        note_id=study_memo.note.note_id,
        researcher_id=researcher_id,
        expected_revision_number=1,
        title="Reflexive memo revised",
        body="Revised analytic reflection.",
    )
    source_annotation = service.remove_note(
        note_kind="annotation",
        note_id=source_annotation.note.note_id,
        researcher_id=researcher_id,
    )
    snapshots = (
        study_memo,
        source_annotation,
        case_memo,
        code_annotation,
        excerpt_memo,
    )
    exported = ProjectArchiveStore(root).create_archive(study_id)
    return study_id, snapshots, exported


def _build_research_review_archive(root: Path):
    (
        study_id,
        import_record,
        prepared,
        existing_reference,
        _,
    ) = _build_coding_reference_archive(root)
    owner_id = existing_reference.created_by
    reviewer_id = "res_archive_reviewer"
    reviews = ResearchReviewService(root, study_id)
    researcher = reviews.create_researcher(
        researcher_id=reviewer_id,
        actor_id=owner_id,
        display_name="Archive Reviewer",
        role="reviewer",
    )
    target = prepared.passages[0].cunits[1]
    suggestion = reviews.create_agent_suggestion(
        agent_suggestion_id=f"ags_{'a' * 32}",
        origin_kind="imported_agent_output",
        origin_id="archive-agent-output",
        origin_suggestion_key="candidate-1",
        researcher_id=owner_id,
        project_source_id=import_record.project_source_id,
        transcript_revision_id=import_record.transcript_revision_id,
        evidence_set_id=prepared.evidence_set_id,
        target_kind="cunit",
        passage_id=prepared.passages[0].passage_id,
        cunit_id=target.cunit_id,
        start_offset=0,
        end_offset=len(target.text),
        codebook_version_id=existing_reference.codebook_version_id,
        code_id=existing_reference.code_id,
    )
    result = CodingReferenceService(root, study_id).create_reference(
        researcher_id=reviewer_id,
        project_source_id=import_record.project_source_id,
        transcript_revision_id=import_record.transcript_revision_id,
        evidence_set_id=prepared.evidence_set_id,
        target_kind="cunit",
        passage_id=prepared.passages[0].passage_id,
        cunit_id=target.cunit_id,
        start_offset=0,
        end_offset=len(target.text),
        codebook_version_id=existing_reference.codebook_version_id,
        code_id=existing_reference.code_id,
    )
    decision = reviews.append_reviewer_decision(
        reviewer_decision_id=f"rvd_{'b' * 32}",
        agent_suggestion_id=suggestion.suggestion.agent_suggestion_id,
        researcher_id=reviewer_id,
        expected_decision_number=0,
        decision="accepted",
        coding_reference_id=result.coding_reference_id,
    )
    tombstone = CodingReferenceService(root, study_id).remove_reference(
        researcher_id=reviewer_id,
        coding_reference_id=result.coding_reference_id,
    )
    suggestion = reviews.read_agent_suggestion(
        suggestion.suggestion.agent_suggestion_id
    )
    exported = ProjectArchiveStore(root).create_archive(study_id)
    return study_id, researcher, suggestion, decision, tombstone, exported


def _build_cunit_target_archive(root: Path):
    study = StudyWorkspaceStore(root).create_study(
        {"name": "Archive C-unit Target"}
    )
    import_record, prepared, passage_id, cunit_ids = _add_cunit_evidence(
        root,
        study.id,
    )
    exported = ProjectArchiveStore(root).create_archive(study.id)
    return (
        study.id,
        import_record,
        prepared,
        passage_id,
        cunit_ids,
        exported,
    )


def _prepared_text_blob_sha256s(prepared) -> tuple[str, ...]:
    digests = {prepared.transcript_text_sha256}
    for passage in prepared.passages:
        digests.add(passage.text_sha256)
        digests.update(cunit.text_sha256 for cunit in passage.cunits)
    return tuple(sorted(digests))


def _rewrite_qualitative_database(
    archive_path: Path,
    output_path: Path,
    database_path: Path,
    tamper_sql: str,
) -> None:
    with ZipFile(archive_path) as archive:
        database_path.write_bytes(archive.read("study/qualitative.sqlite3"))
    with sqlite3.connect(database_path) as connection:
        connection.executescript(tamper_sql)
    _rewrite_archive_members(
        archive_path,
        output_path,
        lambda members: members.__setitem__(
            "study/qualitative.sqlite3",
            database_path.read_bytes(),
        ),
    )


def _rewrite_archive_journal(
    archive_path: Path,
    output_path: Path,
    journal_path: Path,
) -> None:
    with ZipFile(archive_path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members["study/batch_operations.sqlite3"] = journal_path.read_bytes()
    manifest = json.loads(members["manifest.json"].decode("utf-8"))
    journal_record = next(
        record
        for record in manifest["members"]
        if record["path"] == "study/batch_operations.sqlite3"
    )
    journal_record["size_bytes"] = len(members["study/batch_operations.sqlite3"])
    journal_record["sha256"] = sha256(
        members["study/batch_operations.sqlite3"]
    ).hexdigest()
    members["manifest.json"] = json.dumps(
        manifest,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def _rewrite_archive_members(
    archive_path: Path,
    output_path: Path,
    mutate,
) -> None:
    with ZipFile(archive_path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(members.pop("manifest.json").decode("utf-8"))
    mutate(members)
    manifest["members"] = [
        {
            "path": name,
            "size_bytes": len(content),
            "sha256": sha256(content).hexdigest(),
        }
        for name, content in sorted(members.items())
    ]
    with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
        )
        for name, content in sorted(members.items()):
            archive.writestr(name, content)


def _write_archive(
    archive_path: Path,
    *,
    study_id: str,
    members: dict[str, bytes],
) -> None:
    manifest = {
        "format_version": 1,
        "study_id": study_id,
        "created_at": "2026-07-30T12:00:00+00:00",
        "members": [
            {
                "path": name,
                "size_bytes": len(content),
                "sha256": sha256(content).hexdigest(),
            }
            for name, content in sorted(members.items())
        ],
    }
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, content in members.items():
            archive.writestr(name, content)


def _rewrite_archive_manifest(
    archive_path: Path,
    output_path: Path,
    mutate,
) -> None:
    with ZipFile(archive_path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(members["manifest.json"].decode("utf-8"))
    members["manifest.json"] = json.dumps(mutate(manifest)).encode("utf-8")
    with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def _remove_evidence_set_ids(value):
    if isinstance(value, dict):
        value.pop("evidence_set_id", None)
        for item in value.values():
            _remove_evidence_set_ids(item)
    elif isinstance(value, list):
        for item in value:
            _remove_evidence_set_ids(item)


def _rewrite_archive_as_v1(
    archive_path: Path,
    output_path: Path,
    mutate=None,
) -> None:
    with ZipFile(archive_path) as archive:
        members = {
            name: archive.read(name)
            for name in archive.namelist()
            if name != "manifest.json"
        }
        manifest = json.loads(archive.read("manifest.json"))
    members.pop("evidence/targets.json", None)
    for name in list(members):
        if name.startswith("evidence_text_blobs/"):
            members.pop(name)
        elif name.endswith(".json"):
            payload = json.loads(members[name])
            _remove_evidence_set_ids(payload)
            members[name] = json.dumps(payload, sort_keys=True).encode("utf-8")
    if mutate is not None:
        mutate(members)
    manifest["format_version"] = 1
    manifest["members"] = [
        {
            "path": name,
            "size_bytes": len(content),
            "sha256": sha256(content).hexdigest(),
        }
        for name, content in sorted(members.items())
    ]
    with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
        )
        for name, content in sorted(members.items()):
            archive.writestr(name, content)


def _destination_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.name != ".workspace-mutation.lock"
    }


def _destination_tree(root: Path) -> dict[str, tuple[str, bytes | str | None]]:
    if not root.exists() and not root.is_symlink():
        return {}
    tree: dict[str, tuple[str, bytes | str | None]] = {}
    for path in (root, *sorted(root.rglob("*"))):
        relative = "." if path == root else path.relative_to(root).as_posix()
        if path.is_symlink():
            tree[relative] = ("symlink", os.readlink(path))
        elif path.is_dir():
            tree[relative] = ("directory", None)
        else:
            tree[relative] = ("file", path.read_bytes())
    return tree


def _note_audit_rows(root: Path, study_id: str) -> tuple[tuple[object, ...], ...]:
    database_path = root / "studies" / study_id / "qualitative.sqlite3"
    with sqlite3.connect(database_path) as connection:
        return tuple(
            connection.execute(
                """
                select event_id, project_id, actor_id, event_type,
                       subject_type, subject_id, metadata_json, created_at
                from qualitative_audit_events
                where subject_type in ('memo', 'annotation')
                   or event_type like 'memo.%'
                   or event_type like 'annotation.%'
                order by event_id
                """
            ).fetchall()
        )


def _research_review_audit_rows(
    root: Path,
    study_id: str,
) -> tuple[tuple[object, ...], ...]:
    database_path = root / "studies" / study_id / "qualitative.sqlite3"
    with sqlite3.connect(database_path) as connection:
        return tuple(
            connection.execute(
                """
                select event_id, project_id, actor_id, event_type,
                       subject_type, subject_id, metadata_json, created_at
                from qualitative_audit_events
                where subject_type in ('researcher', 'agent_suggestion',
                                       'reviewer_decision')
                   or event_type like 'qualitative.researcher.%'
                   or event_type like 'qualitative.agent_suggestion.%'
                   or event_type like 'qualitative.reviewer_decision.%'
                order by event_id
                """
            ).fetchall()
        )


def test_project_archive_round_trips_study_evidence_and_source_blobs(tmp_path) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, blob_hash = _build_study(source_root)
    source_store = StudyWorkspaceStore(source_root)
    source_batch = source_store.list_batches(study_id)[0]
    source_journal = StudyBatchOperationStore(source_root, study_id)
    source_operation = source_journal.get_operation(source_batch.batch_id)
    source_items = source_journal.list_items(source_batch.batch_id)

    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    with ZipFile(exported.archive_path) as archive:
        assert "study/batch_operations.sqlite3" in archive.namelist()
        assert "study/qualitative.sqlite3" not in archive.namelist()
    restored = ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    restored_store = StudyWorkspaceStore(restore_root)
    restored_study = restored_store.list_studies()[0]
    restored_batch = restored_store.load_batch(study_id, source_batch.batch_id)
    restored_journal = StudyBatchOperationStore(restore_root, study_id)
    restored_imports = EvidenceCatalog(restore_root).workspace_import_records(study_id)
    assert exported.archive_path.exists()
    assert len(exported.archive_sha256) == 64
    assert restored.study_id == study_id
    assert restored_study.id == study_id
    assert restored.import_count == 1
    assert restored.blob_count == 1
    assert restored.audit_event_count == 3
    assert [
        event["event_type"]
        for event in AuditLogStore(restore_root).events_for_subject("study", study_id)
    ] == ["study.created", "skill_pack.versioned", "batch.completed"]
    restored_events = AuditLogStore(restore_root).events_for_subject(
        "study", study_id
    )
    assert AuditLogStore(restore_root).import_events(restored_events) == 0
    conflicting_event = {**restored_events[0], "actor": "different-actor"}
    with pytest.raises(ValueError, match="identity conflicts"):
        AuditLogStore(restore_root).import_events([conflicting_event])
    assert restored_imports[0].source_blob_sha256 == blob_hash
    assert SourceBlobStore(restore_root).read_verified(blob_hash) == (
        b"P1_c: One.\nP1_p: Two."
    )
    assert restored_batch.aggregate_dir == (
        restore_root
        / "studies"
        / study_id
        / "batches"
        / source_batch.batch_id
    )
    assert restored_journal.get_operation(source_batch.batch_id) == source_operation
    assert restored_journal.list_items(source_batch.batch_id) == source_items
    assert not (
        restore_root / "studies" / study_id / "qualitative.sqlite3"
    ).exists()

    replayed = restored_store.run_text_batch(
        study_id,
        source_batch.skill_pack_version_id,
        [
            {
                "source_filename": "session.txt",
                "content": "P1_c: One.\nP1_p: Two.",
            }
        ],
        batch_id=source_batch.batch_id,
    )
    assert replayed == restored_batch
    assert restored_journal.get_operation(source_batch.batch_id)[
        "attempt_count"
    ] == 1
    assert len(
        [
            event
            for event in AuditLogStore(restore_root).list_events(limit=None)
            if event["event_type"] == "batch.completed"
        ]
    ) == 1

    with pytest.raises(FileExistsError):
        ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)


def test_project_archive_round_trips_qualitative_case_state(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    (
        study_id,
        case_id,
        attribute_definition_id,
        project_source_id,
        exported,
    ) = _build_qualitative_archive(source_root)
    source_snapshot = CaseService(source_root, study_id).read_case(case_id)

    with ZipFile(exported.archive_path) as archive:
        assert "study/qualitative.sqlite3" in archive.namelist()
    ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    restored_service = CaseService(restore_root, study_id)
    restored_snapshot = restored_service.read_case(case_id)
    restored_value = restored_service.read_attribute_value(
        case_id=case_id,
        attribute_definition_id=attribute_definition_id,
    )
    assert restored_snapshot == source_snapshot
    assert restored_value.value == 3
    assert restored_snapshot.project_source_ids == (project_source_id,)


def test_project_archive_v2_round_trips_coding_reference_target_closure(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    (
        study_id,
        import_record,
        prepared,
        reference,
        exported,
    ) = _build_coding_reference_archive(source_root)

    with ZipFile(exported.archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        target_document = json.loads(archive.read("evidence/targets.json"))
        text_members = {
            name
            for name in archive.namelist()
            if name.startswith("evidence_text_blobs/")
        }
    assert manifest["format_version"] == 2
    assert prepared.evidence_set_id in {
        record["evidence_set_id"] for record in target_document["sets"]
    }
    assert {
        f"evidence_text_blobs/{digest}.utf8"
        for digest in _prepared_text_blob_sha256s(prepared)
    }.issubset(text_members)

    ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    assert CodingReferenceService(restore_root, study_id).read_reference(
        reference.coding_reference_id
    ) == reference
    restored_target = EvidenceTargetRegistry(restore_root).resolve(
        study_id,
        import_record.project_source_id,
        import_record.transcript_revision_id,
        prepared.evidence_set_id,
        reference.passage_id,
        reference.cunit_id,
    )
    assert restored_target.text == "I came"
    assert prepared.evidence_set_id in {
        snapshot.evidence_set_id
        for snapshot in EvidenceTargetRegistry(restore_root).workspace_snapshot(
            study_id
        )
    }
    for digest in _prepared_text_blob_sha256s(prepared):
        assert EvidenceTextBlobStore(restore_root).read_verified(digest)


def test_project_archive_v2_round_trips_all_note_targets_and_history(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, source_snapshots, exported = _build_note_archive(source_root)
    source_service = NoteService(source_root, study_id)
    source_histories = {
        snapshot.note.note_id: source_service.list_revisions(
            snapshot.note.note_kind,
            snapshot.note.note_id,
            limit=50,
        )[0]
        for snapshot in source_snapshots
    }
    source_audits = _note_audit_rows(source_root, study_id)

    ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    restored = NoteService(restore_root, study_id)
    restored.validate_project_state()
    for snapshot in source_snapshots:
        assert restored.read_note(
            snapshot.note.note_kind,
            snapshot.note.note_id,
        ) == snapshot
        revisions, next_cursor = restored.list_revisions(
            snapshot.note.note_kind,
            snapshot.note.note_id,
            limit=50,
        )
        assert next_cursor is None
        assert revisions == source_histories[snapshot.note.note_id]
    assert _note_audit_rows(restore_root, study_id) == source_audits
    assert source_snapshots[1].note.removed_at is not None


def test_project_archive_v2_round_trips_research_review_state_and_audits(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, researcher, suggestion, decision, tombstone, exported = (
        _build_research_review_archive(source_root)
    )
    source_reviews = ResearchReviewService(source_root, study_id)
    source_decisions = source_reviews.list_reviewer_decisions(
        suggestion.suggestion.agent_suggestion_id,
        limit=50,
    )
    source_audits = _research_review_audit_rows(source_root, study_id)

    ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    restored = ResearchReviewService(restore_root, study_id)
    restored.validate_project_state()
    assert restored.read_researcher(researcher.researcher_id) == researcher
    assert restored.read_agent_suggestion(
        suggestion.suggestion.agent_suggestion_id
    ) == suggestion
    assert restored.list_reviewer_decisions(
        suggestion.suggestion.agent_suggestion_id,
        limit=50,
    ) == source_decisions
    assert source_decisions.reviewer_decisions == (decision,)
    assert CodingReferenceService(restore_root, study_id).read_reference(
        tombstone.coding_reference_id
    ) == tombstone
    assert _research_review_audit_rows(restore_root, study_id) == source_audits


def test_project_archive_concurrent_coding_write_is_attributably_atomic(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    (
        study_id,
        import_record,
        prepared,
        existing_reference,
        _,
    ) = _build_coding_reference_archive(source_root)
    barrier = threading.Barrier(3)
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def create_archive() -> None:
        try:
            barrier.wait(timeout=5)
            results["archive"] = ProjectArchiveStore(source_root).create_archive(
                study_id
            )
        except BaseException as exc:
            errors.append(exc)

    def create_reference() -> None:
        try:
            barrier.wait(timeout=5)
            results["reference"] = CodingReferenceService(
                source_root,
                study_id,
            ).create_reference(
                researcher_id=existing_reference.created_by,
                project_source_id=import_record.project_source_id,
                transcript_revision_id=import_record.transcript_revision_id,
                evidence_set_id=prepared.evidence_set_id,
                target_kind="passage",
                passage_id=existing_reference.passage_id,
                cunit_id="",
                start_offset=0,
                end_offset=len("I came"),
                codebook_version_id=existing_reference.codebook_version_id,
                code_id=existing_reference.code_id,
            )
        except BaseException as exc:
            errors.append(exc)

    archive_thread = threading.Thread(target=create_archive)
    reference_thread = threading.Thread(target=create_reference)
    archive_thread.start()
    reference_thread.start()
    barrier.wait(timeout=5)
    archive_thread.join(timeout=15)
    reference_thread.join(timeout=15)

    assert not archive_thread.is_alive()
    assert not reference_thread.is_alive()
    assert errors == []
    concurrent_reference = results["reference"]
    exported = results["archive"]
    database_copy = tmp_path / "concurrent-archive.sqlite3"
    with ZipFile(exported.archive_path) as archive:
        database_copy.write_bytes(archive.read("study/qualitative.sqlite3"))
    with sqlite3.connect(database_copy) as connection:
        coding_count = connection.execute(
            """
            select count(*) from coding_references
            where coding_reference_id = ?
            """,
            (concurrent_reference.coding_reference_id,),
        ).fetchone()[0]
        audit_count = connection.execute(
            """
            select count(*) from qualitative_audit_events
            where event_type = 'coding_reference.created'
              and subject_id = ?
            """,
            (concurrent_reference.coding_reference_id,),
        ).fetchone()[0]

    assert (coding_count, audit_count) in {(0, 0), (1, 1)}


def test_project_archive_concurrent_suggestion_write_is_attributably_atomic(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    (
        study_id,
        import_record,
        prepared,
        existing_reference,
        _,
    ) = _build_coding_reference_archive(source_root)
    target = prepared.passages[0].cunits[1]
    barrier = threading.Barrier(3)
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def create_archive() -> None:
        try:
            barrier.wait(timeout=5)
            results["archive"] = ProjectArchiveStore(source_root).create_archive(
                study_id
            )
        except BaseException as exc:
            errors.append(exc)

    def create_suggestion() -> None:
        try:
            barrier.wait(timeout=5)
            results["suggestion"] = ResearchReviewService(
                source_root,
                study_id,
            ).create_agent_suggestion(
                agent_suggestion_id=f"ags_{'c' * 32}",
                origin_kind="synthetic_fixture",
                origin_id="archive-race",
                origin_suggestion_key="candidate-1",
                researcher_id=existing_reference.created_by,
                project_source_id=import_record.project_source_id,
                transcript_revision_id=import_record.transcript_revision_id,
                evidence_set_id=prepared.evidence_set_id,
                target_kind="cunit",
                passage_id=prepared.passages[0].passage_id,
                cunit_id=target.cunit_id,
                start_offset=0,
                end_offset=len(target.text),
                codebook_version_id=existing_reference.codebook_version_id,
                code_id=existing_reference.code_id,
            )
        except BaseException as exc:
            errors.append(exc)

    archive_thread = threading.Thread(target=create_archive)
    suggestion_thread = threading.Thread(target=create_suggestion)
    archive_thread.start()
    suggestion_thread.start()
    barrier.wait(timeout=5)
    archive_thread.join(timeout=15)
    suggestion_thread.join(timeout=15)

    assert not archive_thread.is_alive()
    assert not suggestion_thread.is_alive()
    assert errors == []
    concurrent_suggestion = results["suggestion"]
    suggestion_id = concurrent_suggestion.suggestion.agent_suggestion_id
    exported = results["archive"]
    database_copy = tmp_path / "concurrent-suggestion-archive.sqlite3"
    with ZipFile(exported.archive_path) as archive:
        database_copy.write_bytes(archive.read("study/qualitative.sqlite3"))
    with sqlite3.connect(database_copy) as connection:
        suggestion_count = connection.execute(
            """
            select count(*) from agent_coding_suggestions
            where agent_suggestion_id = ?
            """,
            (suggestion_id,),
        ).fetchone()[0]
        audit_count = connection.execute(
            """
            select count(*) from qualitative_audit_events
            where event_type = 'qualitative.agent_suggestion.created'
              and subject_id = ?
            """,
            (suggestion_id,),
        ).fetchone()[0]

    assert (suggestion_count, audit_count) in {(0, 0), (1, 1)}


def test_project_archive_concurrent_note_write_is_attributably_atomic(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    study_id, _, _, _, _ = _build_qualitative_archive(source_root)
    researcher_id = "res_archive_researcher"
    barrier = threading.Barrier(3)
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def create_archive() -> None:
        try:
            barrier.wait(timeout=5)
            results["archive"] = ProjectArchiveStore(source_root).create_archive(
                study_id
            )
        except BaseException as exc:
            errors.append(exc)

    def create_note() -> None:
        try:
            barrier.wait(timeout=5)
            results["note"] = NoteService(source_root, study_id).create_note(
                note_kind="memo",
                researcher_id=researcher_id,
                title="Concurrent memo",
                body="Complete note state or no note state.",
                target={"kind": "study"},
            )
        except BaseException as exc:
            errors.append(exc)

    archive_thread = threading.Thread(target=create_archive)
    note_thread = threading.Thread(target=create_note)
    archive_thread.start()
    note_thread.start()
    barrier.wait(timeout=5)
    archive_thread.join(timeout=15)
    note_thread.join(timeout=15)

    assert not archive_thread.is_alive()
    assert not note_thread.is_alive()
    assert errors == []
    note = results["note"]
    exported = results["archive"]
    database_copy = tmp_path / "concurrent-note-archive.sqlite3"
    with ZipFile(exported.archive_path) as archive:
        database_copy.write_bytes(archive.read("study/qualitative.sqlite3"))
    with sqlite3.connect(database_copy) as connection:
        note_count = connection.execute(
            "select count(*) from qualitative_notes where note_id = ?",
            (note.note.note_id,),
        ).fetchone()[0]
        revision_count = connection.execute(
            """
            select count(*) from qualitative_note_revisions where note_id = ?
            """,
            (note.note.note_id,),
        ).fetchone()[0]
        audit_count = connection.execute(
            """
            select count(*) from qualitative_audit_events
            where event_type = 'memo.created' and subject_id = ?
            """,
            (note.note.note_id,),
        ).fetchone()[0]

    assert (note_count, revision_count, audit_count) in {
        (0, 0, 0),
        (1, 1, 1),
    }


@pytest.mark.parametrize("mutation_kind", ["revise", "remove"])
def test_project_archive_concurrent_note_lifecycle_is_attributably_atomic(
    tmp_path: Path,
    mutation_kind: str,
) -> None:
    source_root = tmp_path / "source"
    study_id, snapshots, _ = _build_note_archive(source_root)
    initial = snapshots[-1]
    barrier = threading.Barrier(3)
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def create_archive() -> None:
        try:
            barrier.wait(timeout=5)
            results["archive"] = ProjectArchiveStore(source_root).create_archive(
                study_id
            )
        except BaseException as exc:
            errors.append(exc)

    def mutate_note() -> None:
        try:
            barrier.wait(timeout=5)
            service = NoteService(source_root, study_id)
            if mutation_kind == "revise":
                results["note"] = service.revise_note(
                    note_kind="memo",
                    note_id=initial.note.note_id,
                    researcher_id=initial.note.created_by,
                    expected_revision_number=1,
                    title="Concurrent excerpt revision",
                    body="Archive captures this complete revision or not at all.",
                )
            else:
                results["note"] = service.remove_note(
                    note_kind="memo",
                    note_id=initial.note.note_id,
                    researcher_id=initial.note.created_by,
                )
        except BaseException as exc:
            errors.append(exc)

    archive_thread = threading.Thread(target=create_archive)
    mutation_thread = threading.Thread(target=mutate_note)
    archive_thread.start()
    mutation_thread.start()
    barrier.wait(timeout=5)
    archive_thread.join(timeout=15)
    mutation_thread.join(timeout=15)

    assert not archive_thread.is_alive()
    assert not mutation_thread.is_alive()
    assert errors == []
    mutated = results["note"]
    exported = results["archive"]
    database_copy = tmp_path / f"concurrent-note-{mutation_kind}.sqlite3"
    with ZipFile(exported.archive_path) as archive:
        database_copy.write_bytes(archive.read("study/qualitative.sqlite3"))
    with sqlite3.connect(database_copy) as connection:
        if mutation_kind == "revise":
            revision_count = connection.execute(
                """
                select count(*) from qualitative_note_revisions
                where note_revision_id = ?
                """,
                (mutated.current_revision.note_revision_id,),
            ).fetchone()[0]
            chain_count = connection.execute(
                """
                select count(*) from qualitative_note_revisions where note_id = ?
                """,
                (initial.note.note_id,),
            ).fetchone()[0]
            metadata = json.dumps(
                {
                    "note_revision_id": mutated.current_revision.note_revision_id,
                    "revision_number": mutated.current_revision.revision_number,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            audit_count = connection.execute(
                """
                select count(*) from qualitative_audit_events
                where event_type = 'memo.revised' and subject_id = ?
                  and metadata_json = ?
                """,
                (initial.note.note_id, metadata),
            ).fetchone()[0]
            assert (revision_count, audit_count, chain_count) in {
                (0, 0, 1),
                (1, 1, 2),
            }
        else:
            removed_by, removed_at = connection.execute(
                """
                select removed_by, removed_at from qualitative_notes
                where note_id = ?
                """,
                (initial.note.note_id,),
            ).fetchone()
            audit_count = connection.execute(
                """
                select count(*) from qualitative_audit_events
                where event_type = 'memo.removed' and subject_id = ?
                """,
                (initial.note.note_id,),
            ).fetchone()[0]
            assert (
                (removed_by, removed_at, audit_count)
                == (None, None, 0)
                or (removed_by, removed_at, audit_count)
                == (mutated.note.removed_by, mutated.note.removed_at, 1)
            )


def test_project_archive_enters_live_guards_before_destination_workspace_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "source"
    study_id, _, _, _, _ = _build_coding_reference_archive(source_root)
    destination_lock_depth = 0
    study_guard_entries: list[int] = []
    qualitative_validation_roots: list[Path] = []
    note_validation_roots: list[Path] = []
    review_validation_roots: list[Path] = []
    original_workspace_lock = project_archive_module.workspace_mutation_lock
    original_archive_guard = StudyBatchOperationStore.archive_snapshot_guard
    original_case_validation = CaseService.validate_project_state
    original_coding_validation = CodingReferenceService.validate_project_state
    original_note_validation = NoteService.validate_project_state
    original_review_validation = ResearchReviewService.validate_project_state

    @contextmanager
    def tracked_workspace_lock(root):
        nonlocal destination_lock_depth
        is_destination = Path(root) == source_root
        with original_workspace_lock(root):
            if is_destination:
                destination_lock_depth += 1
            try:
                yield
            finally:
                if is_destination:
                    destination_lock_depth -= 1

    @contextmanager
    def tracked_archive_guard(store):
        study_guard_entries.append(destination_lock_depth)
        assert destination_lock_depth == 0
        with original_archive_guard(store):
            yield

    def tracked_case_validation(service):
        qualitative_validation_roots.append(service.root)
        if destination_lock_depth:
            assert service.root != source_root
        return original_case_validation(service)

    def tracked_coding_validation(service):
        qualitative_validation_roots.append(service.root)
        if destination_lock_depth:
            assert service.root != source_root
        return original_coding_validation(service)

    def tracked_note_validation(service):
        note_validation_roots.append(service.root)
        if destination_lock_depth:
            assert service.root != source_root
        return original_note_validation(service)

    def tracked_review_validation(service):
        review_validation_roots.append(service.root)
        if destination_lock_depth:
            assert service.root != source_root
        return original_review_validation(service)

    monkeypatch.setattr(
        project_archive_module,
        "workspace_mutation_lock",
        tracked_workspace_lock,
    )
    monkeypatch.setattr(
        StudyBatchOperationStore,
        "archive_snapshot_guard",
        tracked_archive_guard,
    )
    monkeypatch.setattr(
        CaseService,
        "validate_project_state",
        tracked_case_validation,
    )
    monkeypatch.setattr(
        CodingReferenceService,
        "validate_project_state",
        tracked_coding_validation,
    )
    monkeypatch.setattr(
        NoteService,
        "validate_project_state",
        tracked_note_validation,
    )
    monkeypatch.setattr(
        ResearchReviewService,
        "validate_project_state",
        tracked_review_validation,
    )

    ProjectArchiveStore(source_root).create_archive(study_id)

    assert study_guard_entries == [0]
    assert qualitative_validation_roots
    assert all(root != source_root for root in qualitative_validation_roots)
    assert note_validation_roots
    assert all(root != source_root for root in note_validation_roots)
    assert review_validation_roots
    assert all(root != source_root for root in review_validation_roots)
    assert destination_lock_depth == 0


@pytest.mark.parametrize("closure_kind", ["missing", "extra"])
def test_project_archive_rejects_inexact_evidence_text_blob_closure(
    tmp_path: Path,
    closure_kind: str,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _, prepared, _, _, exported = _build_cunit_target_archive(
        source_root
    )
    forged_archive = tmp_path / f"{closure_kind}-text-closure.nlpstudy.zip"

    def mutate(members):
        if closure_kind == "missing":
            digest = _prepared_text_blob_sha256s(prepared)[0]
            members.pop(f"evidence_text_blobs/{digest}.utf8")
        else:
            content = b"unreferenced evidence text"
            digest = sha256(content).hexdigest()
            members[f"evidence_text_blobs/{digest}.utf8"] = content

    _rewrite_archive_members(
        exported.archive_path,
        forged_archive,
        mutate,
    )

    with pytest.raises(ProjectArchiveError, match="blob closure"):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive)

    assert not (restore_root / "studies" / study_id).exists()


@pytest.mark.parametrize(
    ("fault_kind", "message"),
    [
        ("wrong_hash", "blob hash is invalid"),
        ("invalid_utf8", "not valid UTF-8"),
    ],
)
def test_project_archive_rejects_invalid_evidence_text_members(
    tmp_path: Path,
    fault_kind: str,
    message: str,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _, _, _, _, exported = _build_cunit_target_archive(source_root)
    forged_archive = tmp_path / f"{fault_kind}-text-member.nlpstudy.zip"

    def mutate(members):
        document = json.loads(members["evidence/targets.json"])
        cunit = document["sets"][0]["passages"][0]["cunits"][0]
        original_digest = cunit["text_sha256"]
        original_name = f"evidence_text_blobs/{original_digest}.utf8"
        if fault_kind == "wrong_hash":
            members[original_name] = b"different valid UTF-8 text"
            return
        invalid_content = b"\xff"
        invalid_digest = sha256(invalid_content).hexdigest()
        cunit["text_sha256"] = invalid_digest
        members.pop(original_name)
        members[f"evidence_text_blobs/{invalid_digest}.utf8"] = invalid_content
        members["evidence/targets.json"] = json.dumps(
            document,
            sort_keys=True,
        ).encode("utf-8")

    _rewrite_archive_members(
        exported.archive_path,
        forged_archive,
        mutate,
    )

    with pytest.raises(ProjectArchiveError, match=message):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive)

    assert _destination_tree(restore_root) == {}
    assert not (restore_root / "studies" / study_id).exists()


@pytest.mark.parametrize(
    ("tamper_kind", "message"),
    [
        ("ownership", "belongs to another workspace"),
        ("ordinal", "records are malformed"),
        ("count", "identity is invalid"),
        ("id", "identity is invalid"),
    ],
)
def test_project_archive_rejects_rehashed_target_record_tampering(
    tmp_path: Path,
    tamper_kind: str,
    message: str,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _, _, _, _, exported = _build_cunit_target_archive(source_root)
    forged_archive = tmp_path / f"{tamper_kind}-target-record.nlpstudy.zip"

    def mutate(members):
        document = json.loads(members["evidence/targets.json"])
        record = document["sets"][0]
        if tamper_kind == "ownership":
            record["workspace_id"] = "foreign-workspace"
        elif tamper_kind == "ordinal":
            record["passages"][0]["passage_ordinal"] = 1
        elif tamper_kind == "count":
            record["cunit_count"] += 1
        else:
            record["evidence_set_id"] = (
                "evs_0123456789abcdef0123456789abcdef"
            )
        members["evidence/targets.json"] = json.dumps(
            document,
            sort_keys=True,
        ).encode("utf-8")

    _rewrite_archive_members(
        exported.archive_path,
        forged_archive,
        mutate,
    )

    with pytest.raises(ProjectArchiveError, match=message):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive)

    assert _destination_tree(restore_root) == {}
    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_rejects_self_consistent_noncanonical_cunit_targets(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    (
        study_id,
        _,
        original,
        _,
        cunit_ids,
        exported,
    ) = _build_cunit_target_archive(source_root)
    forged_archive = tmp_path / "producer-divergent-targets.nlpstudy.zip"

    def mutate(members):
        document = json.loads(members["evidence/targets.json"])
        record = document["sets"][0]
        passage_text = members[
            f"evidence_text_blobs/{record['passages'][0]['text_sha256']}.utf8"
        ].decode("utf-8")
        passage_record = record["passages"][0]
        passage_record["cunits"] = [
            {
                "cunit_id": cunit_ids[0],
                "cunit_ordinal": 0,
                "text_sha256": passage_record["text_sha256"],
                "text_length": len(passage_text),
            }
        ]
        record["cunit_count"] = 1
        identity_manifest = {
            key: value
            for key, value in record.items()
            if key not in {"evidence_set_id", "snapshot_sha256", "created_at"}
        }
        snapshot_sha256 = sha256(
            json.dumps(
                identity_manifest,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        record["snapshot_sha256"] = snapshot_sha256
        record["evidence_set_id"] = f"evs_{snapshot_sha256[:32]}"
        document["sets"] = [record]
        members["evidence/targets.json"] = json.dumps(
            document,
            sort_keys=True,
        ).encode("utf-8")
        required_digests = {
            record["transcript_text_sha256"],
            passage_record["text_sha256"],
            passage_record["cunits"][0]["text_sha256"],
        }
        for name in list(members):
            if (
                name.startswith("evidence_text_blobs/")
                and Path(name).stem not in required_digests
            ):
                members.pop(name)

    _rewrite_archive_members(
        exported.archive_path,
        forged_archive,
        mutate,
    )

    assert original.cunit_count == 2
    with pytest.raises(ProjectArchiveError, match="current producer"):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive)

    assert _destination_tree(restore_root) == {}


@pytest.mark.parametrize("object_kind", ["directory", "fifo"])
def test_project_archive_rejects_non_regular_qualitative_database(
    tmp_path: Path,
    object_kind: str,
) -> None:
    study_id, _ = _build_study(tmp_path)
    database = QualitativeProjectDatabase(tmp_path, study_id)
    database.initialize(
        researcher_id="res_non_regular_archive",
        researcher_name="Non-Regular Archive Researcher",
    )
    database.db_path.unlink()
    if object_kind == "directory":
        database.db_path.mkdir()
    else:
        mkfifo = getattr(os, "mkfifo", None)
        if mkfifo is None:
            pytest.skip("FIFO filesystem objects are unavailable")
        mkfifo(database.db_path)

    with pytest.raises(
        ProjectArchiveConflict,
        match="Qualitative database must be a non-symlink regular file",
    ):
        ProjectArchiveStore(tmp_path).create_archive(study_id)

    assert not list((tmp_path / "backups").glob("*.nlpstudy.zip"))


def test_project_archive_rejects_missing_qualitative_source_before_publish(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _, _, _, exported = _build_qualitative_archive(source_root)
    forged_archive = tmp_path / "missing-qualitative-source.nlpstudy.zip"
    _rewrite_qualitative_database(
        exported.archive_path,
        forged_archive,
        tmp_path / "missing-source.sqlite3",
        """
        update source_case_links
        set project_source_id = 'psrc_missing_from_archive'
        """,
    )

    with pytest.raises(
        ProjectArchiveError,
        match="Archive qualitative project is invalid",
    ):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive)

    assert not (restore_root / "studies" / study_id).exists()
    assert _destination_files(restore_root) == {}


def test_project_archive_rejects_foreign_qualitative_source_before_publish(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _, _, project_source_id, exported = _build_qualitative_archive(
        source_root
    )
    original_source_history = EvidenceCatalog.source_history

    def foreign_source_history(catalog, requested_source_id):
        history = original_source_history(catalog, requested_source_id)
        if requested_source_id == project_source_id:
            history["source"] = {
                **history["source"],
                "workspace_id": "foreign-study",
            }
        return history

    monkeypatch.setattr(EvidenceCatalog, "source_history", foreign_source_history)

    with pytest.raises(
        ProjectArchiveError,
        match="Archive qualitative project is invalid",
    ):
        ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    assert not (restore_root / "studies" / study_id).exists()
    assert _destination_files(restore_root) == {}


def test_project_archive_rejects_invalid_qualitative_rows_before_publish(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _, _, _, exported = _build_qualitative_archive(source_root)
    forged_archive = tmp_path / "invalid-qualitative-row.nlpstudy.zip"
    _rewrite_qualitative_database(
        exported.archive_path,
        forged_archive,
        tmp_path / "invalid-row.sqlite3",
        """
        update case_attribute_values set value_json = '"private-value"'
        """,
    )

    with pytest.raises(
        ProjectArchiveError,
        match="Archive qualitative project is invalid",
    ) as error:
        ProjectArchiveStore(restore_root).restore_archive(forged_archive)

    assert "private-value" not in str(error.value)
    assert not (restore_root / "studies" / study_id).exists()
    assert _destination_files(restore_root) == {}


def test_project_archive_rejects_rehashed_unmatched_note_audit_before_publish(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _, exported = _build_note_archive(source_root)
    forged_archive = tmp_path / "unmatched-note-audit.nlpstudy.zip"
    _rewrite_qualitative_database(
        exported.archive_path,
        forged_archive,
        tmp_path / "unmatched-note-audit.sqlite3",
        """
        insert into qualitative_audit_events (
          event_id, project_id, actor_id, event_type,
          subject_type, subject_id, metadata_json, created_at
        )
        select
          'qae_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
          project_id,
          created_by,
          case when note_kind = 'memo' then 'memo.revised'
               else 'annotation.revised' end,
          note_kind,
          note_id,
          '{"note_revision_id":"nrv_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
            || '"revision_number":99}',
          created_at
        from qualitative_notes
        order by note_id
        limit 1
        """,
    )
    restore_root.mkdir()
    (restore_root / "sentinel.txt").write_text("unchanged", encoding="utf-8")
    before = _destination_tree(restore_root)

    with pytest.raises(
        ProjectArchiveError,
        match="Archive qualitative project is invalid",
    ):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive)

    assert _destination_tree(restore_root) == before
    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_rejects_rehashed_invalid_agent_suggestion_before_publish(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _, _, _, _, exported = _build_research_review_archive(source_root)
    forged_archive = tmp_path / "invalid-agent-suggestion.nlpstudy.zip"
    _rewrite_qualitative_database(
        exported.archive_path,
        forged_archive,
        tmp_path / "invalid-agent-suggestion.sqlite3",
        f"""
        drop trigger prevent_agent_coding_suggestion_update;
        update agent_coding_suggestions
        set cunit_id = 'cun_{'f' * 32}'
        """,
    )
    restore_root.mkdir()
    (restore_root / "sentinel.txt").write_text("unchanged", encoding="utf-8")
    before = _destination_tree(restore_root)

    with pytest.raises(
        ProjectArchiveError,
        match="Archive qualitative project is invalid",
    ):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive)

    assert _destination_tree(restore_root) == before
    assert not (restore_root / "studies" / study_id).exists()


@pytest.mark.parametrize(
    ("event_type_sql", "subject_type_sql"),
    (
        ("cast(' MEMO.CREATED ' as blob)", "'unrelated'"),
        ("'unrelated'", "cast(' ANNOTATION ' as blob)"),
    ),
)
def test_project_archive_rejects_rehashed_binary_note_audit_markers_before_publish(
    tmp_path: Path,
    event_type_sql: str,
    subject_type_sql: str,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _, exported = _build_note_archive(source_root)
    forged_archive = tmp_path / "binary-note-audit.nlpstudy.zip"
    _rewrite_qualitative_database(
        exported.archive_path,
        forged_archive,
        tmp_path / "binary-note-audit.sqlite3",
        f"""
        insert into qualitative_audit_events (
          event_id, project_id, actor_id, event_type,
          subject_type, subject_id, metadata_json, created_at
        )
        select
          'qae_cccccccccccccccccccccccccccccccc',
          project_id,
          created_by,
          {event_type_sql},
          {subject_type_sql},
          'unrelated_audit',
          '{{}}',
          created_at
        from qualitative_notes
        order by note_id
        limit 1
        """,
    )
    restore_root.mkdir()
    (restore_root / "sentinel.txt").write_text("unchanged", encoding="utf-8")
    before = _destination_tree(restore_root)

    with pytest.raises(
        ProjectArchiveError,
        match="Archive qualitative project is invalid",
    ):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive)

    assert _destination_tree(restore_root) == before
    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_rejects_newer_qualitative_schema_before_publish(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _, _, _, exported = _build_qualitative_archive(source_root)
    forged_archive = tmp_path / "newer-qualitative-schema.nlpstudy.zip"
    _rewrite_qualitative_database(
        exported.archive_path,
        forged_archive,
        tmp_path / "newer-qualitative.sqlite3",
        "pragma user_version = 99",
    )

    with pytest.raises(ProjectArchiveConflict, match="newer than supported"):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive)

    assert not (restore_root / "studies" / study_id).exists()
    assert _destination_files(restore_root) == {}


def test_project_archive_refuses_running_study_batch(tmp_path) -> None:
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Running Archive Study"})
    StudyBatchOperationStore(tmp_path, study.id).begin(
        batch_id="batch_20260729080808_77778888",
        skill_pack_version_id="archive_pack-1_0_0",
        skill_pack_sha256="a" * 64,
        request_sha256="b" * 64,
        item_count=0,
        created_at="2026-07-29T08:08:08+00:00",
    )

    with pytest.raises(ProjectArchiveConflict, match="running batch"):
        ProjectArchiveStore(tmp_path).create_archive(study.id)

    assert not list((tmp_path / "backups").glob("*.nlpstudy.zip"))


@pytest.mark.parametrize("missing_dependency", ["batch_manifest", "csv"])
def test_project_archive_refuses_corrupt_completed_source_snapshot(
    tmp_path,
    missing_dependency,
) -> None:
    study_id, _ = _build_study(tmp_path)
    study_dir = tmp_path / "studies" / study_id
    if missing_dependency == "batch_manifest":
        target = next(study_dir.glob("batches/*/batch.json"))
    else:
        target = next(study_dir.glob("batches/*/*.csv"))
    target.unlink()

    with pytest.raises(ProjectArchiveConflict, match="Completed study batch"):
        ProjectArchiveStore(tmp_path).create_archive(study_id)

    assert not list((tmp_path / "backups").glob("*.nlpstudy.zip"))


def test_project_archive_rejects_newer_batch_journal_before_restore_writes(
    tmp_path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    future_archive_path = tmp_path / "future-journal.nlpstudy.zip"
    future_db_path = tmp_path / "future-batch-operations.sqlite3"

    with ZipFile(exported.archive_path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    future_db_path.write_bytes(members["study/batch_operations.sqlite3"])
    with sqlite3.connect(future_db_path) as connection:
        connection.execute("pragma user_version = 99")
    members["study/batch_operations.sqlite3"] = future_db_path.read_bytes()
    manifest = json.loads(members["manifest.json"].decode("utf-8"))
    journal_record = next(
        record
        for record in manifest["members"]
        if record["path"] == "study/batch_operations.sqlite3"
    )
    journal_record["size_bytes"] = len(members["study/batch_operations.sqlite3"])
    journal_record["sha256"] = sha256(
        members["study/batch_operations.sqlite3"]
    ).hexdigest()
    members["manifest.json"] = json.dumps(
        manifest,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    with ZipFile(future_archive_path, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)

    with pytest.raises(ProjectArchiveConflict, match="newer than supported"):
        ProjectArchiveStore(restore_root).restore_archive(future_archive_path)

    assert not (restore_root / "studies" / study_id).exists()
    assert AuditLogStore(restore_root).list_events(limit=None) == []


@pytest.mark.parametrize(
    "missing_dependency",
    [
        "batch_manifest",
        "run",
        "evidence",
        "audit",
        "skill_pack",
        "skill_pack_metadata",
        "skill_pack_audit",
        "csv",
    ],
)
def test_project_archive_rejects_incomplete_completed_batch_dependencies(
    tmp_path,
    missing_dependency,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    forged_archive_path = tmp_path / f"missing-{missing_dependency}.nlpstudy.zip"

    def remove_dependency(members):
        if missing_dependency == "batch_manifest":
            target = next(
                name
                for name in members
                if name.startswith("study/batches/") and name.endswith("/batch.json")
            )
            members.pop(target)
        elif missing_dependency == "run":
            target = next(
                name
                for name in members
                if "/runs/" in name and name.endswith(".json")
            )
            members.pop(target)
        elif missing_dependency == "evidence":
            members["evidence/imports.json"] = b"[]"
            for name in list(members):
                if name.startswith("blobs/"):
                    members.pop(name)
        elif missing_dependency == "audit":
            audit_events = json.loads(members["evidence/audit.json"])
            members["evidence/audit.json"] = json.dumps(
                [
                    event
                    for event in audit_events
                    if event["event_type"] != "batch.completed"
                ],
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
        elif missing_dependency == "skill_pack":
            target = next(
                name
                for name in members
                if name.startswith("study/skill_packs/")
                and name.endswith(".json")
                and not name.endswith(".metadata.json")
            )
            payload = json.loads(members[target])
            payload["name"] = "Forged Skill Pack"
            members[target] = json.dumps(payload, indent=2).encode("utf-8")
        elif missing_dependency == "skill_pack_metadata":
            target = next(
                name
                for name in members
                if name.startswith("study/skill_packs/")
                and name.endswith(".metadata.json")
            )
            members.pop(target)
        elif missing_dependency == "skill_pack_audit":
            audit_events = json.loads(members["evidence/audit.json"])
            members["evidence/audit.json"] = json.dumps(
                [
                    event
                    for event in audit_events
                    if event["event_type"] != "skill_pack.versioned"
                ],
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
        else:
            target = next(
                name
                for name in members
                if name.startswith("study/batches/") and name.endswith(".csv")
            )
            members.pop(target)

    _rewrite_archive_members(
        exported.archive_path,
        forged_archive_path,
        remove_dependency,
    )

    expected_error = (
        "unavailable import"
        if missing_dependency == "evidence"
        else "completed batch artifacts are invalid"
    )
    with pytest.raises(ProjectArchiveError, match=expected_error):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive_path)

    assert not (restore_root / "studies" / study_id).exists()
    assert AuditLogStore(restore_root).list_events(limit=None) == []
    assert not (restore_root / "source_blobs").exists()


def test_project_archive_rejects_private_invalid_migration_timestamp(
    tmp_path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    forged_archive_path = tmp_path / "private-ledger.nlpstudy.zip"
    forged_db_path = tmp_path / "private-ledger.sqlite3"
    with ZipFile(exported.archive_path) as archive:
        forged_db_path.write_bytes(
            archive.read("study/batch_operations.sqlite3")
        )
    with sqlite3.connect(forged_db_path) as connection:
        connection.execute(
            "update schema_migrations set applied_at = 'PRIVATE-CONTENT'"
        )
    _rewrite_archive_journal(
        exported.archive_path,
        forged_archive_path,
        forged_db_path,
    )

    with pytest.raises(
        ProjectArchiveConflict,
        match="invalid applied_at",
    ) as error:
        ProjectArchiveStore(restore_root).restore_archive(forged_archive_path)

    assert "PRIVATE-CONTENT" not in str(error.value)
    assert not (restore_root / "studies" / study_id).exists()


@pytest.mark.parametrize(
    "tamper_sql",
    [
        """
        create trigger forged_history_delete
        after insert on study_batch_operations
        begin
          delete from study_batch_operations where batch_id != new.batch_id;
        end
        """,
        "drop trigger study_batch_operation_items_index_guard",
    ],
)
def test_project_archive_rejects_forged_batch_journal_schema(
    tmp_path,
    tamper_sql,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    forged_archive_path = tmp_path / "forged-schema.nlpstudy.zip"
    forged_db_path = tmp_path / "forged-schema.sqlite3"
    with ZipFile(exported.archive_path) as archive:
        forged_db_path.write_bytes(
            archive.read("study/batch_operations.sqlite3")
        )
    with sqlite3.connect(forged_db_path) as connection:
        connection.executescript(tamper_sql)
    _rewrite_archive_journal(
        exported.archive_path,
        forged_archive_path,
        forged_db_path,
    )

    with pytest.raises(ProjectArchiveError, match="journal is invalid"):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive_path)

    assert not (restore_root / "studies" / study_id).exists()
    assert AuditLogStore(restore_root).list_events(limit=None) == []
    assert not (restore_root / "source_blobs").exists()


@pytest.mark.parametrize(
    "tamper_sql",
    [
        "update study_batch_operation_items set run_id = '../escape'",
        "update study_batch_operations set updated_at = 'PRIVATE-CONTENT'",
        """
        update study_batch_operations
        set status = 'failed', stage = 'prepared', completed_at = '',
            last_error_type = 'PRIVATE-CONTENT'
        """,
        """
        update study_batch_operations
        set status = 'running', stage = 'prepared', completed_at = '',
            last_error_type = ''
        """,
        "update study_batch_operation_items set run_id = 'CON'",
        """
        update study_batch_operations set item_count = 2;
        update study_batch_operation_items set run_id = 'Run';
        insert into study_batch_operation_items (
          batch_id, item_index, item_request_sha256,
          run_id, import_id, project_source_id,
          source_blob_sha256, transcript_sha256,
          transcript_revision_id, run_payload_sha256,
          stage, last_error_type, created_at, updated_at
        )
        select batch_id, 1, item_request_sha256,
               'run', 'casefold-import', project_source_id,
               source_blob_sha256, transcript_sha256,
               transcript_revision_id, run_payload_sha256,
               stage, last_error_type, created_at, updated_at
        from study_batch_operation_items where item_index = 0
        """,
        """
        update study_batch_operations
        set item_count = 1.5, attempt_count = 1.5
        """,
        "update study_batch_operation_items set item_index = 0.5",
        """
        update study_batch_operations
        set aggregate_payload_sha256 =
          '0000000000000000000000000000000000000000000000000000000000000000'
        """,
        """
        update study_batch_operations
        set skill_pack_version_id = 'other-9_9_9'
        """,
    ],
)
def test_project_archive_rejects_invalid_batch_journal_rows(
    tmp_path,
    tamper_sql,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    forged_archive_path = tmp_path / "forged-rows.nlpstudy.zip"
    forged_db_path = tmp_path / "forged-rows.sqlite3"
    with ZipFile(exported.archive_path) as archive:
        forged_db_path.write_bytes(
            archive.read("study/batch_operations.sqlite3")
        )
    with sqlite3.connect(forged_db_path) as connection:
        connection.executescript(tamper_sql)
    _rewrite_archive_journal(
        exported.archive_path,
        forged_archive_path,
        forged_db_path,
    )

    with pytest.raises(ProjectArchiveError, match="journal is invalid"):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive_path)

    assert not (restore_root / "studies" / study_id).exists()
    assert AuditLogStore(restore_root).list_events(limit=None) == []
    assert not (restore_root / "source_blobs").exists()


def test_project_archive_rejects_hash_mismatch_and_unsafe_paths(tmp_path) -> None:
    source_root = tmp_path / "source"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    corrupt_path = tmp_path / "corrupt.zip"
    with ZipFile(exported.archive_path) as original, ZipFile(
        corrupt_path, "w", compression=ZIP_DEFLATED
    ) as corrupt:
        for info in original.infolist():
            content = original.read(info)
            if info.filename == "study/study.json":
                content = bytes([content[0] ^ 1]) + content[1:]
            corrupt.writestr(info.filename, content)
    with pytest.raises(ProjectArchiveError, match="hash mismatch"):
        ProjectArchiveStore(tmp_path / "restore-corrupt").restore_archive(corrupt_path)

    unsafe_path = tmp_path / "unsafe.zip"
    manifest = {
        "format_version": 1,
        "study_id": "archive-study",
        "members": [],
    }
    with ZipFile(unsafe_path, "w") as unsafe:
        unsafe.writestr("manifest.json", json.dumps(manifest))
        unsafe.writestr("../escape.txt", b"escape")
    with pytest.raises(ProjectArchiveError, match="unsafe member path"):
        ProjectArchiveStore(tmp_path / "restore-unsafe").restore_archive(unsafe_path)
    assert not (tmp_path / "escape.txt").exists()

    malformed_path = tmp_path / "malformed.zip"
    malformed_path.write_bytes(b"not a zip")
    with pytest.raises(ProjectArchiveError, match="malformed"):
        ProjectArchiveStore(tmp_path / "restore-malformed").restore_archive(
            malformed_path
        )

    incomplete_path = tmp_path / "incomplete.zip"
    empty_imports = b"[]"
    incomplete_manifest = {
        "format_version": 1,
        "study_id": "archive-study",
        "created_at": "2026-07-30T12:00:00+00:00",
        "members": [
            {
                "path": "evidence/imports.json",
                "size_bytes": len(empty_imports),
                "sha256": sha256(empty_imports).hexdigest(),
            }
        ],
    }
    with ZipFile(incomplete_path, "w") as incomplete:
        incomplete.writestr("manifest.json", json.dumps(incomplete_manifest))
        incomplete.writestr("evidence/imports.json", empty_imports)
    with pytest.raises(ProjectArchiveError, match="required members"):
        ProjectArchiveStore(tmp_path / "restore-incomplete").restore_archive(
            incomplete_path
        )


def test_project_archive_rejects_symlinked_study_root_before_journal_access(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    external_root = tmp_path / "external"
    study_id, _ = _build_study(external_root)
    (source_root / "studies").mkdir(parents=True)
    (source_root / "studies" / study_id).symlink_to(
        external_root / "studies" / study_id,
        target_is_directory=True,
    )

    with pytest.raises(ProjectArchiveError, match="root cannot be a symbolic link"):
        ProjectArchiveStore(source_root).create_archive(study_id)

    assert not (external_root / "studies" / study_id / ".archive.lock").exists()
    assert not (source_root / "backups").exists()


@pytest.mark.parametrize("ancestor_kind", ["root", "studies"])
def test_project_archive_rejects_symlinked_backup_source_ancestors(
    tmp_path: Path,
    ancestor_kind: str,
) -> None:
    external_root = tmp_path / "external"
    study_id, _ = _build_study(external_root)
    source_root = tmp_path / "source-link"
    if ancestor_kind == "root":
        source_root.symlink_to(external_root, target_is_directory=True)
    else:
        source_root.mkdir()
        (source_root / "studies").symlink_to(
            external_root / "studies",
            target_is_directory=True,
        )
    before = _destination_tree(external_root)

    with pytest.raises(ProjectArchiveError, match="non-symlink directory"):
        ProjectArchiveStore(source_root).create_archive(study_id)

    assert _destination_tree(external_root) == before
    assert not (external_root / "backups").exists()


def test_project_archive_rejects_symlinked_backup_directory_without_external_write(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    external_backup = tmp_path / "external-backup"
    study_id, _ = _build_study(source_root)
    external_backup.mkdir()
    (source_root / "backups").symlink_to(
        external_backup,
        target_is_directory=True,
    )

    with pytest.raises(ProjectArchiveError, match="non-symlink directory"):
        ProjectArchiveStore(source_root).create_archive(study_id)

    assert _destination_tree(external_backup) == {
        ".": ("directory", None),
    }


@pytest.mark.parametrize("audit_path_kind", ["directory", "events"])
def test_project_archive_export_rejects_symlinked_audit_paths_without_external_write(
    tmp_path: Path,
    audit_path_kind: str,
) -> None:
    source_root = tmp_path / "source"
    study_id, _ = _build_study(source_root)
    audit = AuditLogStore(source_root)
    if audit_path_kind == "directory":
        audit.audit_dir.rename(source_root / "original-audit")
        external_path = tmp_path / "external-audit"
        external_path.mkdir()
        (external_path / "sentinel").write_bytes(b"external audit sentinel")
        audit.audit_dir.symlink_to(external_path, target_is_directory=True)
    else:
        audit.events_path.rename(audit.audit_dir / "original-events.jsonl")
        external_path = tmp_path / "external-events.jsonl"
        external_path.write_bytes(b"external audit sentinel")
        audit.events_path.symlink_to(external_path)
    before = _destination_tree(external_path)

    with pytest.raises(ProjectArchiveError, match="non-symlink"):
        ProjectArchiveStore(source_root).create_archive(study_id)

    assert _destination_tree(external_path) == before
    assert not (source_root / "backups").exists()


@pytest.mark.parametrize("audit_path_kind", ["directory", "events"])
def test_project_archive_restore_rejects_symlinked_audit_paths_without_external_write(
    tmp_path: Path,
    audit_path_kind: str,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    restore_root.mkdir()
    audit = AuditLogStore(restore_root)
    if audit_path_kind == "directory":
        external_path = tmp_path / "external-audit"
        external_path.mkdir()
        (external_path / "sentinel").write_bytes(b"external audit sentinel")
        audit.audit_dir.symlink_to(external_path, target_is_directory=True)
    else:
        audit.audit_dir.mkdir()
        external_path = tmp_path / "external-events.jsonl"
        external_path.write_bytes(b"external audit sentinel")
        audit.events_path.symlink_to(external_path)
    before_destination = _destination_tree(restore_root)
    before_external = _destination_tree(external_path)

    with pytest.raises(ProjectArchiveError, match="conflicts with destination"):
        ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    assert _destination_tree(restore_root) == before_destination
    assert _destination_tree(external_path) == before_external


def test_project_archive_export_rejects_symlinked_workspace_lock_without_external_write(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    study_id, _ = _build_study(source_root)
    lock_path = source_root / ".workspace-mutation.lock"
    lock_path.rename(source_root / "original-workspace-lock")
    external_lock = tmp_path / "external-workspace-lock"
    external_lock.write_bytes(b"external lock sentinel")
    lock_path.symlink_to(external_lock)

    with pytest.raises(ProjectArchiveError, match="non-symlink regular file"):
        ProjectArchiveStore(source_root).create_archive(study_id)

    assert external_lock.read_bytes() == b"external lock sentinel"
    assert not (source_root / "backups").exists()


def test_project_archive_restore_rejects_symlinked_workspace_lock_without_external_write(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    restore_root.mkdir()
    external_lock = tmp_path / "external-workspace-lock"
    external_lock.write_bytes(b"external lock sentinel")
    (restore_root / ".workspace-mutation.lock").symlink_to(external_lock)
    before = _destination_tree(restore_root)

    with pytest.raises(ProjectArchiveError, match="workspace lock is invalid"):
        ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    assert _destination_tree(restore_root) == before
    assert external_lock.read_bytes() == b"external lock sentinel"


def test_project_archive_rejects_symlinked_restore_studies_ancestor(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    external_studies = tmp_path / "external-studies"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    restore_root.mkdir()
    external_studies.mkdir()
    (restore_root / "studies").symlink_to(
        external_studies,
        target_is_directory=True,
    )
    before = _destination_tree(restore_root)

    with pytest.raises(ProjectArchiveError, match="non-symlink directory"):
        ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    assert _destination_tree(restore_root) == before
    assert _destination_tree(external_studies) == {
        ".": ("directory", None),
    }


@pytest.mark.parametrize(
    "blob_ancestor",
    ["source_blobs", "evidence_text_blobs"],
)
def test_project_archive_rejects_symlinked_restore_blob_ancestors(
    tmp_path: Path,
    blob_ancestor: str,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    external_blobs = tmp_path / "external-blobs"
    _, _, _, _, _, exported = _build_cunit_target_archive(source_root)
    restore_root.mkdir()
    external_blobs.mkdir()
    (restore_root / blob_ancestor).symlink_to(
        external_blobs,
        target_is_directory=True,
    )
    before = _destination_tree(restore_root)

    with pytest.raises(ProjectArchiveError, match="conflicts with destination"):
        ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    assert _destination_tree(restore_root) == before
    assert _destination_tree(external_blobs) == {
        ".": ("directory", None),
    }


def test_project_archive_backup_names_do_not_collide(tmp_path: Path) -> None:
    study_id, _ = _build_study(tmp_path)
    store = ProjectArchiveStore(tmp_path)

    first = store.create_archive(study_id)
    second = store.create_archive(study_id)

    assert first.archive_path != second.archive_path
    assert first.archive_path.is_file()
    assert second.archive_path.is_file()


def test_project_archive_rejects_semantically_invalid_unreferenced_skill_pack(
    tmp_path: Path,
) -> None:
    store = StudyWorkspaceStore(tmp_path)
    study = store.create_study({"name": "Invalid Unreferenced Pack"})
    store.add_skill_pack_version(
        study.id,
        {
            "id": "invalid_unreferenced_pack",
            "version": "1.0.0",
            "metrics": [],
        },
        validate=False,
    )

    with pytest.raises(ProjectArchiveConflict, match="semantically invalid"):
        ProjectArchiveStore(tmp_path).create_archive(study.id)

    assert not list((tmp_path / "backups").glob("*.nlpstudy.zip"))


def test_project_archive_restores_true_format_v1_without_target_state(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study = StudyWorkspaceStore(source_root).create_study(
        {"name": "True Format V1"}
    )
    study_id = study.id
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    legacy_archive = tmp_path / "legacy-format-v1.nlpstudy.zip"

    _rewrite_archive_as_v1(
        exported.archive_path,
        legacy_archive,
    )

    ProjectArchiveStore(restore_root).restore_archive(legacy_archive)

    restored_study = StudyWorkspaceStore(restore_root).load_study(study_id)
    assert restored_study == study
    assert EvidenceTargetRegistry(restore_root).workspace_snapshot(study_id) == ()


def test_project_archive_rejects_v2_target_members_declared_as_v1(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _, _, _, _, exported = _build_cunit_target_archive(source_root)
    forged_archive = tmp_path / "v2-members-declared-v1.nlpstudy.zip"
    _rewrite_archive_manifest(
        exported.archive_path,
        forged_archive,
        lambda manifest: {**manifest, "format_version": 1},
    )

    with pytest.raises(ProjectArchiveError, match="cannot contain evidence target"):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive)

    assert _destination_tree(restore_root) == {}
    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_rejects_v1_nested_study_artifact_target_reference(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    forged_archive = tmp_path / "v1-study-target-reference.nlpstudy.zip"

    def add_target_reference(members):
        members["study/legacy-metadata.json"] = json.dumps(
            {
                "nested": [
                    {"evidence_set_id": "evs_0123456789abcdef0123456789abcdef"}
                ]
            }
        ).encode("utf-8")

    _rewrite_archive_as_v1(
        exported.archive_path,
        forged_archive,
        add_target_reference,
    )

    with pytest.raises(ProjectArchiveError, match="cannot reference evidence"):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive)

    assert _destination_tree(restore_root) == {}


def test_project_archive_rejects_deep_v1_target_reference_without_recursion_leak(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    forged_archive = tmp_path / "deep-v1-study-target-reference.nlpstudy.zip"

    def add_deep_target_reference(members):
        target = b'{"evidence_set_id":"evs_0123456789abcdef0123456789abcdef"}'
        members["study/deep-legacy-metadata.json"] = (
            b"[" * 600 + target + b"]" * 600
        )

    _rewrite_archive_as_v1(
        exported.archive_path,
        forged_archive,
        add_deep_target_reference,
    )

    with pytest.raises(ProjectArchiveError, match="cannot reference evidence"):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive)

    assert _destination_tree(restore_root) == {}


def test_project_archive_rejects_v1_qualitative_coding_reference_row(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _, _, _, exported = _build_coding_reference_archive(source_root)
    forged_archive = tmp_path / "v1-coding-row.nlpstudy.zip"
    _rewrite_archive_as_v1(exported.archive_path, forged_archive)

    with pytest.raises(ProjectArchiveError, match="cannot reference evidence"):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive)

    assert _destination_tree(restore_root) == {}
    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_rejects_v1_agent_suggestion_row(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _, _, _, _, exported = _build_research_review_archive(source_root)
    forged_archive = tmp_path / "v1-agent-suggestion-row.nlpstudy.zip"
    _rewrite_archive_as_v1(exported.archive_path, forged_archive)

    with pytest.raises(ProjectArchiveError, match="cannot reference evidence"):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive)

    assert _destination_tree(restore_root) == {}
    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_v1_accepts_non_excerpt_note_and_ignores_body_literal(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id = StudyWorkspaceStore(source_root).create_study(
        {"name": "Legacy note archive"}
    ).id
    QualitativeProjectDatabase(source_root, study_id).initialize(
        researcher_id="res_legacy_note",
        researcher_name="Legacy Note Researcher",
    )
    snapshot = NoteService(source_root, study_id).create_note(
        note_kind="memo",
        researcher_id="res_legacy_note",
        title="Legacy-compatible memo",
        body="The literal evidence_set_id is researcher-authored content.",
        target={"kind": "study"},
    )
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    legacy_archive = tmp_path / "v1-non-excerpt-note.nlpstudy.zip"
    _rewrite_archive_as_v1(exported.archive_path, legacy_archive)

    ProjectArchiveStore(restore_root).restore_archive(legacy_archive)

    assert NoteService(restore_root, study_id).read_note(
        "memo",
        snapshot.note.note_id,
    ) == snapshot


def test_project_archive_v1_rejects_excerpt_note_row(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id = StudyWorkspaceStore(source_root).create_study(
        {"name": "Version 1 excerpt note"}
    ).id
    import_record, prepared, passage_id, _ = _add_cunit_evidence(
        source_root,
        study_id,
    )
    QualitativeProjectDatabase(source_root, study_id).initialize(
        researcher_id="res_v1_excerpt_note",
        researcher_name="Version 1 Excerpt Researcher",
    )
    NoteService(source_root, study_id).create_note(
        note_kind="annotation",
        researcher_id="res_v1_excerpt_note",
        title="",
        body="Direct note evidence target.",
        target={
            "kind": "excerpt",
            "project_source_id": import_record.project_source_id,
            "transcript_revision_id": import_record.transcript_revision_id,
            "evidence_set_id": prepared.evidence_set_id,
            "excerpt_target_kind": "passage",
            "passage_id": passage_id,
            "start_offset": 0,
            "end_offset": len("I came"),
        },
    )
    with sqlite3.connect(
        source_root / "studies" / study_id / "qualitative.sqlite3"
    ) as connection:
        assert connection.execute(
            "select count(*) from coding_references"
        ).fetchone() == (0,)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    legacy_archive = tmp_path / "v1-excerpt-note.nlpstudy.zip"
    _rewrite_archive_as_v1(exported.archive_path, legacy_archive)

    with pytest.raises(ProjectArchiveError, match="cannot reference evidence"):
        ProjectArchiveStore(restore_root).restore_archive(legacy_archive)

    assert _destination_tree(restore_root) == {}
    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_rejects_v1_qualitative_audit_metadata_reference(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _, _, _, exported = _build_qualitative_archive(source_root)
    tampered_v2 = tmp_path / "audit-metadata-v2.nlpstudy.zip"
    forged_archive = tmp_path / "v1-audit-metadata.nlpstudy.zip"
    _rewrite_qualitative_database(
        exported.archive_path,
        tampered_v2,
        tmp_path / "audit-metadata.sqlite3",
        """
        insert into qualitative_audit_events (
          event_id, project_id, actor_id, event_type,
          subject_type, subject_id, metadata_json, created_at
        )
        select
          'qae_v1_target_metadata', project_id, actor_id,
          'legacy.targeted', 'qualitative_project', project_id,
          '{"evidence_set_id":"evs_0123456789abcdef0123456789abcdef"}',
          created_at
        from qualitative_audit_events
        limit 1
        """,
    )
    _rewrite_archive_as_v1(tampered_v2, forged_archive)

    with pytest.raises(ProjectArchiveError, match="cannot reference evidence"):
        ProjectArchiveStore(restore_root).restore_archive(forged_archive)

    assert _destination_tree(restore_root) == {}


def test_project_archive_round_trips_original_pre_audit_batch_generation(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    source_store = StudyWorkspaceStore(source_root)
    batch = source_store.list_batches(study_id)[0]
    run_path = next((batch.aggregate_dir / "runs").glob("*.json"))
    run_payload = json.loads(run_path.read_text(encoding="utf-8"))
    run_path.write_text(
        json.dumps(
            {
                key: run_payload[key]
                for key in (
                    "run_id",
                    "source_filename",
                    "created_at",
                    "turn_count",
                    "results",
                )
            }
        ),
        encoding="utf-8",
    )
    aggregate_path = batch.aggregate_dir / "aggregate_results.json"
    aggregate_payload = json.loads(aggregate_path.read_text(encoding="utf-8"))
    aggregate_payload.pop("study_schema")
    aggregate_path.write_text(json.dumps(aggregate_payload), encoding="utf-8")
    (source_root / "audit" / "events.jsonl").write_text("", encoding="utf-8")
    (source_root / "studies" / study_id / "batch_operations.sqlite3").unlink()

    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    restored = ProjectArchiveStore(restore_root).restore_archive(
        exported.archive_path
    )

    batches = StudyWorkspaceStore(restore_root).list_batches(study_id)
    assert restored.study_id == study_id
    assert len(batches) == 1
    assert StudyWorkspaceStore(restore_root).list_batch_runs(
        study_id,
        batches[0].batch_id,
    )


@pytest.mark.parametrize("catalog_workspace", ["legacy", "local-default"])
@pytest.mark.parametrize("destination_blob_state", ["absent", "corrupt"])
def test_project_archive_preserves_import_v1_catalog_without_original_blob(
    tmp_path: Path,
    catalog_workspace: str,
    destination_blob_state: str,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    source_store = StudyWorkspaceStore(source_root)
    batch = source_store.list_batches(study_id)[0]
    run_path = next((batch.aggregate_dir / "runs").glob("*.json"))
    run_payload = json.loads(run_path.read_text(encoding="utf-8"))
    project_source_id = run_payload["project_source_id"]
    for field_name in (
        "project_source_id",
        "parent_transcript_revision_id",
        "workspace_id",
        "evidence_set_id",
    ):
        run_payload.pop(field_name)
    run_path.write_text(json.dumps(run_payload), encoding="utf-8")
    catalog = EvidenceCatalog(source_root)
    with sqlite3.connect(catalog.db_path) as connection:
        connection.execute(
            """
            update project_sources set workspace_id = ?
            where project_source_id = ?
            """,
            (catalog_workspace, project_source_id),
        )
    SourceBlobStore(source_root).blob_path(
        run_payload["source_blob_sha256"]
    ).unlink()
    (source_root / "studies" / study_id / "batch_operations.sqlite3").unlink()

    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    with ZipFile(exported.archive_path) as archive:
        archived_imports = json.loads(archive.read("evidence/imports.json"))
        unretained_blobs = json.loads(
            archive.read("evidence/unretained_blobs.json")
        )
        assert f"blobs/{run_payload['source_blob_sha256']}.blob" not in (
            archive.namelist()
        )
    if destination_blob_state == "corrupt":
        destination_blob = SourceBlobStore(restore_root).blob_path(
            run_payload["source_blob_sha256"]
        )
        destination_blob.parent.mkdir(parents=True, exist_ok=True)
        destination_blob.write_bytes(b"corrupt")
        with pytest.raises(ProjectArchiveError, match="destination"):
            ProjectArchiveStore(restore_root).restore_archive(
                exported.archive_path
            )
        assert not (restore_root / "studies" / study_id).exists()
        return
    restored = ProjectArchiveStore(restore_root).restore_archive(
        exported.archive_path
    )

    restored_imports = EvidenceCatalog(restore_root).workspace_import_records(
        study_id
    )
    assert archived_imports[0]["import_id"] == run_payload["import_id"]
    assert archived_imports[0]["workspace_id"] == study_id
    assert unretained_blobs == [run_payload["source_blob_sha256"]]
    assert restored.import_count == 1
    assert restored.blob_count == 0
    assert restored_imports[0].import_id == run_payload["import_id"]
    assert StudyWorkspaceStore(restore_root).list_batch_runs(
        study_id,
        batch.batch_id,
    )


@pytest.mark.parametrize("marker_state", ["unreferenced", "journal-backed"])
def test_project_archive_rejects_invalid_unretained_blob_marker(
    tmp_path: Path,
    marker_state: str,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, digest = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    tampered_archive = tmp_path / f"unretained-{marker_state}.nlpstudy.zip"

    def mutate(members):
        marked_digest = "0" * 64 if marker_state == "unreferenced" else digest
        members["evidence/unretained_blobs.json"] = json.dumps(
            [marked_digest]
        ).encode("utf-8")
        if marker_state == "journal-backed":
            members.pop(f"blobs/{digest}.blob")

    _rewrite_archive_members(
        exported.archive_path,
        tampered_archive,
        mutate,
    )

    with pytest.raises(ProjectArchiveError):
        ProjectArchiveStore(restore_root).restore_archive(tampered_archive)

    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_rejects_unretained_marker_for_non_batch_import(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study = StudyWorkspaceStore(source_root).create_study(
        {"name": "Ordinary Evidence Archive Study"}
    )
    content = b"ordinary retained source"
    digest = sha256(content).hexdigest()
    SourceBlobStore(source_root).store(content, digest)
    EvidenceCatalog(source_root).record_import(
        EvidenceImportRecord(
            import_id="imp_ordinary_archive_evidence",
            run_id="run_ordinary_archive_evidence",
            pipeline="analysis",
            source_id=f"src_{digest[:32]}",
            source_filename="ordinary.txt",
            source_media_type="text/plain",
            source_blob_sha256=digest,
            transcript_revision_id=f"trv_{digest[:32]}",
            transcript_sha256=digest,
            imported_at="2026-08-01T12:00:00+00:00",
            project_source_id="psrc_ordinary_archive_evidence",
            workspace_id=study.id,
        )
    )
    exported = ProjectArchiveStore(source_root).create_archive(study.id)
    tampered_archive = tmp_path / "ordinary-unretained.nlpstudy.zip"

    def mutate(members):
        members.pop(f"blobs/{digest}.blob")
        members["evidence/unretained_blobs.json"] = json.dumps([digest]).encode(
            "utf-8"
        )

    _rewrite_archive_members(
        exported.archive_path,
        tampered_archive,
        mutate,
    )

    with pytest.raises(ProjectArchiveError, match="artifacts are invalid"):
        ProjectArchiveStore(restore_root).restore_archive(tampered_archive)

    assert not (restore_root / "studies" / study.id).exists()


@pytest.mark.parametrize(
    "artifact_kind",
    ["aggregate", "run", "identity", "csv", "audit"],
)
def test_project_archive_rejects_tampered_pre_journal_batch_on_backup(
    tmp_path: Path,
    artifact_kind: str,
) -> None:
    study_id, _ = _build_study(tmp_path)
    study_dir = tmp_path / "studies" / study_id
    batch = StudyWorkspaceStore(tmp_path).list_batches(study_id)[0]
    (study_dir / "batch_operations.sqlite3").unlink()
    if artifact_kind == "aggregate":
        (batch.aggregate_dir / "aggregate_results.json").write_text(
            json.dumps({"results": [], "failures": []}),
            encoding="utf-8",
        )
    elif artifact_kind == "run":
        next((batch.aggregate_dir / "runs").glob("*.json")).write_text(
            json.dumps({"run_id": "tampered"}),
            encoding="utf-8",
        )
    elif artifact_kind == "identity":
        run_path = next((batch.aggregate_dir / "runs").glob("*.json"))
        run_payload = json.loads(run_path.read_text(encoding="utf-8"))
        run_payload["import_id"] = "import_tampered"
        run_path.write_text(json.dumps(run_payload), encoding="utf-8")
    elif artifact_kind == "csv":
        next(batch.aggregate_dir.glob("*.csv")).write_text(
            "tampered\n",
            encoding="utf-8",
        )
    else:
        events_path = tmp_path / "audit" / "events.jsonl"
        events = AuditLogStore(tmp_path).list_events(limit=None)
        events_path.write_text(
            "".join(
                json.dumps(event) + "\n"
                for event in events
                if event.get("event_type") != "batch.completed"
            ),
            encoding="utf-8",
        )

    with pytest.raises(ProjectArchiveConflict, match="Legacy completed"):
        ProjectArchiveStore(tmp_path).create_archive(study_id)

    assert not list((tmp_path / "backups").glob("*.nlpstudy.zip"))


@pytest.mark.parametrize(
    "artifact_kind",
    ["aggregate", "run", "identity", "csv", "audit"],
)
def test_project_archive_rejects_tampered_pre_journal_batch_on_restore(
    tmp_path: Path,
    artifact_kind: str,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    tampered_archive = tmp_path / f"legacy-{artifact_kind}.nlpstudy.zip"

    def mutate(members):
        members.pop("study/batch_operations.sqlite3")
        if artifact_kind == "aggregate":
            aggregate_name = "study/batches/" + next(
                name.split("/")[2]
                for name in members
                if name.endswith("/aggregate_results.json")
            ) + "/aggregate_results.json"
            members[aggregate_name] = b"[]"
        elif artifact_kind == "run":
            run_name = next(
                name
                for name in members
                if "/runs/" in name and name.endswith(".json")
            )
            members[run_name] = json.dumps({"run_id": "tampered"}).encode(
                "utf-8"
            )
        elif artifact_kind == "identity":
            run_name = next(
                name
                for name in members
                if "/runs/" in name and name.endswith(".json")
            )
            run_payload = json.loads(members[run_name])
            run_payload["import_id"] = "import_tampered"
            members[run_name] = json.dumps(run_payload).encode("utf-8")
        elif artifact_kind == "csv":
            csv_name = next(name for name in members if name.endswith(".csv"))
            members[csv_name] = b"tampered\n"
        else:
            events = json.loads(members["evidence/audit.json"])
            members["evidence/audit.json"] = json.dumps(
                [
                    event
                    for event in events
                    if event.get("event_type") != "batch.completed"
                ]
            ).encode("utf-8")

    _rewrite_archive_members(
        exported.archive_path,
        tampered_archive,
        mutate,
    )

    with pytest.raises(ProjectArchiveError, match="artifacts are invalid"):
        ProjectArchiveStore(restore_root).restore_archive(tampered_archive)

    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_rejects_hash_aligned_non_object_run_snapshot(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    source_store = StudyWorkspaceStore(source_root)
    batch = source_store.list_batches(study_id)[0]
    item = StudyBatchOperationStore(source_root, study_id).list_items(
        batch.batch_id
    )[0]
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    tampered_archive = tmp_path / "hash-aligned-non-object-run.nlpstudy.zip"
    journal_copy = tmp_path / "tampered-journal.sqlite3"
    with ZipFile(exported.archive_path) as archive:
        journal_copy.write_bytes(
            archive.read("study/batch_operations.sqlite3")
        )
    with sqlite3.connect(journal_copy) as connection:
        connection.execute(
            """
            update study_batch_operation_items
            set run_payload_sha256 = ?
            where batch_id = ? and item_index = ?
            """,
            (sha256(b"[]").hexdigest(), batch.batch_id, item["item_index"]),
        )

    def mutate(members):
        members["study/batch_operations.sqlite3"] = journal_copy.read_bytes()
        run_name = next(
            name
            for name in members
            if name.endswith(f"/runs/{item['run_id']}.json")
        )
        members[run_name] = b"[]"

    _rewrite_archive_members(
        exported.archive_path,
        tampered_archive,
        mutate,
    )

    with pytest.raises(ProjectArchiveError, match="artifacts are invalid"):
        ProjectArchiveStore(restore_root).restore_archive(tampered_archive)

    assert not (restore_root / "studies" / study_id).exists()


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "C:/study/study.json",
        "study/C:/study.json",
        "study//study.json",
        "study/./study.json",
        "study/folder../study.json",
        "study/folder /study.json",
        "study/CON/study.json",
        "study/con.txt",
        "study/COM¹.txt",
        "study/LPT³.txt",
        "study/question?.json",
        "study/star*.json",
        'study/quote".json',
        "study/pipe|.json",
        "study/less<.json",
        "study/control\x01.json",
        f"study/{'a' * 256}/study.json",
        f"study/{'😀' * 128}/study.json",
        "study/e\u0301/study.json",
    ],
)
def test_project_archive_rejects_nonportable_member_paths(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    archive_path = tmp_path / "unsafe-portable.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format_version": 1,
                    "study_id": "portable-study",
                    "created_at": "2026-07-30T12:00:00+00:00",
                    "members": [],
                }
            ),
        )
        archive.writestr(unsafe_name, b"unsafe")

    with pytest.raises(ProjectArchiveError, match="unsafe member path"):
        ProjectArchiveStore(tmp_path / "restore").restore_archive(archive_path)


def test_project_archive_rejects_file_directory_member_collisions(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "file-directory-collision.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", b"{}")
        archive.writestr("study/a", b"file")
        archive.writestr("study/a/b", b"child")

    with pytest.raises(ProjectArchiveError, match="file-directory-colliding"):
        ProjectArchiveStore(tmp_path / "restore").restore_archive(archive_path)


@pytest.mark.parametrize(
    ("header_offset", "field_value", "message"),
    [
        (8, 99, "unsupported compression"),
        (6, 1, "encrypted member"),
    ],
)
def test_project_archive_rejects_unsupported_zip_member_features(
    tmp_path: Path,
    header_offset: int,
    field_value: int,
    message: str,
) -> None:
    archive_path = tmp_path / "unsupported-member.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", b"{}")
    archive_bytes = bytearray(archive_path.read_bytes())
    local_header = archive_bytes.index(b"PK\x03\x04")
    central_header = archive_bytes.index(b"PK\x01\x02")
    archive_bytes[
        local_header + header_offset : local_header + header_offset + 2
    ] = field_value.to_bytes(2, "little")
    central_offset = header_offset + 2
    archive_bytes[
        central_header + central_offset : central_header + central_offset + 2
    ] = field_value.to_bytes(2, "little")
    archive_path.write_bytes(archive_bytes)

    with pytest.raises(ProjectArchiveError, match=message):
        ProjectArchiveStore(tmp_path / "restore").restore_archive(archive_path)


def test_project_archive_translates_zip_member_read_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive_path = tmp_path / "unreadable-member.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", b"{}")

    def fail_read(self, name, pwd=None):
        raise RuntimeError("injected ZIP read failure")

    monkeypatch.setattr(ZipFile, "read", fail_read)

    with pytest.raises(ProjectArchiveError, match="member data is malformed"):
        ProjectArchiveStore(tmp_path / "restore").restore_archive(archive_path)


def test_project_archive_rejects_casefold_colliding_member_paths(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "casefold-collision.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format_version": 1,
                    "study_id": "portable-study",
                    "created_at": "2026-07-30T12:00:00+00:00",
                    "members": [],
                }
            ),
        )
        archive.writestr("study/File.json", b"one")
        archive.writestr("study/file.json", b"two")

    with pytest.raises(ProjectArchiveError, match="case-colliding"):
        ProjectArchiveStore(tmp_path / "restore").restore_archive(archive_path)


def test_project_archive_rejects_overlong_legacy_study_id_before_writes(
    tmp_path: Path,
) -> None:
    study_id = "s" * 97
    archive_path = tmp_path / "overlong-study.zip"
    members = {
        "study/study.json": json.dumps(
            {
                "id": study_id,
                "name": "Overlong Legacy Study",
                "description": "",
                "created_at": "2026-07-30T12:00:00+00:00",
            }
        ).encode("utf-8"),
        "evidence/imports.json": b"[]",
        "evidence/audit.json": b"[]",
    }
    _write_archive(archive_path, study_id=study_id, members=members)
    restore_root = tmp_path / "restore-overlong"

    with pytest.raises(ProjectArchiveError, match="Invalid study id"):
        ProjectArchiveStore(restore_root).restore_archive(archive_path)

    assert not restore_root.exists()


@pytest.mark.parametrize(
    ("mutate_manifest", "message"),
    [
        (lambda manifest: [], "manifest is malformed"),
        (
            lambda manifest: {**manifest, "unexpected": True},
            "manifest is malformed",
        ),
        (
            lambda manifest: {**manifest, "format_version": True},
            "Unsupported archive format version",
        ),
        (
            lambda manifest: {**manifest, "created_at": None},
            "manifest timestamp is invalid",
        ),
        (
            lambda manifest: {**manifest, "members": [None]},
            "manifest member is invalid",
        ),
        (
            lambda manifest: {
                **manifest,
                "members": [
                    {**manifest["members"][0], "size_bytes": "1"},
                    *manifest["members"][1:],
                ],
            },
            "member size is invalid",
        ),
        (
            lambda manifest: {
                **manifest,
                "members": [
                    {**manifest["members"][0], "sha256": 7},
                    *manifest["members"][1:],
                ],
            },
            "member hash is invalid",
        ),
    ],
)
def test_project_archive_rejects_malformed_manifest_shapes(
    tmp_path: Path,
    mutate_manifest,
    message: str,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    malformed_archive = tmp_path / "malformed-manifest.zip"
    _rewrite_archive_manifest(
        exported.archive_path,
        malformed_archive,
        mutate_manifest,
    )

    with pytest.raises(ProjectArchiveError, match=message):
        ProjectArchiveStore(restore_root).restore_archive(malformed_archive)

    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_translates_malformed_study_json(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    malformed_archive = tmp_path / "malformed-study-json.zip"

    def mutate(members):
        members["study/study.json"] = b"{not-json"

    _rewrite_archive_members(
        exported.archive_path,
        malformed_archive,
        mutate,
    )

    with pytest.raises(ProjectArchiveError, match="study record is malformed"):
        ProjectArchiveStore(restore_root).restore_archive(malformed_archive)

    assert not (restore_root / "studies" / study_id).exists()


@pytest.mark.parametrize(
    ("member_name", "mutate_payload", "message"),
    [
        (
            "study/study.json",
            lambda payload: {**payload, "name": 7},
            "study record is malformed",
        ),
        (
            "evidence/imports.json",
            lambda payload: [
                {**payload[0], "source_blob_sha256": "A" * 64},
                *payload[1:],
            ],
            "evidence SHA-256 is invalid",
        ),
        (
            "evidence/imports.json",
            lambda payload: [
                {**payload[0], "imported_at": "2026-07-30T12:00:00"},
                *payload[1:],
            ],
            "evidence import timestamp is invalid",
        ),
        (
            "evidence/imports.json",
            lambda payload: [
                {**payload[0], "source_filename": "x" * 4097},
                *payload[1:],
            ],
            "evidence records are malformed",
        ),
        (
            "evidence/audit.json",
            lambda payload: [
                {**payload[0], "metadata": []},
                *payload[1:],
            ],
            "audit records are malformed",
        ),
        (
            "evidence/audit.json",
            lambda payload: [
                {**payload[0], "created_at": "not-a-timestamp"},
                *payload[1:],
            ],
            "audit event timestamp is invalid",
        ),
    ],
)
def test_project_archive_validates_imported_record_types_and_bounds(
    tmp_path: Path,
    member_name: str,
    mutate_payload,
    message: str,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    malformed_archive = tmp_path / "malformed-record.zip"

    def mutate(members):
        payload = json.loads(members[member_name].decode("utf-8"))
        members[member_name] = json.dumps(mutate_payload(payload)).encode("utf-8")

    _rewrite_archive_members(
        exported.archive_path,
        malformed_archive,
        mutate,
    )

    with pytest.raises(ProjectArchiveError, match=message):
        ProjectArchiveStore(restore_root).restore_archive(malformed_archive)

    assert not (restore_root / "studies" / study_id).exists()


@pytest.mark.parametrize("conflict_kind", ["evidence", "audit", "blob"])
def test_project_archive_preflights_destination_conflicts_without_partial_writes(
    tmp_path: Path,
    conflict_kind: str,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    with ZipFile(exported.archive_path) as archive:
        import_payload = json.loads(archive.read("evidence/imports.json"))[0]
        audit_payload = json.loads(archive.read("evidence/audit.json"))[0]

    if conflict_kind == "evidence":
        EvidenceCatalog(restore_root).record_import(
            EvidenceImportRecord(
                **{**import_payload, "run_id": f"{import_payload['run_id']}-other"}
            )
        )
    elif conflict_kind == "audit":
        AuditLogStore(restore_root).import_events(
            [{**audit_payload, "actor": "conflicting-actor"}]
        )
    else:
        blob_path = SourceBlobStore(restore_root).blob_path(
            import_payload["source_blob_sha256"]
        )
        blob_path.parent.mkdir(parents=True)
        blob_path.write_bytes(b"corrupt destination blob")
    before = _destination_files(restore_root)

    with pytest.raises(ProjectArchiveError, match="conflicts with destination"):
        ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    assert _destination_files(restore_root) == before
    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_preflights_evidence_text_conflict_without_writes(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _, prepared, _, _, exported = _build_cunit_target_archive(
        source_root
    )
    digest = _prepared_text_blob_sha256s(prepared)[0]
    destination = EvidenceTextBlobStore(restore_root).blob_path(digest)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"corrupt destination evidence text")
    before = _destination_tree(restore_root)

    with pytest.raises(ProjectArchiveError, match="conflicts with destination"):
        ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    assert _destination_tree(restore_root) == before
    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_rejects_newer_empty_destination_evidence_database(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study = StudyWorkspaceStore(source_root).create_study(
        {"name": "Newer Destination Evidence"}
    )
    exported = ProjectArchiveStore(source_root).create_archive(study.id)
    restore_root.mkdir()
    destination_catalog = EvidenceCatalog(restore_root)
    with sqlite3.connect(destination_catalog.db_path) as connection:
        connection.execute("pragma user_version = 99")
    before = _destination_tree(restore_root)

    with pytest.raises(ProjectArchiveConflict, match="newer"):
        ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    assert _destination_tree(restore_root) == before
    assert not (restore_root / "studies" / study.id).exists()


def test_project_archive_accepts_exact_preexisting_evidence_target_replay(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    (
        study_id,
        import_record,
        prepared,
        _,
        _,
        exported,
    ) = _build_cunit_target_archive(source_root)
    source_content = SourceBlobStore(source_root).read_verified(
        import_record.source_blob_sha256
    )
    SourceBlobStore(restore_root).store(
        source_content,
        import_record.source_blob_sha256,
    )
    EvidenceCatalog(restore_root).record_import(import_record)
    preexisting_snapshot = EvidenceTargetRegistry(
        restore_root
    ).register_complete_set(prepared)
    before_texts = {
        digest: EvidenceTextBlobStore(restore_root).read_verified(digest)
        for digest in _prepared_text_blob_sha256s(prepared)
    }

    restored = ProjectArchiveStore(restore_root).restore_archive(
        exported.archive_path
    )

    assert restored.study_id == study_id
    assert EvidenceCatalog(restore_root).workspace_import_records(study_id) == [
        import_record
    ]
    assert EvidenceTargetRegistry(restore_root).workspace_snapshot(study_id) == (
        preexisting_snapshot,
    )
    assert {
        digest: EvidenceTextBlobStore(restore_root).read_verified(digest)
        for digest in _prepared_text_blob_sha256s(prepared)
    } == before_texts


def test_project_archive_rolls_back_destination_after_late_publish_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)
    destination_store = StudyWorkspaceStore(restore_root)
    destination_study = destination_store.create_study(
        {"name": "Existing Destination"}
    )
    destination_version = destination_store.add_skill_pack_version(
        destination_study.id,
        {
            "id": "destination_pack",
            "name": "Destination Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
    )
    destination_store.run_text_batch(
        destination_study.id,
        destination_version.version_id,
        [{"source_filename": "existing.txt", "content": "CG: One.\nP: Two."}],
    )
    before = _destination_tree(restore_root)
    archive_store = ProjectArchiveStore(restore_root)
    original_publish = archive_store._publish_study

    def fail_publish(stage_dir, study_dir):
        original_publish(stage_dir, study_dir)
        raise OSError("injected late publish failure")

    monkeypatch.setattr(archive_store, "_publish_study", fail_publish)

    with pytest.raises(ProjectArchiveError, match="could not be committed"):
        archive_store.restore_archive(exported.archive_path)

    assert _destination_tree(restore_root) == before
    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_rolls_back_new_destination_ancestors_after_publish(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "absent-restore"
    study_id, _, _, _, exported = _build_coding_reference_archive(source_root)
    archive_store = ProjectArchiveStore(restore_root)
    original_publish = archive_store._publish_study

    def publish_then_fail(stage_dir, study_dir):
        original_publish(stage_dir, study_dir)
        raise OSError("injected post-publish failure")

    monkeypatch.setattr(archive_store, "_publish_study", publish_then_fail)

    assert _destination_tree(restore_root) == {}
    with pytest.raises(ProjectArchiveError, match="could not be committed"):
        archive_store.restore_archive(exported.archive_path)

    assert _destination_tree(restore_root) == {}
    assert not (restore_root / "studies" / study_id).exists()


def test_project_archive_rolls_back_source_blob_when_store_raises_after_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    _, _, _, _, _, exported = _build_cunit_target_archive(source_root)
    original_store = SourceBlobStore.store

    def store_then_raise(store, content, expected_sha256):
        stored = original_store(store, content, expected_sha256)
        if store.root == restore_root:
            raise OSError("injected source blob post-write failure")
        return stored

    monkeypatch.setattr(SourceBlobStore, "store", store_then_raise)

    with pytest.raises(ProjectArchiveError, match="could not be committed"):
        ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    assert _destination_tree(restore_root) == {}


def test_project_archive_rolls_back_text_blob_when_store_raises_after_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    _, _, _, _, _, exported = _build_cunit_target_archive(source_root)
    original_store = EvidenceTextBlobStore.store

    def store_then_raise(store, text, expected_sha256):
        stored = original_store(store, text, expected_sha256)
        if store.root == restore_root:
            raise OSError("injected evidence text post-write failure")
        return stored

    monkeypatch.setattr(EvidenceTextBlobStore, "store", store_then_raise)

    with pytest.raises(ProjectArchiveError, match="could not be committed"):
        ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    assert _destination_tree(restore_root) == {}


def test_project_archive_translates_sqlite_import_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "source"
    restore_root = tmp_path / "restore"
    study_id, _ = _build_study(source_root)
    exported = ProjectArchiveStore(source_root).create_archive(study_id)

    def fail_import(self, record):
        raise sqlite3.InterfaceError("injected binding failure")

    monkeypatch.setattr(EvidenceCatalog, "record_import", fail_import)

    with pytest.raises(ProjectArchiveError, match="artifacts are invalid"):
        ProjectArchiveStore(restore_root).restore_archive(exported.archive_path)

    assert not (restore_root / "studies" / study_id).exists()
