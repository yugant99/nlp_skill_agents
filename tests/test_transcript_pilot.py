from __future__ import annotations

import json
import re
import sqlite3
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.transcript_pilot import api as pilot_api
from backend.professor_demo.provider import (
    LunaProviderError,
    PreflightReceipt,
    ProviderCallReceipt,
    RedactionNonverbalResult,
    RepairOverlapResult,
    SpeakerTurnResult,
    SPECIALIST_SPECS,
    TimingPauseResult,
)
from backend.transcript_pilot.protocol import PROTOCOL_VERSION, chunk_transcript
from backend.transcript_pilot.provider import PilotPreflightReceipt
from backend.transcript_pilot.provider import (
    LunaTranscriptClient,
    PilotProviderAmbiguousError,
    request_sha256,
)
from backend.transcript_pilot.service import (
    SAMPLE_TRANSCRIPT,
    TranscriptPilotError,
    TranscriptPilotRuntime,
    TranscriptPilotService,
)
from backend.transcript_pilot.store import (
    TranscriptPilotStore,
    TranscriptPilotStoreError,
    _migrate_v1,
)


@pytest.fixture(autouse=True)
def _verified_executable_identity(monkeypatch):
    monkeypatch.setenv("TRANSCRIPT_PILOT_CODE_COMMIT", "a" * 40)
    monkeypatch.setenv("TRANSCRIPT_PILOT_CODE_DIRTY", "false")


class FakeLunaClient:
    def __init__(self, classification: str) -> None:
        self.classification = classification
        self.call_count = 0

    def build_payload(self, spec, transcript_lines: list[str]) -> dict:
        return {
            "specialist": spec.specialist_id,
            "classification": self.classification,
            "lines": transcript_lines,
        }

    def preflight_job(self, chunks, *, authorized_cost_usd: str):
        max_cost_per_call = (
            Decimal("8000") * Decimal("0.0000001")
            + Decimal("800") * Decimal("0.0000002")
        )
        return PilotPreflightReceipt(
            model="openai/gpt-5.6-luna",
            provider="Azure",
            endpoint="azure/eu",
            endpoint_is_zdr=True,
            required_parameters_supported=True,
            metadata_request_count=3,
            chunk_count=len(chunks),
            planned_call_count=len(chunks) * 4,
            max_prompt_tokens_per_call=8000,
            max_completion_tokens_per_call=800,
            request_price_per_call_usd="0",
            estimated_max_cost_usd=str(
                max_cost_per_call * Decimal(len(chunks) * 4)
            ),
            max_cost_per_call_usd=str(max_cost_per_call),
            authorized_cost_usd=authorized_cost_usd,
            global_cost_ceiling_usd="5.00",
            prompt_price_per_token_usd="0.0000001",
            completion_price_per_token_usd="0.0000002",
            checked_at="2026-08-23T00:00:00+00:00",
        )

    def call_specialist(self, spec, transcript_lines: list[str]):
        self.call_count += 1
        lines = []
        for index, source in enumerate(transcript_lines):
            timestamp_match = re.match(r"^\[(\d{2}:\d{2}(?::\d{2})?)\]", source)
            timestamp = timestamp_match.group(1) if timestamp_match else "unknown"
            if re.search(r"(?:\[|\()long pause(?:\]|\))", source, flags=re.IGNORECASE):
                pause = "long"
            elif re.search(
                r"(?:\[|\()(?:short )?pause(?:\]|\))",
                source,
                flags=re.IGNORECASE,
            ):
                pause = "short"
            else:
                pause = "none"
            without_timestamp = re.sub(
                r"^\[\d{2}:\d{2}(?::\d{2})?\]\s*", "", source
            )
            speaker_match = re.match(r"([^:]+):\s*(.*)$", without_timestamp)
            label = speaker_match.group(1).strip() if speaker_match else ""
            spoken = speaker_match.group(2) if speaker_match else without_timestamp
            spoken = re.sub(
                r"\s*(?:\[|\()(?:short |long )?pause(?:\]|\))\s*",
                " ",
                spoken,
                flags=re.IGNORECASE,
            ).strip()
            if spec.specialist_id == "speaker_turn":
                normalized = label.casefold()
                speaker = (
                    "Interviewer"
                    if normalized in {"interviewer", "i", "int", "q"}
                    else "Participant"
                    if normalized in {"participant", "p", "par", "a"}
                    else "Unknown"
                )
                lines.append({"line_index": index, "speaker": speaker})
            elif spec.specialist_id == "timing_pause":
                lines.append(
                    {"line_index": index, "timestamp": timestamp, "pause": pause}
                )
            elif spec.specialist_id == "repair_overlap":
                lines.append({"line_index": index, "cleaned_text": spoken})
            else:
                lines.append(
                    {"line_index": index, "redactions": [], "nonverbal_cues": []}
                )
        model = {
            "speaker_turn": SpeakerTurnResult,
            "timing_pause": TimingPauseResult,
            "repair_overlap": RepairOverlapResult,
            "redaction_nonverbal": RedactionNonverbalResult,
        }[spec.specialist_id]
        result = model.model_validate(
            {"specialist_id": spec.specialist_id, "lines": lines}, strict=True
        )
        receipt = ProviderCallReceipt(
            model_requested="openai/gpt-5.6-luna",
            endpoint_requested="azure/eu",
            generation_id=f"gen-{self.call_count}",
            model_returned="openai/gpt-5.6-luna-20260801",
            provider_returned="Azure",
            router_attempt_count=1,
            cache_hit=False,
            finish_reason="stop",
            prompt_tokens=20,
            completion_tokens=10,
            reasoning_tokens=0,
            total_tokens=30,
            cost_usd="0.0001",
            accounting_complete=True,
            latency_ms=5,
        )
        return result, receipt


class FailingPreflightClient(FakeLunaClient):
    def preflight_job(self, chunks, *, authorized_cost_usd: str):
        raise LunaProviderError(
            "preflight_endpoint_unavailable",
            "The pinned Luna endpoint is unavailable",
        )


@pytest.fixture
def service(tmp_path: Path):
    clients: list[FakeLunaClient] = []

    def factory(classification):
        client = FakeLunaClient(classification)
        clients.append(client)
        return client

    return TranscriptPilotService(tmp_path, client_factory=factory), clients


def _source(service: TranscriptPilotService, *, transcript: str | None = None):
    study = service.create_study(
        name="Professor Pilot",
        description="Synthetic supervised transcript revision",
        researcher_id="res_professor",
        researcher_name="Professor Example",
        study_id="professor-pilot",
    )
    content = transcript or (
        "[00:00] Q: What happened next?\n"
        "[00:05] A: Um, I... I waited. (long pause)"
    )
    source = service.import_source(
        study_id=study["study_id"],
        researcher_id="res_professor",
        source_filename="synthetic.txt",
        source_media_type="text/plain",
        source_bytes=content.encode("utf-8"),
        extracted_text=content,
        data_classification="synthetic",
        authorization_basis="Synthetic professor demonstration fixture",
        remote_egress_authorized=True,
        contains_direct_identifiers=False,
        protocol_version=PROTOCOL_VERSION,
    )
    return study, source


def _job(service: TranscriptPilotService, source: dict, key: str = "job-key-0001"):
    return service.create_job(
        source_id=source["source_id"],
        researcher_id="res_professor",
        input_revision_id=source["active_revision_id"],
        idempotency_key=key,
        authorized_cost_usd="0.50",
        confirmation="authorize-four-specialists-per-chunk",
    )


def _preflight_payload(
    service: TranscriptPilotService,
    job: dict,
    *,
    checked_at: str | None = None,
) -> dict:
    loaded = service.store.load_job(job["job_id"])
    transcript = "\n".join(
        line for chunk in loaded["chunks"] for line in chunk["lines"]
    )
    payload = FakeLunaClient("synthetic").preflight_job(
        chunk_transcript(transcript),
        authorized_cost_usd=loaded["authorized_cost_usd"],
    ).model_dump(mode="json")
    if checked_at is not None:
        payload["checked_at"] = checked_at
    return payload


def test_chunk_plan_is_deterministic_and_plans_four_calls_per_chunk():
    transcript = "\n".join(f"[{index:02d}:00] Q: line {index}" for index in range(7))
    chunks = chunk_transcript(transcript)
    assert [(item.start_line_index, item.end_line_index) for item in chunks] == [
        (0, 5),
        (6, 6),
    ]
    assert len(chunks) * 4 == 8


def test_request_fingerprint_binds_preflight_price_caps():
    client = LunaTranscriptClient(classification="synthetic")
    client._provider_max_price = {
        "prompt": 0.22,
        "completion": 1.32,
        "request": 0.0,
    }
    first = request_sha256(client, SPECIALIST_SPECS[0], SAMPLE_TRANSCRIPT.splitlines())
    client._provider_max_price = {
        "prompt": 0.23,
        "completion": 1.32,
        "request": 0.0,
    }
    second = request_sha256(client, SPECIALIST_SPECS[0], SAMPLE_TRANSCRIPT.splitlines())

    assert first != second


def test_v1_database_upgrades_authorization_and_provenance_columns(tmp_path: Path):
    store = TranscriptPilotStore(tmp_path)
    store.pilot_root.mkdir(parents=True)
    with sqlite3.connect(store.db_path) as connection:
        _migrate_v1(connection)
        connection.execute(
            """
            create table schema_migrations (
              version integer primary key,
              name text not null,
              applied_at text not null
            )
            """
        )
        connection.execute(
            """
            insert into schema_migrations (version, name, applied_at)
            values (1, 'researcher-supervised transcript pilot',
                    '2026-08-23T00:00:00+00:00')
            """
        )
        connection.execute("pragma user_version = 1")
        for column in (
            "authorization_confirmation",
            "authorization_actor_id",
            "authorization_revision_id",
            "authorization_transcript_sha256",
            "authorization_at",
        ):
            connection.execute(f"alter table pilot_jobs drop column {column}")

    assert [item["version"] for item in store.migration_status()] == [1, 2]
    with store.read() as connection:
        columns = {
            str(row[1]) for row in connection.execute("pragma table_info(pilot_jobs)")
        }
    assert {
        "authorization_confirmation",
        "authorization_actor_id",
        "authorization_revision_id",
        "authorization_transcript_sha256",
        "authorization_at",
    }.issubset(columns)


def test_deidentified_source_with_direct_identifier_fails_before_client_creation(
    tmp_path: Path,
):
    clients: list[FakeLunaClient] = []
    service = TranscriptPilotService(
        tmp_path,
        client_factory=lambda classification: clients.append(FakeLunaClient(classification)),
    )
    study = service.create_study(
        name="Privacy Pilot",
        description="",
        researcher_id="res_privacy",
        researcher_name="Privacy Researcher",
        study_id="privacy-pilot",
    )
    content = "[00:00] Participant: Email me at participant@example.com"
    with pytest.raises(TranscriptPilotError, match="possible direct identifiers"):
        service.import_source(
            study_id=study["study_id"],
            researcher_id="res_privacy",
            source_filename="blocked.txt",
            source_media_type="text/plain",
            source_bytes=content.encode(),
            extracted_text=content,
            data_classification="authorized-deidentified",
            authorization_basis="Approved de-identification workflow reference 12",
            remote_egress_authorized=True,
            contains_direct_identifiers=False,
            protocol_version=PROTOCOL_VERSION,
        )
    assert clients == []


def test_identical_transcript_can_belong_to_two_distinct_project_sources(tmp_path: Path):
    pilot = TranscriptPilotService(
        tmp_path,
        client_factory=lambda classification: FakeLunaClient(classification),
    )
    _, first = _source(pilot)
    second_study = pilot.create_study(
        name="Second Pilot",
        description="",
        researcher_id="res_second",
        researcher_name="Second Researcher",
        study_id="second-pilot",
    )
    content = (
        "[00:00] Q: What happened next?\n"
        "[00:05] A: Um, I... I waited. (long pause)"
    )
    second = pilot.import_source(
        study_id=second_study["study_id"],
        researcher_id="res_second",
        source_filename="same-content.txt",
        source_media_type="text/plain",
        source_bytes=content.encode(),
        extracted_text=content,
        data_classification="synthetic",
        authorization_basis="Second synthetic professor fixture",
        remote_egress_authorized=True,
        contains_direct_identifiers=False,
        protocol_version=PROTOCOL_VERSION,
    )
    assert second["source_id"] != first["source_id"]
    assert second["original_revision_id"] == first["original_revision_id"]


def test_intake_retry_replays_reserved_cross_store_identity(tmp_path: Path, monkeypatch):
    pilot = TranscriptPilotService(
        tmp_path,
        client_factory=lambda classification: FakeLunaClient(classification),
    )
    study = pilot.create_study(
        name="Replay Pilot",
        description="",
        researcher_id="res_replay",
        researcher_name="Replay Researcher",
        study_id="replay-pilot",
    )
    content = "[00:00] Q: Can this intake resume?\n[00:02] A: Yes."
    original_create_source = pilot.store.create_source
    failed_once = False

    def interrupt_after_catalog(payload):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise TranscriptPilotStoreError(
                "simulated_intake_interruption",
                "Simulated interruption after catalog publication",
            )
        return original_create_source(payload)

    monkeypatch.setattr(pilot.store, "create_source", interrupt_after_catalog)
    arguments = {
        "study_id": study["study_id"],
        "researcher_id": "res_replay",
        "source_filename": "replay.txt",
        "source_media_type": "text/plain",
        "source_bytes": content.encode("utf-8"),
        "extracted_text": content,
        "data_classification": "synthetic",
        "authorization_basis": "Synthetic replay fixture",
        "remote_egress_authorized": True,
        "contains_direct_identifiers": False,
        "protocol_version": PROTOCOL_VERSION,
    }

    with pytest.raises(TranscriptPilotError, match="Simulated interruption"):
        pilot.import_source(**arguments)
    source = pilot.import_source(**arguments)
    duplicate = pilot.import_source(**arguments)

    assert duplicate["source_id"] == source["source_id"]
    assert len(pilot.evidence_catalog.list_imports()) == 1
    assert len(pilot.store.list_sources(study["study_id"])) == 1
    with pilot.store.read() as connection:
        intake = connection.execute(
            "select status, source_id from pilot_intakes"
        ).fetchone()
    assert intake == ("completed", source["source_id"])


def test_multichunk_job_executes_exactly_four_calls_per_chunk_and_is_idempotent(service):
    pilot, clients = service
    transcript = "\n".join(f"[{index:02d}:00] Q: line {index}" for index in range(7))
    _, source = _source(pilot, transcript=transcript)
    job = _job(pilot, source)
    replay = _job(pilot, source)
    assert replay["job_id"] == job["job_id"]
    pilot.execute_job(job["job_id"])
    completed = pilot.store.load_job(job["job_id"])
    assert completed["status"] == "needs_review"
    assert completed["progress"]["planned_call_count"] == 8
    assert completed["progress"]["valid_call_count"] == 8
    assert completed["usage"]["accounting_complete"] is True
    assert clients[0].call_count == 8
    assert completed["authorization_confirmation"] == (
        "authorize-four-specialists-per-chunk"
    )
    assert completed["authorization_actor_id"] == "res_professor"
    assert completed["authorization_revision_id"] == source["active_revision_id"]
    assert completed["authorization_transcript_sha256"] == (
        source["original_transcript_sha256"]
    )
    assert completed["authorization_at"]
    with pytest.raises(TranscriptPilotStoreError, match="can no longer be cancelled"):
        pilot.store.mark_cancelled(job["job_id"])
    assert pilot.store.load_job(job["job_id"])["status"] == "needs_review"

    with pytest.raises(TranscriptPilotError, match="Idempotency key"):
        pilot.create_job(
            source_id=source["source_id"],
            researcher_id="res_professor",
            input_revision_id=source["active_revision_id"],
            idempotency_key="job-key-0001",
            authorized_cost_usd="0.75",
            confirmation="authorize-four-specialists-per-chunk",
        )


def test_review_commit_lineage_and_restore_preserve_original(service):
    pilot, _ = service
    _, source = _source(pilot)
    original_revision_id = source["original_revision_id"]
    job = _job(pilot, source)
    pilot.execute_job(job["job_id"])
    review = pilot.store.load_job(job["job_id"])
    for proposal in review["proposals"]:
        if proposal["changed"]:
            review = pilot.save_decision(
                job_id=job["job_id"],
                proposal_id=proposal["proposal_id"],
                researcher_id="res_professor",
                action="accept",
                edited_text="",
                expected_decision_version=0,
            )
    committed = pilot.commit(
        job_id=job["job_id"],
        researcher_id="res_professor",
        expected_active_revision_id=original_revision_id,
    )
    assert committed["status"] == "committed"
    child_id = committed["committed_revision_id"]
    assert child_id != original_revision_id
    source_after = pilot.load_source(source["source_id"])
    assert source_after["active_revision_id"] == child_id
    assert source_after["original_revision_id"] == original_revision_id
    history = pilot.evidence_catalog.source_history(source["source_id"])
    child = next(item for item in history["revisions"] if item["transcript_revision_id"] == child_id)
    assert child["parent_transcript_revision_id"] == original_revision_id

    replay = _job(pilot, source)
    assert replay["job_id"] == job["job_id"]

    restored = pilot.restore_original(
        source_id=source["source_id"],
        researcher_id="res_professor",
        expected_active_revision_id=child_id,
    )
    assert restored["active_revision_id"] == original_revision_id
    assert any(item["revision_id"] == child_id for item in restored["revisions"])


def test_repeated_historical_content_cannot_claim_a_new_parent(service):
    pilot, _ = service
    _, source = _source(pilot)
    original_revision_id = source["original_revision_id"]

    first_job = _job(pilot, source)
    pilot.execute_job(first_job["job_id"])
    for proposal in pilot.store.load_job(first_job["job_id"])["proposals"]:
        if proposal["changed"]:
            pilot.save_decision(
                job_id=first_job["job_id"],
                proposal_id=proposal["proposal_id"],
                researcher_id="res_professor",
                action="accept",
                edited_text="",
                expected_decision_version=0,
            )
    first_commit = pilot.commit(
        job_id=first_job["job_id"],
        researcher_id="res_professor",
        expected_active_revision_id=original_revision_id,
    )
    pilot.restore_original(
        source_id=source["source_id"],
        researcher_id="res_professor",
        expected_active_revision_id=first_commit["committed_revision_id"],
    )

    second_job = _job(pilot, pilot.load_source(source["source_id"]), "job-key-0002")
    pilot.execute_job(second_job["job_id"])
    for proposal in pilot.store.load_job(second_job["job_id"])["proposals"]:
        if proposal["changed"]:
            pilot.save_decision(
                job_id=second_job["job_id"],
                proposal_id=proposal["proposal_id"],
                researcher_id="res_professor",
                action="accept",
                edited_text="",
                expected_decision_version=0,
            )
    with pytest.raises(TranscriptPilotError, match="exact transcript already exists"):
        pilot.commit(
            job_id=second_job["job_id"],
            researcher_id="res_professor",
            expected_active_revision_id=original_revision_id,
        )


def test_stale_decision_and_wrong_commit_parent_fail_closed(service):
    pilot, _ = service
    _, source = _source(pilot)
    job = _job(pilot, source)
    pilot.execute_job(job["job_id"])
    review = pilot.store.load_job(job["job_id"])
    proposal = next(item for item in review["proposals"] if item["changed"])
    with pytest.raises(TranscriptPilotError, match="cannot add a transcript line break"):
        pilot.save_decision(
            job_id=job["job_id"],
            proposal_id=proposal["proposal_id"],
            researcher_id="res_professor",
            action="edit",
            edited_text="one line\nsecond line",
            expected_decision_version=0,
        )
    pilot.save_decision(
        job_id=job["job_id"],
        proposal_id=proposal["proposal_id"],
        researcher_id="res_professor",
        action="keep_original",
        edited_text="",
        expected_decision_version=0,
    )
    with pytest.raises(TranscriptPilotError, match="decision changed"):
        pilot.save_decision(
            job_id=job["job_id"],
            proposal_id=proposal["proposal_id"],
            researcher_id="res_professor",
            action="accept",
            edited_text="",
            expected_decision_version=0,
        )
    for item in pilot.store.load_job(job["job_id"])["proposals"]:
        if item["changed"] and item["decision"] is None:
            pilot.save_decision(
                job_id=job["job_id"],
                proposal_id=item["proposal_id"],
                researcher_id="res_professor",
                action="accept",
                edited_text="",
                expected_decision_version=0,
            )
    with pytest.raises(TranscriptPilotError, match="only create a child"):
        pilot.commit(
            job_id=job["job_id"],
            researcher_id="res_professor",
            expected_active_revision_id="trv_" + "0" * 32,
        )


def test_commit_reservation_rejects_a_stale_review_snapshot(service):
    pilot, _ = service
    _, source = _source(pilot)
    job = _job(pilot, source)
    pilot.execute_job(job["job_id"])
    for proposal in pilot.store.load_job(job["job_id"])["proposals"]:
        if proposal["changed"]:
            pilot.save_decision(
                job_id=job["job_id"],
                proposal_id=proposal["proposal_id"],
                researcher_id="res_professor",
                action="accept",
                edited_text="",
                expected_decision_version=0,
            )
    stale = pilot.store.load_job(job["job_id"])
    changed = next(item for item in stale["proposals"] if item["changed"])
    pilot.save_decision(
        job_id=job["job_id"],
        proposal_id=changed["proposal_id"],
        researcher_id="res_professor",
        action="keep_original",
        edited_text="",
        expected_decision_version=1,
    )

    with pytest.raises(TranscriptPilotStoreError, match="decision changed"):
        pilot.store.prepare_commit(
            job_id=job["job_id"],
            researcher_id="res_professor",
            expected_active_revision_id=source["active_revision_id"],
            revision_id="trv_" + "a" * 32,
            transcript_sha256="a" * 64,
            import_id="imp_stale_review_snapshot",
            review_snapshot_sha256=stale["review_snapshot_sha256"],
        )


def test_only_one_revision_can_publish_for_a_source_parent(service):
    pilot, _ = service
    _, source = _source(pilot)
    first = _job(pilot, source, "publisher-job-0001")
    second = _job(pilot, source, "publisher-job-0002")
    for job in (first, second):
        pilot.execute_job(job["job_id"])
        for proposal in pilot.store.load_job(job["job_id"])["proposals"]:
            if proposal["changed"]:
                pilot.save_decision(
                    job_id=job["job_id"],
                    proposal_id=proposal["proposal_id"],
                    researcher_id="res_professor",
                    action="accept",
                    edited_text="",
                    expected_decision_version=0,
                )
    first_review = pilot.store.load_job(first["job_id"])
    second_review = pilot.store.load_job(second["job_id"])
    pilot.store.prepare_commit(
        job_id=first["job_id"],
        researcher_id="res_professor",
        expected_active_revision_id=source["active_revision_id"],
        revision_id="trv_" + "b" * 32,
        transcript_sha256="b" * 64,
        import_id="imp_first_publisher",
        review_snapshot_sha256=first_review["review_snapshot_sha256"],
    )

    with pytest.raises(TranscriptPilotStoreError, match="restore is temporarily blocked"):
        pilot.store.restore_original(
            source_id=source["source_id"],
            researcher_id="res_professor",
            expected_active_revision_id=source["active_revision_id"],
        )

    with pytest.raises(TranscriptPilotStoreError, match="already publishing"):
        pilot.store.prepare_commit(
            job_id=second["job_id"],
            researcher_id="res_professor",
            expected_active_revision_id=source["active_revision_id"],
            revision_id="trv_" + "c" * 32,
            transcript_sha256="c" * 64,
            import_id="imp_second_publisher",
            review_snapshot_sha256=second_review["review_snapshot_sha256"],
        )


def test_preflight_failure_attempts_zero_calls(tmp_path: Path):
    clients: list[FailingPreflightClient] = []

    def factory(classification):
        client = FailingPreflightClient(classification)
        clients.append(client)
        return client

    pilot = TranscriptPilotService(tmp_path, client_factory=factory)
    _, source = _source(pilot)
    job = _job(pilot, source)
    pilot.execute_job(job["job_id"])
    failed = pilot.store.load_job(job["job_id"])
    assert failed["status"] == "failed"
    assert failed["progress"]["attempted_call_count"] == 0
    assert clients[0].call_count == 0


def test_cancel_before_execution_never_calls_specialist(service):
    pilot, clients = service
    _, source = _source(pilot)
    job = _job(pilot, source)
    pilot.store.request_cancel(job["job_id"], "res_professor")
    pilot.execute_job(job["job_id"])
    cancelled = pilot.store.load_job(job["job_id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["progress"]["attempted_call_count"] == 0
    assert clients == []


def test_worker_failure_preserves_a_concurrent_cancellation(service):
    pilot, _ = service
    _, source = _source(pilot)
    job = _job(pilot, source)
    assert pilot.store.claim_job(job["job_id"])
    pilot.store.request_cancel(job["job_id"], "res_professor")

    pilot.store.fail_job(job["job_id"], "worker_failed", "Simulated worker failure")

    cancelled = pilot.store.load_job(job["job_id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["error_code"] == "cancelled_by_researcher"
    assert all(
        call["status"] == "cancelled"
        for chunk in cancelled["chunks"]
        for call in chunk["calls"]
    )
    assert all(chunk["status"] == "cancelled" for chunk in cancelled["chunks"])


def test_recovery_marks_inflight_call_ambiguous_without_requeue(service):
    pilot, _ = service
    _, source = _source(pilot)
    job = _job(pilot, source)
    assert pilot.store.claim_job(job["job_id"])
    preflight_id = pilot.store.set_preflight(
        job["job_id"],
        _preflight_payload(pilot, job),
    )
    pilot.store.begin_call(
        job["job_id"],
        0,
        "speaker_turn",
        "0" * 64,
        preflight_id,
    )
    queued = pilot.store.recover_interrupted_jobs()
    recovered = pilot.store.load_job(job["job_id"])
    assert recovered["status"] == "needs_attention"
    assert recovered["chunks"][0]["calls"][0]["status"] == "ambiguous"
    assert job["job_id"] not in queued


def test_worker_failure_marks_an_inflight_call_ambiguous_immediately(service):
    pilot, _ = service
    _, source = _source(pilot)
    job = _job(pilot, source)
    assert pilot.store.claim_job(job["job_id"])
    preflight_id = pilot.store.set_preflight(
        job["job_id"], _preflight_payload(pilot, job)
    )
    pilot.store.begin_call(
        job["job_id"],
        0,
        "speaker_turn",
        "0" * 64,
        preflight_id,
    )

    pilot.store.fail_job(job["job_id"], "worker_failed", "Simulated failure")

    failed = pilot.store.load_job(job["job_id"])
    assert failed["status"] == "needs_attention"
    assert failed["chunks"][0]["status"] == "failed"
    assert failed["chunks"][0]["calls"][0]["status"] == "ambiguous"


def test_recovery_expires_preflight_before_resuming_pending_calls(service):
    pilot, _ = service
    _, source = _source(pilot)
    job = _job(pilot, source)
    assert pilot.store.claim_job(job["job_id"])
    first_preflight_id = pilot.store.set_preflight(
        job["job_id"],
        _preflight_payload(pilot, job, checked_at="stale"),
    )
    chunk_lines = pilot.store.load_job(job["job_id"])["chunks"][0]["lines"]
    result, receipt = FakeLunaClient("synthetic").call_specialist(
        SPECIALIST_SPECS[0], chunk_lines
    )
    pilot.store.begin_call(
        job["job_id"],
        0,
        "speaker_turn",
        "0" * 64,
        first_preflight_id,
    )
    pilot.store.complete_call(
        job_id=job["job_id"],
        chunk_index=0,
        specialist_id="speaker_turn",
        status="valid",
        result=result.model_dump(mode="json"),
        receipt=receipt.model_dump(mode="json"),
    )
    queued = pilot.store.recover_interrupted_jobs()
    recovered = pilot.store.load_job(job["job_id"])
    assert queued == [job["job_id"]]
    assert recovered["status"] == "queued"
    assert recovered["preflight"] is None
    assert [item["preflight_id"] for item in recovered["preflight_history"]] == [
        first_preflight_id
    ]
    assert recovered["chunks"][0]["calls"][0]["preflight_id"] == first_preflight_id

    assert pilot.store.claim_job(job["job_id"])
    second_preflight_id = pilot.store.set_preflight(
        job["job_id"],
        _preflight_payload(pilot, job, checked_at="fresh"),
    )
    resumed = pilot.store.load_job(job["job_id"])
    assert second_preflight_id != first_preflight_id
    assert len(resumed["preflight_history"]) == 2
    assert resumed["chunks"][0]["calls"][0]["preflight_id"] == first_preflight_id


def test_recovery_quarantines_a_changed_execution_contract(service, monkeypatch):
    monkeypatch.setenv("TRANSCRIPT_PILOT_CODE_COMMIT", "a" * 40)
    monkeypatch.setenv("TRANSCRIPT_PILOT_CODE_DIRTY", "false")
    pilot, _ = service
    _, source = _source(pilot)
    job = _job(pilot, source)
    assert pilot.store.claim_job(job["job_id"])
    pilot.store.set_preflight(
        job["job_id"], _preflight_payload(pilot, job, checked_at="stale")
    )
    provenance = pilot.store.load_job(job["job_id"])["provenance"]
    provenance["prompt_version"] = "obsolete-prompt"
    with pilot.store.transaction() as connection:
        connection.execute(
            "update pilot_jobs set provenance_json = ? where job_id = ?",
            (
                json.dumps(provenance, sort_keys=True, separators=(",", ":")),
                job["job_id"],
            ),
        )

    assert pilot.recover_interrupted_jobs() == []
    blocked = pilot.store.load_job(job["job_id"])
    assert blocked["status"] == "needs_attention"
    assert blocked["error_code"] == "execution_contract_changed"


def test_valid_call_preserves_missing_cost_as_a_terminal_error(service):
    pilot, _ = service
    _, source = _source(pilot)
    job = _job(pilot, source)
    assert pilot.store.claim_job(job["job_id"])
    preflight_id = pilot.store.set_preflight(
        job["job_id"], _preflight_payload(pilot, job, checked_at="now")
    )
    chunk_lines = pilot.store.load_job(job["job_id"])["chunks"][0]["lines"]
    result, receipt = FakeLunaClient("synthetic").call_specialist(
        SPECIALIST_SPECS[0], chunk_lines
    )
    incomplete_receipt = receipt.model_dump(mode="json")
    incomplete_receipt["cost_usd"] = None
    pilot.store.begin_call(
        job["job_id"],
        0,
        "speaker_turn",
        "0" * 64,
        preflight_id,
    )
    pilot.store.complete_call(
        job_id=job["job_id"],
        chunk_index=0,
        specialist_id="speaker_turn",
        status="valid",
        result=result.model_dump(mode="json"),
        receipt=incomplete_receipt,
    )
    stored_call = pilot.store.load_job(job["job_id"])["chunks"][0]["calls"][0]
    assert stored_call["status"] == "error"
    assert stored_call["error_code"] == "provider_receipt_invalid"
    assert stored_call["receipt"]["cost_usd"] is None


def test_provider_error_cost_breach_is_preserved_and_needs_attention(service):
    pilot, _ = service
    _, source = _source(pilot)
    job = _job(pilot, source)
    assert pilot.store.claim_job(job["job_id"])
    preflight_id = pilot.store.set_preflight(
        job["job_id"], _preflight_payload(pilot, job)
    )
    pilot.store.begin_call(
        job["job_id"], 0, "speaker_turn", "0" * 64, preflight_id
    )
    _, receipt = FakeLunaClient("synthetic").call_specialist(
        SPECIALIST_SPECS[0],
        pilot.store.load_job(job["job_id"])["chunks"][0]["lines"],
    )
    over_budget = receipt.model_dump(mode="json")
    over_budget["cost_usd"] = "0.5"

    pilot.store.complete_call(
        job_id=job["job_id"],
        chunk_index=0,
        specialist_id="speaker_turn",
        status="error",
        result=None,
        receipt=over_budget,
        error_code="provider_response_invalid",
        error_message="Invalid provider response",
    )

    stored = pilot.store.load_job(job["job_id"])
    stored_call = stored["chunks"][0]["calls"][0]
    assert stored["status"] == "needs_attention"
    assert stored_call["error_code"] == "provider_cost_bound_exceeded"
    assert stored_call["receipt"]["cost_usd"] == "0.5"


def test_ambiguous_cost_breach_remains_ambiguous(service):
    pilot, _ = service
    _, source = _source(pilot)
    job = _job(pilot, source)
    assert pilot.store.claim_job(job["job_id"])
    preflight_id = pilot.store.set_preflight(
        job["job_id"], _preflight_payload(pilot, job)
    )
    pilot.store.begin_call(
        job["job_id"], 0, "speaker_turn", "0" * 64, preflight_id
    )
    _, receipt = FakeLunaClient("synthetic").call_specialist(
        SPECIALIST_SPECS[0],
        pilot.store.load_job(job["job_id"])["chunks"][0]["lines"],
    )
    over_budget = receipt.model_dump(mode="json")
    over_budget["cost_usd"] = "0.5"

    pilot.store.complete_call(
        job_id=job["job_id"],
        chunk_index=0,
        specialist_id="speaker_turn",
        status="ambiguous",
        result=None,
        receipt=over_budget,
        error_code="provider_outcome_ambiguous",
        error_message="Uncertain provider outcome",
    )

    stored = pilot.store.load_job(job["job_id"])
    stored_call = stored["chunks"][0]["calls"][0]
    assert stored["status"] == "needs_attention"
    assert stored["error_code"] == "provider_outcome_ambiguous"
    assert stored_call["status"] == "ambiguous"
    assert stored_call["error_code"] == "provider_cost_bound_exceeded"
    assert stored_call["receipt"]["cost_usd"] == "0.5"


def test_duplicate_provider_generation_cannot_enter_review(service):
    pilot, _ = service
    _, source = _source(pilot)
    job = _job(pilot, source)
    assert pilot.store.claim_job(job["job_id"])
    preflight_id = pilot.store.set_preflight(
        job["job_id"], _preflight_payload(pilot, job)
    )
    chunk_lines = pilot.store.load_job(job["job_id"])["chunks"][0]["lines"]

    first_result, first_receipt = FakeLunaClient("synthetic").call_specialist(
        SPECIALIST_SPECS[0], chunk_lines
    )
    pilot.store.begin_call(
        job["job_id"], 0, "speaker_turn", "0" * 64, preflight_id
    )
    pilot.store.complete_call(
        job_id=job["job_id"],
        chunk_index=0,
        specialist_id="speaker_turn",
        status="valid",
        result=first_result.model_dump(mode="json"),
        receipt=first_receipt.model_dump(mode="json"),
    )

    second_result, second_receipt = FakeLunaClient("synthetic").call_specialist(
        SPECIALIST_SPECS[1], chunk_lines
    )
    assert second_receipt.generation_id == first_receipt.generation_id
    pilot.store.begin_call(
        job["job_id"], 0, "timing_pause", "1" * 64, preflight_id
    )
    pilot.store.complete_call(
        job_id=job["job_id"],
        chunk_index=0,
        specialist_id="timing_pause",
        status="valid",
        result=second_result.model_dump(mode="json"),
        receipt=second_receipt.model_dump(mode="json"),
    )

    stored = pilot.store.load_job(job["job_id"])
    second_call = stored["chunks"][0]["calls"][1]
    assert stored["status"] == "needs_attention"
    assert second_call["status"] == "error"
    assert second_call["error_code"] == "provider_generation_reused"


def test_gateway_500_is_ambiguous_and_preserves_returned_usage(monkeypatch):
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(
        500,
        request=request,
        json={
            "id": "gen-uncertain",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
                "completion_tokens_details": {"reasoning_tokens": 0},
                "cost": "0.0001",
            },
        },
    )
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: response)
    client = LunaTranscriptClient(classification="synthetic")
    with pytest.raises(PilotProviderAmbiguousError) as caught:
        client._post_json("https://example.invalid", headers={}, payload={})
    assert caught.value.receipt is not None
    assert caught.value.receipt.generation_id == "gen-uncertain"
    assert caught.value.receipt.cost_usd == "0.0001"


def test_restart_reconciles_a_publishing_commit(service, monkeypatch):
    pilot, _ = service
    _, source = _source(pilot)
    job = _job(pilot, source)
    pilot.execute_job(job["job_id"])
    for proposal in pilot.store.load_job(job["job_id"])["proposals"]:
        if proposal["changed"]:
            pilot.save_decision(
                job_id=job["job_id"],
                proposal_id=proposal["proposal_id"],
                researcher_id="res_professor",
                action="accept",
                edited_text="",
                expected_decision_version=0,
            )

    record_import = pilot.evidence_catalog.record_import

    def fail_publication(record):
        raise RuntimeError("simulated catalog interruption")

    monkeypatch.setattr(pilot.evidence_catalog, "record_import", fail_publication)
    with pytest.raises(TranscriptPilotError, match="safe commit recovery"):
        pilot.commit(
            job_id=job["job_id"],
            researcher_id="res_professor",
            expected_active_revision_id=source["active_revision_id"],
        )
    interrupted = pilot.store.load_job(job["job_id"])
    assert interrupted["status"] == "committing"
    assert interrupted["commit"]["status"] == "publishing"

    monkeypatch.setattr(pilot.evidence_catalog, "record_import", record_import)
    TranscriptPilotRuntime(pilot).recover()
    recovered = pilot.store.load_job(job["job_id"])
    assert recovered["status"] == "committed"
    assert recovered["commit"]["status"] == "completed"


def test_http_contract_runs_supervised_synthetic_flow_without_remote_calls(
    tmp_path: Path,
    monkeypatch,
):
    pilot = TranscriptPilotService(
        tmp_path,
        client_factory=lambda classification: FakeLunaClient(classification),
    )

    class StubRuntime:
        def recover(self):
            return None

        def submit(self, job_id: str):
            return None

    monkeypatch.setattr(pilot_api, "_service", lambda: pilot)
    monkeypatch.setattr(pilot_api, "_runtime", lambda: StubRuntime())

    with TestClient(app) as client:
        study_response = client.post(
            "/api/transcript-pilot/studies",
            json={
                "name": "HTTP pilot",
                "description": "Synthetic supervised route test",
                "researcher_id": "res_http",
                "researcher_name": "HTTP Researcher",
                "study_id": "http-pilot",
            },
        )
        assert study_response.status_code == 201
        study = study_response.json()["study"]

        source_response = client.post(
            f"/api/transcript-pilot/studies/{study['study_id']}/sources",
            files={
                "file": (
                    "synthetic.txt",
                    SAMPLE_TRANSCRIPT.encode("utf-8"),
                    "text/plain",
                )
            },
            data={
                "researcher_id": "res_http",
                "data_classification": "synthetic",
                "authorization_basis": "Synthetic HTTP route fixture",
                "remote_egress_authorized": "true",
                "contains_direct_identifiers": "false",
                "protocol_version": PROTOCOL_VERSION,
            },
        )
        assert source_response.status_code == 201
        source = source_response.json()["source"]
        assert source["chunk_count"] == 1
        assert source["planned_call_count"] == 4

        job_response = client.post(
            f"/api/transcript-pilot/sources/{source['source_id']}/jobs",
            json={
                "researcher_id": "res_http",
                "input_revision_id": source["active_revision_id"],
                "idempotency_key": "http-job-key-0001",
                "authorized_cost_usd": "0.50",
                "confirmation": "authorize-four-specialists-per-chunk",
            },
        )
        assert job_response.status_code == 202
        job = job_response.json()["job"]
        pilot.execute_job(job["job_id"])

        review_response = client.get(f"/api/transcript-pilot/jobs/{job['job_id']}")
        assert review_response.status_code == 200
        review = review_response.json()["job"]
        assert review["status"] == "needs_review"
        assert any(
            event["event_type"] == "transcript.job.ready_for_review"
            for event in review["audit_events"]
        )
        for proposal in review["proposals"]:
            if proposal["changed"]:
                decision_response = client.put(
                    (
                        f"/api/transcript-pilot/jobs/{job['job_id']}/proposals/"
                        f"{proposal['proposal_id']}/decision"
                    ),
                    json={
                        "researcher_id": "res_http",
                        "action": "accept",
                        "edited_text": "",
                        "expected_decision_version": 0,
                    },
                )
                assert decision_response.status_code == 200

        commit_response = client.post(
            f"/api/transcript-pilot/jobs/{job['job_id']}/commit",
            json={
                "researcher_id": "res_http",
                "expected_active_revision_id": source["active_revision_id"],
                "confirmation": "create-supervised-child-revision",
            },
        )
        assert commit_response.status_code == 200
        committed = commit_response.json()["job"]
        assert committed["status"] == "committed"
        assert client.get(
            f"/api/transcript-pilot/jobs/{job['job_id']}/exports/transcript.txt"
        ).status_code == 200
        receipt = client.get(
            f"/api/transcript-pilot/jobs/{job['job_id']}/exports/receipt.json"
        )
        assert receipt.status_code == 200
        assert receipt.json()["job"]["usage"]["accounting_complete"] is True

        restore_response = client.post(
            f"/api/transcript-pilot/sources/{source['source_id']}/restore-original",
            json={
                "researcher_id": "res_http",
                "expected_active_revision_id": committed["committed_revision_id"],
                "confirmation": "restore-immutable-original",
            },
        )
        assert restore_response.status_code == 200
        assert restore_response.json()["source"]["active_revision_id"] == (
            source["original_revision_id"]
        )
