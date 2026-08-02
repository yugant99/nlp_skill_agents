import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

import backend.qualitative.research_reviews as research_reviews_module
from backend.qualitative.research_reviews import (
    ResearchReviewService,
    ReviewConflictError,
    ReviewNotFoundError,
    ReviewValidationError,
)
from tests.test_qualitative_coding_references import (
    OWNER_ID,
    SECOND_ID,
    _create_fixture,
    _create_reference,
)


NEW_REVIEWER_ID = "res_review_registered"


def _suggestion_arguments(fixture, suffix: str, **overrides):
    values = {
        "agent_suggestion_id": f"ags_{suffix * 32}",
        "origin_kind": "synthetic_fixture",
        "origin_id": f"fixture:{suffix}",
        "origin_suggestion_key": f"candidate:{suffix}",
        "researcher_id": OWNER_ID,
        "project_source_id": fixture.project_source_id,
        "transcript_revision_id": fixture.transcript_revision_id,
        "evidence_set_id": fixture.evidence_set_id,
        "target_kind": "passage",
        "passage_id": fixture.passage_id,
        "cunit_id": "",
        "start_offset": 2,
        "end_offset": 8,
        "codebook_version_id": fixture.codebook_version_id,
        "code_id": fixture.code_id,
    }
    values.update(overrides)
    return values


def _coding_count(fixture) -> int:
    with sqlite3.connect(fixture.database.db_path) as connection:
        return int(connection.execute("select count(*) from coding_references").fetchone()[0])


def test_researcher_registration_provenance_retry_and_bound_pagination(tmp_path):
    fixture = _create_fixture(tmp_path)
    service = ResearchReviewService(tmp_path, fixture.project_id)

    bootstrap = service.read_researcher(OWNER_ID)
    assert bootstrap.provenance_classification == "bootstrap"
    assert bootstrap.provenance_actor_id == OWNER_ID
    assert service.read_researcher(SECOND_ID).provenance_classification == "legacy_unverified"

    registered = service.create_researcher(
        researcher_id=NEW_REVIEWER_ID,
        actor_id=OWNER_ID,
        display_name="  Registered Reviewer  ",
        role="reviewer",
    )
    assert registered.display_name == "Registered Reviewer"
    assert registered.provenance_classification == "registered"
    assert registered.provenance_actor_id == OWNER_ID
    assert service.create_researcher(
        researcher_id=NEW_REVIEWER_ID,
        actor_id=OWNER_ID,
        display_name="Registered Reviewer",
        role="reviewer",
    ) == registered

    first = service.list_researchers(limit=1)
    assert len(first.researchers) == 1
    assert first.next_cursor is not None
    second = service.list_researchers(limit=1, cursor=first.next_cursor)
    assert second.researchers
    with pytest.raises(ReviewValidationError):
        service.list_researchers(role="reviewer", cursor=first.next_cursor)
    with pytest.raises(ReviewConflictError):
        service.create_researcher(
            researcher_id=NEW_REVIEWER_ID,
            actor_id=OWNER_ID,
            display_name="Different",
            role="reviewer",
        )


def test_suggestion_three_stage_retry_collision_and_external_validation(tmp_path):
    fixture = _create_fixture(tmp_path)
    service = ResearchReviewService(tmp_path, fixture.project_id)
    arguments = _suggestion_arguments(fixture, "a")

    created = service.create_agent_suggestion(**arguments)
    assert created.review_status == "unreviewed"
    assert created.current_decision is None
    assert service.create_agent_suggestion(**arguments) == created

    with pytest.raises(ReviewConflictError):
        service.create_agent_suggestion(
            **{
                **arguments,
                "origin_id": "fixture:changed",
                "passage_id": f"psg_{'f' * 32}",
            }
        )
    with pytest.raises(ReviewNotFoundError):
        service.create_agent_suggestion(
            **_suggestion_arguments(
                fixture,
                "b",
                passage_id=f"psg_{'f' * 32}",
            )
        )
    with pytest.raises(ReviewValidationError):
        service.create_agent_suggestion(
            **_suggestion_arguments(fixture, "c", start_offset=True)
        )


def test_decision_acceptance_exact_retry_and_no_coding_mutation(tmp_path):
    fixture = _create_fixture(tmp_path)
    service = ResearchReviewService(tmp_path, fixture.project_id)
    suggestion = service.create_agent_suggestion(
        **_suggestion_arguments(fixture, "d")
    )
    reference = _create_reference(fixture)
    count_before = _coding_count(fixture)

    accepted = service.append_reviewer_decision(
        reviewer_decision_id=f"rvd_{'d' * 32}",
        agent_suggestion_id=suggestion.suggestion.agent_suggestion_id,
        researcher_id=OWNER_ID,
        expected_decision_number=0,
        decision="accepted",
        coding_reference_id=reference.coding_reference_id,
    )
    assert accepted.decision_number == 1
    assert service.append_reviewer_decision(
        reviewer_decision_id=accepted.reviewer_decision_id,
        agent_suggestion_id=suggestion.suggestion.agent_suggestion_id,
        researcher_id=OWNER_ID,
        expected_decision_number=0,
        decision="accepted",
        coding_reference_id=reference.coding_reference_id,
    ) == accepted
    assert service.read_agent_suggestion(
        suggestion.suggestion.agent_suggestion_id
    ).review_status == "accepted"
    service.validate_project_state()
    assert _coding_count(fixture) == count_before
    with pytest.raises(ReviewConflictError):
        service.append_reviewer_decision(
            reviewer_decision_id=f"rvd_{'e' * 32}",
            agent_suggestion_id=suggestion.suggestion.agent_suggestion_id,
            researcher_id=OWNER_ID,
            expected_decision_number=1,
            decision="rejected",
        )


def test_deferred_then_rejected_history_and_decision_cursor_binding(tmp_path):
    fixture = _create_fixture(tmp_path)
    service = ResearchReviewService(tmp_path, fixture.project_id)
    suggestion = service.create_agent_suggestion(
        **_suggestion_arguments(fixture, "e")
    ).suggestion
    deferred = service.append_reviewer_decision(
        reviewer_decision_id=f"rvd_{'a' * 32}",
        agent_suggestion_id=suggestion.agent_suggestion_id,
        researcher_id=OWNER_ID,
        expected_decision_number=0,
        decision="deferred",
    )
    rejected = service.append_reviewer_decision(
        reviewer_decision_id=f"rvd_{'b' * 32}",
        agent_suggestion_id=suggestion.agent_suggestion_id,
        researcher_id=OWNER_ID,
        expected_decision_number=1,
        decision="rejected",
    )
    assert (deferred.decision_number, rejected.decision_number) == (1, 2)
    first = service.list_reviewer_decisions(
        suggestion.agent_suggestion_id,
        limit=1,
    )
    assert first.reviewer_decisions == (deferred,)
    assert first.next_cursor is not None
    second = service.list_reviewer_decisions(
        suggestion.agent_suggestion_id,
        limit=1,
        cursor=first.next_cursor,
    )
    assert second.reviewer_decisions == (rejected,)
    other = service.create_agent_suggestion(
        **_suggestion_arguments(fixture, "f")
    ).suggestion
    with pytest.raises(ReviewValidationError):
        service.list_reviewer_decisions(
            other.agent_suggestion_id,
            cursor=first.next_cursor,
        )


def test_result_must_be_reviewer_owned_and_match_decision_semantics(tmp_path):
    fixture = _create_fixture(tmp_path)
    service = ResearchReviewService(tmp_path, fixture.project_id)
    suggestion = service.create_agent_suggestion(
        **_suggestion_arguments(fixture, "1")
    ).suggestion
    other_owned = _create_reference(fixture, researcher_id=SECOND_ID)
    with pytest.raises(ReviewConflictError):
        service.append_reviewer_decision(
            reviewer_decision_id=f"rvd_{'1' * 32}",
            agent_suggestion_id=suggestion.agent_suggestion_id,
            researcher_id=OWNER_ID,
            expected_decision_number=0,
            decision="accepted",
            coding_reference_id=other_owned.coding_reference_id,
        )
    with pytest.raises(ReviewValidationError):
        service.append_reviewer_decision(
            reviewer_decision_id=f"rvd_{'2' * 32}",
            agent_suggestion_id=suggestion.agent_suggestion_id,
            researcher_id=OWNER_ID,
            expected_decision_number=0,
            decision="rejected",
            coding_reference_id=other_owned.coding_reference_id,
        )


def test_duplicate_audit_is_rejected_and_audit_failure_rolls_back(tmp_path, monkeypatch):
    fixture = _create_fixture(tmp_path)
    service = ResearchReviewService(tmp_path, fixture.project_id)
    created = service.create_agent_suggestion(
        **_suggestion_arguments(fixture, "2")
    ).suggestion
    with sqlite3.connect(fixture.database.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        connection.execute(
            """
            insert into qualitative_audit_events (
              event_id, project_id, actor_id, event_type, subject_type,
              subject_id, metadata_json, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"qae_{'f' * 32}",
                fixture.project_id,
                OWNER_ID,
                "qualitative.agent_suggestion.created",
                "agent_suggestion",
                created.agent_suggestion_id,
                '{"origin_kind":"synthetic_fixture"}',
                created.created_at,
            ),
        )
    with pytest.raises(ReviewConflictError):
        service.read_agent_suggestion(created.agent_suggestion_id)

    fresh = _create_fixture(tmp_path / "fresh")
    fresh_service = ResearchReviewService(tmp_path / "fresh", fresh.project_id)

    def fail_audit(*args, **kwargs):
        raise sqlite3.OperationalError("injected")

    monkeypatch.setattr(fresh_service, "_append_audit", fail_audit)
    with pytest.raises(ReviewConflictError):
        fresh_service.create_researcher(
            researcher_id=NEW_REVIEWER_ID,
            actor_id=OWNER_ID,
            display_name="Rollback Reviewer",
            role="reviewer",
        )
    with pytest.raises(ReviewNotFoundError):
        fresh_service.read_researcher(NEW_REVIEWER_ID)


@pytest.mark.parametrize(
    ("subject_type", "event_type", "subject_id"),
    (
        (
            sqlite3.Binary(b"agent_suggestion"),
            sqlite3.Binary(b"qualitative.agent_suggestion.created"),
            f"ags_{'8' * 32}",
        ),
        (
            " agent_suggestion ",
            " QUALITATIVE.AGENT_SUGGESTION.CREATED ",
            f" ags_{'9' * 32} ",
        ),
    ),
)
def test_binary_or_padded_unmatched_suggestion_audit_is_rejected(
    tmp_path,
    subject_type,
    event_type,
    subject_id,
):
    fixture = _create_fixture(tmp_path)
    service = ResearchReviewService(tmp_path, fixture.project_id)
    with sqlite3.connect(fixture.database.db_path) as connection:
        connection.execute("pragma foreign_keys = on")
        connection.execute(
            """
            insert into qualitative_audit_events (
              event_id, project_id, actor_id, event_type, subject_type,
              subject_id, metadata_json, created_at
            ) values (?, ?, ?, ?, ?, ?, '{}', ?)
            """,
            (
                f"qae_{'7' * 32}",
                fixture.project_id,
                OWNER_ID,
                event_type,
                subject_type,
                subject_id,
                service.read_researcher(OWNER_ID).created_at,
            ),
        )
    with pytest.raises(ReviewConflictError):
        service.validate_project_state()


def test_concurrent_exact_decision_append_is_one_atomic_result(tmp_path):
    fixture = _create_fixture(tmp_path)
    service = ResearchReviewService(tmp_path, fixture.project_id)
    suggestion = service.create_agent_suggestion(
        **_suggestion_arguments(fixture, "3")
    ).suggestion
    reference = _create_reference(fixture)
    request = {
        "reviewer_decision_id": f"rvd_{'3' * 32}",
        "agent_suggestion_id": suggestion.agent_suggestion_id,
        "researcher_id": OWNER_ID,
        "expected_decision_number": 0,
        "decision": "accepted",
        "coding_reference_id": reference.coding_reference_id,
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: service.append_reviewer_decision(**request), range(2)))
    assert results[0] == results[1]
    assert service.list_reviewer_decisions(
        suggestion.agent_suggestion_id
    ).reviewer_decisions == (results[0],)


def test_review_operations_never_overlap_study_and_workspace_locks_or_call_provider(
    tmp_path,
    monkeypatch,
):
    fixture = _create_fixture(tmp_path)
    service = ResearchReviewService(tmp_path, fixture.project_id)
    state = {"study": 0, "workspace": 0}
    real_read = service.database.read
    real_transaction = service.database.transaction
    real_workspace_lock = research_reviews_module.workspace_mutation_lock

    @contextmanager
    def tracked_read():
        assert state["workspace"] == 0
        state["study"] += 1
        try:
            with real_read() as connection:
                yield connection
        finally:
            state["study"] -= 1

    @contextmanager
    def tracked_transaction():
        assert state["workspace"] == 0
        state["study"] += 1
        try:
            with real_transaction() as connection:
                yield connection
        finally:
            state["study"] -= 1

    @contextmanager
    def tracked_workspace_lock(root):
        assert state["study"] == 0
        state["workspace"] += 1
        try:
            with real_workspace_lock(root):
                yield
        finally:
            state["workspace"] -= 1

    def forbidden_provider(*_args, **_kwargs):
        raise AssertionError("review service attempted a provider call")

    monkeypatch.setattr(service.database, "read", tracked_read)
    monkeypatch.setattr(service.database, "transaction", tracked_transaction)
    monkeypatch.setattr(
        research_reviews_module,
        "workspace_mutation_lock",
        tracked_workspace_lock,
    )
    monkeypatch.setattr(
        "backend.llm.openrouter.complete_json",
        forbidden_provider,
    )
    service.create_agent_suggestion(**_suggestion_arguments(fixture, "4"))
    service.validate_project_state()
    assert state == {"study": 0, "workspace": 0}
