from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest

from backend.professor_demo.provider import (
    ENDPOINT_TAG,
    MAX_COMPLETION_TOKENS,
    MODEL_ID,
    SPECIALIST_SPECS,
    LunaDemoClient,
    LunaProviderError,
    PreflightReceipt,
    ProviderCallReceipt,
    RedactionLine,
    RedactionNonverbalResult,
    RedactionSpan,
    RepairLine,
    RepairOverlapResult,
    SpeakerLine,
    SpeakerTurnResult,
    TimingLine,
    TimingPauseResult,
)
from backend.professor_demo.service import (
    ProfessorDemoError,
    ProfessorDemoService,
    merge_specialist_results,
)


TRANSCRIPT = """[00:00] Q: What happened?
[00:05] A: Um, Maya Patel waited. [door closes] (long pause)"""
LINES = TRANSCRIPT.splitlines()


def test_payload_hard_pins_the_four_call_contract() -> None:
    client = LunaDemoClient()

    for spec in SPECIALIST_SPECS:
        payload = client.build_payload(spec, LINES)

        assert payload["model"] == MODEL_ID
        assert payload["max_completion_tokens"] == MAX_COMPLETION_TOKENS
        assert payload["reasoning"] == {"effort": "none", "exclude": True}
        assert payload["provider"] == {
            "only": [ENDPOINT_TAG],
            "order": [ENDPOINT_TAG],
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "deny",
            "zdr": True,
        }
        response_format = payload["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["strict"] is True
        _assert_object_schemas_forbid_extra_fields(
            response_format["json_schema"]["schema"]
        )


def test_provider_accepts_only_plain_strict_json_and_does_not_retry(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("backend.llm.openrouter._DOTENV_LOADED", True)
    client = LunaDemoClient()
    calls: list[dict] = []
    content = '{"specialist_id":"speaker_turn","lines":[' \
        '{"line_index":0,"speaker":"Interviewer"},' \
        '{"line_index":1,"speaker":"Participant"}]}'

    def fake_post(url, *, headers, payload):
        calls.append(payload)
        return _provider_response(content)

    monkeypatch.setattr(client, "_post_json", fake_post)
    result, receipt = client.call_specialist(SPECIALIST_SPECS[0], LINES)

    assert result.specialist_id == "speaker_turn"
    assert receipt.accounting_complete is True
    assert len(calls) == 1

    calls.clear()

    def fake_fenced_post(url, *, headers, payload):
        calls.append(payload)
        return _provider_response(f"```json\n{content}\n```")

    monkeypatch.setattr(client, "_post_json", fake_fenced_post)
    with pytest.raises(LunaProviderError, match="invalid strict result"):
        client.call_specialist(SPECIALIST_SPECS[0], LINES)

    assert len(calls) == 1


def test_cost_preflight_aborts_before_any_completion_call(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("backend.llm.openrouter._DOTENV_LOADED", True)
    client = LunaDemoClient()
    completion_calls = []

    def fake_get(url, *, headers=None):
        if url.endswith("/key"):
            return {"data": {"label": "demo"}}
        if url.endswith("/endpoints/zdr"):
            return {"data": [{"model_id": MODEL_ID, "tag": ENDPOINT_TAG}]}
        return {
            "data": {
                "endpoints": [
                    {
                        "tag": ENDPOINT_TAG,
                        "provider_name": "Azure",
                        "supported_parameters": [
                            "max_completion_tokens",
                            "reasoning",
                            "include_reasoning",
                            "response_format",
                            "structured_outputs",
                        ],
                        "pricing": {"prompt": "0.1", "completion": "0.1"},
                    }
                ]
            }
        }

    monkeypatch.setattr(client, "_get_json", fake_get)
    monkeypatch.setattr(
        client,
        "_post_json",
        lambda *args, **kwargs: completion_calls.append((args, kwargs)),
    )

    with pytest.raises(LunaProviderError, match="cost exceeds"):
        client.preflight(LINES)

    assert completion_calls == []


def test_service_makes_exactly_four_calls_then_accepts_and_reverts(tmp_path) -> None:
    client = FakeLunaClient()
    service = ProfessorDemoService(tmp_path, client=client)

    run = service.run(TRANSCRIPT)

    assert client.calls == [spec.specialist_id for spec in SPECIALIST_SPECS]
    assert run.status == "completed"
    assert run.receipt.attempted_call_count == 4
    assert run.receipt.completed_call_count == 4
    assert run.receipt.valid_result_count == 4
    assert run.receipt.total_cost_usd == "0.004"
    assert run.revision_state.active_revision_number == 0
    assert run.revision_state.candidate is not None
    assert "[PERSON]" in (run.merged_transcript or "")
    original_digest = run.revision_state.original.sha256

    accepted = service.store.accept(run.run_id)
    assert accepted.revision_state.active_revision_number == 1
    assert accepted.revision_state.original.sha256 == original_digest
    assert service.store.accept(run.run_id) == accepted
    assert service.store.latest().run_id == run.run_id

    reverted = service.store.revert(run.run_id)
    assert reverted.revision_state.active_revision_number == 0
    assert reverted.revision_state.original.sha256 == original_digest
    assert reverted.revision_state.candidate == accepted.revision_state.candidate


def test_one_invalid_specialist_still_attempts_four_and_blocks_acceptance(tmp_path) -> None:
    client = FakeLunaClient(fail_specialist="timing_pause")
    service = ProfessorDemoService(tmp_path, client=client)

    run = service.run(TRANSCRIPT)

    assert client.calls == [spec.specialist_id for spec in SPECIALIST_SPECS]
    assert run.status == "failed"
    assert run.receipt.attempted_call_count == 4
    assert run.receipt.valid_result_count == 3
    assert run.merged_transcript is None
    assert run.revision_state.candidate is None
    with pytest.raises(ProfessorDemoError, match="completed four-specialist"):
        service.store.accept(run.run_id)


def test_local_merge_is_canonical_when_result_mapping_order_changes() -> None:
    results = _valid_results()
    reverse_results = dict(reversed(list(results.items())))

    assert merge_specialist_results(LINES, results) == merge_specialist_results(
        LINES,
        reverse_results,
    )


class FakeLunaClient:
    def __init__(self, *, fail_specialist: str | None = None) -> None:
        self.fail_specialist = fail_specialist
        self.calls: list[str] = []

    def preflight(self, transcript_lines: list[str]) -> PreflightReceipt:
        assert transcript_lines == LINES
        return _preflight_receipt()

    def call_specialist(self, spec, transcript_lines):
        assert transcript_lines == LINES
        self.calls.append(spec.specialist_id)
        if spec.specialist_id == self.fail_specialist:
            raise LunaProviderError(
                "provider_response_invalid",
                f"{spec.label} returned an invalid strict result",
                receipt=_call_receipt(spec.specialist_id),
            )
        return _valid_results()[spec.specialist_id], _call_receipt(spec.specialist_id)


def _valid_results():
    return {
        "speaker_turn": SpeakerTurnResult(
            specialist_id="speaker_turn",
            lines=[
                SpeakerLine(line_index=0, speaker="Interviewer"),
                SpeakerLine(line_index=1, speaker="Participant"),
            ],
        ),
        "timing_pause": TimingPauseResult(
            specialist_id="timing_pause",
            lines=[
                TimingLine(line_index=0, timestamp="00:00", pause="none"),
                TimingLine(line_index=1, timestamp="00:05", pause="long"),
            ],
        ),
        "repair_overlap": RepairOverlapResult(
            specialist_id="repair_overlap",
            lines=[
                RepairLine(line_index=0, cleaned_text="What happened?"),
                RepairLine(line_index=1, cleaned_text="Maya Patel waited."),
            ],
        ),
        "redaction_nonverbal": RedactionNonverbalResult(
            specialist_id="redaction_nonverbal",
            lines=[
                RedactionLine(line_index=0, redactions=[], nonverbal_cues=[]),
                RedactionLine(
                    line_index=1,
                    redactions=[
                        RedactionSpan(
                            source_text="Maya Patel",
                            replacement="[PERSON]",
                        )
                    ],
                    nonverbal_cues=["door closes"],
                ),
            ],
        ),
    }


def _provider_response(content: str) -> dict:
    return {
        "id": "gen_demo",
        "model": "openai/gpt-5.6-luna-20260709",
        "provider": "Azure",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cost": 0.001,
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
        "openrouter_metadata": {
            "provider_attempts": [{"provider": "Azure"}],
            "cache_hit": False,
        },
    }


def _call_receipt(specialist_id: str) -> ProviderCallReceipt:
    return ProviderCallReceipt(
        model_requested=MODEL_ID,
        endpoint_requested=ENDPOINT_TAG,
        generation_id=f"gen_{specialist_id}",
        model_returned="openai/gpt-5.6-luna-20260709",
        provider_returned="Azure",
        router_attempt_count=1,
        cache_hit=False,
        finish_reason="stop",
        prompt_tokens=10,
        completion_tokens=5,
        reasoning_tokens=0,
        total_tokens=15,
        cost_usd="0.001",
        accounting_complete=True,
        latency_ms=25,
    )


def _preflight_receipt() -> PreflightReceipt:
    return PreflightReceipt(
        model=MODEL_ID,
        endpoint=ENDPOINT_TAG,
        provider="Azure",
        endpoint_is_zdr=True,
        required_parameters_supported=True,
        metadata_request_count=3,
        max_prompt_tokens_per_call=8_000,
        max_completion_tokens_per_call=MAX_COMPLETION_TOKENS,
        prompt_price_per_token_usd="0.00000022",
        completion_price_per_token_usd="0.00000132",
        estimated_max_cost_usd="0.011264",
        cost_ceiling_usd="0.25",
        checked_at="2026-08-23T00:00:00+00:00",
    )


def _assert_object_schemas_forbid_extra_fields(schema: dict) -> None:
    stack = [deepcopy(schema)]
    object_count = 0
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if current.get("type") == "object":
                object_count += 1
                assert current.get("additionalProperties") is False
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    assert object_count >= 2
