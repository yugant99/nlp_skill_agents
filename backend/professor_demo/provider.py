from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.llm.openrouter import is_openrouter_configured


MODEL_ID = "openai/gpt-5.6-luna"
CANONICAL_MODEL_PREFIX = "openai/gpt-5.6-luna"
ENDPOINT_TAG = "azure/eu"
PROVIDER_NAME = "Azure"
MAX_COMPLETION_TOKENS = 800
DISABLED_PLUGIN_IDS = ("web", "response-healing", "context-compression")
PROMPT_TOKEN_CEILING = 8_000
COST_CEILING_USD = Decimal("0.25")
CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_ENDPOINTS_URL = (
    "https://openrouter.ai/api/v1/models/openai/gpt-5.6-luna/endpoints"
)
ZDR_ENDPOINTS_URL = "https://openrouter.ai/api/v1/endpoints/zdr"
KEY_STATUS_URL = "https://openrouter.ai/api/v1/key"

SpecialistId = Literal[
    "speaker_turn",
    "timing_pause",
    "repair_overlap",
    "redaction_nonverbal",
]


class StrictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SpeakerLine(StrictOutputModel):
    line_index: int
    speaker: Literal["Interviewer", "Participant", "Unknown"]


class SpeakerTurnResult(StrictOutputModel):
    specialist_id: Literal["speaker_turn"]
    lines: list[SpeakerLine]


class TimingLine(StrictOutputModel):
    line_index: int
    timestamp: str
    pause: Literal["none", "short", "long"]

    @field_validator("timestamp")
    @classmethod
    def _timestamp_is_bounded(cls, value: str) -> str:
        if not re.fullmatch(r"(?:\d{2}:\d{2}(?::\d{2})?|unknown)", value):
            raise ValueError("timestamp must be an explicit cue or unknown")
        return value


class TimingPauseResult(StrictOutputModel):
    specialist_id: Literal["timing_pause"]
    lines: list[TimingLine]


class RepairLine(StrictOutputModel):
    line_index: int
    cleaned_text: str

    @field_validator("cleaned_text")
    @classmethod
    def _cleaned_text_is_bounded(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 500:
            raise ValueError("cleaned_text must contain 1 to 500 characters")
        return value


class RepairOverlapResult(StrictOutputModel):
    specialist_id: Literal["repair_overlap"]
    lines: list[RepairLine]


class RedactionSpan(StrictOutputModel):
    source_text: str
    replacement: Literal[
        "[PERSON]",
        "[EMAIL]",
        "[PHONE]",
        "[LOCATION]",
        "[ID]",
    ]

    @field_validator("source_text")
    @classmethod
    def _source_text_is_bounded(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 120:
            raise ValueError("source_text must contain 1 to 120 characters")
        return value


class RedactionLine(StrictOutputModel):
    line_index: int
    redactions: list[RedactionSpan]
    nonverbal_cues: list[str]

    @field_validator("nonverbal_cues")
    @classmethod
    def _cues_are_bounded(cls, values: list[str]) -> list[str]:
        if len(values) > 8:
            raise ValueError("too many nonverbal cues")
        normalized = []
        for value in values:
            cue = value.strip()
            if not cue or len(cue) > 80:
                raise ValueError("nonverbal cue must contain 1 to 80 characters")
            normalized.append(cue)
        return normalized


class RedactionNonverbalResult(StrictOutputModel):
    specialist_id: Literal["redaction_nonverbal"]
    lines: list[RedactionLine]


SpecialistResult = (
    SpeakerTurnResult
    | TimingPauseResult
    | RepairOverlapResult
    | RedactionNonverbalResult
)


@dataclass(frozen=True)
class SpecialistSpec:
    specialist_id: SpecialistId
    label: str
    result_model: type[StrictOutputModel]
    instruction: str


SPECIALIST_SPECS: tuple[SpecialistSpec, ...] = (
    SpecialistSpec(
        specialist_id="speaker_turn",
        label="Speaker turns",
        result_model=SpeakerTurnResult,
        instruction=(
            "Normalize each line's speaker as Interviewer, Participant, or Unknown. "
            "Use transcript evidence only and return exactly one item per line."
        ),
    ),
    SpecialistSpec(
        specialist_id="timing_pause",
        label="Timing and pauses",
        result_model=TimingPauseResult,
        instruction=(
            "Copy each explicit timestamp without inventing one; use unknown if absent. "
            "Classify explicit pause evidence as none, short, or long, and return exactly "
            "one item per line."
        ),
    ),
    SpecialistSpec(
        specialist_id="repair_overlap",
        label="Repairs and overlap",
        result_model=RepairOverlapResult,
        instruction=(
            "Produce the spoken content for each line after removing timestamp, speaker, "
            "pause, and nonverbal markup. Resolve obvious filler and false starts while "
            "preserving names, numbers, and meaning exactly. Return one item per line."
        ),
    ),
    SpecialistSpec(
        specialist_id="redaction_nonverbal",
        label="Redaction and nonverbals",
        result_model=RedactionNonverbalResult,
        instruction=(
            "Identify direct personal identifiers and explicit nonverbal cues per line. "
            "Each source_text must be copied exactly from the spoken content. Do not "
            "redact ordinary times or durations. Do not list pauses as nonverbal cues, "
            "and return cue text without surrounding brackets or parentheses. Return "
            "one item per line, using empty arrays when nothing applies."
        ),
    ),
)


class ProviderCallReceipt(BaseModel):
    model_requested: str
    endpoint_requested: str
    generation_id: str | None
    model_returned: str | None
    provider_returned: str | None
    router_attempt_count: int | None
    cache_hit: bool | None
    router_pipeline_stages: list[str] = Field(default_factory=list)
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    cost_usd: str | None
    accounting_complete: bool
    latency_ms: int


class PreflightReceipt(BaseModel):
    model: str
    endpoint: str
    provider: str
    endpoint_is_zdr: bool
    required_parameters_supported: bool
    metadata_request_count: int
    max_prompt_tokens_per_call: int
    max_completion_tokens_per_call: int
    request_price_per_call_usd: str
    prompt_price_per_token_usd: str
    completion_price_per_token_usd: str
    max_cost_per_call_usd: str
    estimated_max_cost_usd: str
    cost_ceiling_usd: str
    checked_at: str


class LunaProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        receipt: ProviderCallReceipt | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.receipt = receipt


class LunaDemoClient:
    def __init__(self, *, timeout_seconds: float = 75.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._provider_max_price: dict[str, float] = {
            "prompt": 0.0,
            "completion": 0.0,
            "request": 0.0,
        }
        self._price_caps_verified = False

    def preflight(self, transcript_lines: list[str]) -> PreflightReceipt:
        self._price_caps_verified = False
        api_key = _api_key_from_env()
        auth_headers = {"Authorization": f"Bearer {api_key}"}
        self._get_json(KEY_STATUS_URL, headers=auth_headers)
        endpoint_payload = self._get_json(
            MODEL_ENDPOINTS_URL,
            headers=auth_headers,
        )
        zdr_payload = self._get_json(
            ZDR_ENDPOINTS_URL,
            headers=auth_headers,
        )

        endpoints = endpoint_payload.get("data", {}).get("endpoints", [])
        if not isinstance(endpoints, list):
            raise LunaProviderError(
                "preflight_metadata_invalid",
                "OpenRouter endpoint metadata was unavailable",
            )
        endpoint = next(
            (
                item
                for item in endpoints
                if isinstance(item, dict) and item.get("tag") == ENDPOINT_TAG
            ),
            None,
        )
        if endpoint is None or endpoint.get("provider_name") != PROVIDER_NAME:
            raise LunaProviderError(
                "preflight_endpoint_unavailable",
                "The pinned Luna endpoint is unavailable",
            )

        required_parameters = {
            "max_completion_tokens",
            "reasoning",
            "include_reasoning",
            "response_format",
            "structured_outputs",
        }
        supported = endpoint.get("supported_parameters")
        if not isinstance(supported, list) or not required_parameters.issubset(
            {str(item) for item in supported}
        ):
            raise LunaProviderError(
                "preflight_parameters_unsupported",
                "The pinned Luna endpoint cannot honor the demo contract",
            )

        zdr_rows = zdr_payload.get("data", [])
        endpoint_is_zdr = isinstance(zdr_rows, list) and any(
            isinstance(item, dict)
            and item.get("model_id") == MODEL_ID
            and item.get("tag") == ENDPOINT_TAG
            for item in zdr_rows
        )
        if not endpoint_is_zdr:
            raise LunaProviderError(
                "preflight_zdr_unavailable",
                "The pinned Luna endpoint is not currently marked ZDR",
            )

        pricing = endpoint.get("pricing")
        if not isinstance(pricing, dict):
            raise LunaProviderError(
                "preflight_pricing_unavailable",
                "Luna pricing metadata was unavailable",
            )
        prompt_price = _decimal_value(pricing.get("prompt"))
        completion_price = _decimal_value(pricing.get("completion"))
        request_price = _decimal_value(pricing.get("request", "0"))
        if prompt_price < 0 or completion_price < 0 or request_price < 0:
            raise LunaProviderError(
                "preflight_pricing_invalid",
                "Luna pricing metadata was invalid",
            )
        if request_price != 0:
            raise LunaProviderError(
                "preflight_pricing_unsupported",
                "The pinned endpoint has an unsupported per-request charge",
            )
        self._provider_max_price = {
            "prompt": float(prompt_price * Decimal("1000000")),
            "completion": float(completion_price * Decimal("1000000")),
            "request": float(request_price),
        }

        for spec in SPECIALIST_SPECS:
            payload = self.build_payload(spec, transcript_lines)
            request_bytes = len(
                json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(
                    "utf-8"
                )
            )
            if request_bytes > PROMPT_TOKEN_CEILING:
                raise LunaProviderError(
                    "preflight_input_too_large",
                    "The synthetic transcript exceeds the bounded demo prompt",
                )

        max_cost_per_call = (
            request_price
            +
            Decimal(PROMPT_TOKEN_CEILING) * prompt_price
            + Decimal(MAX_COMPLETION_TOKENS) * completion_price
        )
        estimated_max = Decimal(len(SPECIALIST_SPECS)) * max_cost_per_call
        if estimated_max >= COST_CEILING_USD:
            raise LunaProviderError(
                "preflight_cost_too_high",
                "The estimated demo cost exceeds the configured ceiling",
            )

        self._price_caps_verified = True

        return PreflightReceipt(
            model=MODEL_ID,
            endpoint=ENDPOINT_TAG,
            provider=PROVIDER_NAME,
            endpoint_is_zdr=True,
            required_parameters_supported=True,
            metadata_request_count=3,
            max_prompt_tokens_per_call=PROMPT_TOKEN_CEILING,
            max_completion_tokens_per_call=MAX_COMPLETION_TOKENS,
            request_price_per_call_usd=_decimal_text(request_price),
            prompt_price_per_token_usd=_decimal_text(prompt_price),
            completion_price_per_token_usd=_decimal_text(completion_price),
            max_cost_per_call_usd=_decimal_text(max_cost_per_call),
            estimated_max_cost_usd=_decimal_text(estimated_max),
            cost_ceiling_usd=_decimal_text(COST_CEILING_USD),
            checked_at=datetime.now(UTC).isoformat(),
        )

    def call_specialist(
        self,
        spec: SpecialistSpec,
        transcript_lines: list[str],
    ) -> tuple[SpecialistResult, ProviderCallReceipt]:
        if not self._price_caps_verified:
            raise LunaProviderError(
                "preflight_required",
                "A current price preflight is required before Luna inference",
            )
        api_key = _api_key_from_env()
        payload = self.build_payload(spec, transcript_lines)
        started = time.monotonic()
        try:
            response_payload = self._post_json(
                CHAT_COMPLETIONS_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": os.environ.get(
                        "OPENROUTER_SITE_URL", "http://127.0.0.1:5173"
                    ),
                    "X-Title": "NLP Skill Agents - Professor Demo",
                    "X-OpenRouter-Metadata": "enabled",
                    "X-OpenRouter-Cache": "false",
                },
                payload=payload,
            )
        except LunaProviderError:
            raise
        except Exception as exc:  # Defensive boundary around the remote provider.
            raise LunaProviderError(
                "provider_request_failed",
                "The Luna specialist request failed",
            ) from exc

        latency_ms = max(0, round((time.monotonic() - started) * 1000))
        receipt = _receipt_from_response(response_payload, latency_ms=latency_ms)
        try:
            _validate_response_envelope(response_payload, receipt)
            content = response_payload["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("missing response content")
            result = spec.result_model.model_validate_json(content, strict=True)
            if result.specialist_id != spec.specialist_id:
                raise ValueError("specialist identity mismatch")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LunaProviderError(
                "provider_response_invalid",
                f"{spec.label} returned an invalid strict result",
                receipt=receipt,
            ) from exc
        return result, receipt

    def build_payload(
        self,
        spec: SpecialistSpec,
        transcript_lines: list[str],
    ) -> dict[str, Any]:
        numbered_transcript = "\n".join(
            f"{index} | {line}" for index, line in enumerate(transcript_lines)
        )
        return {
            "model": MODEL_ID,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are one bounded transcript specialist in a synthetic-only "
                        "classroom demo. Do only the assigned task. Never add facts, "
                        "commentary, markdown, or fields outside the strict JSON schema."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Task: {spec.instruction}\n"
                        f"Expected line indexes: 0 through {len(transcript_lines) - 1}.\n"
                        "Synthetic transcript:\n"
                        f"{numbered_transcript}"
                    ),
                },
            ],
            "max_completion_tokens": MAX_COMPLETION_TOKENS,
            "reasoning": {"effort": "none", "exclude": True},
            "plugins": [
                {"id": plugin_id, "enabled": False}
                for plugin_id in DISABLED_PLUGIN_IDS
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": f"{spec.specialist_id}_result",
                    "strict": True,
                    "schema": spec.result_model.model_json_schema(),
                },
            },
            "provider": {
                "only": [ENDPOINT_TAG],
                "order": [ENDPOINT_TAG],
                "allow_fallbacks": False,
                "require_parameters": True,
                "data_collection": "deny",
                "zdr": True,
                "max_price": dict(self._provider_max_price),
            },
        }

    def _get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            response = httpx.get(
                url,
                headers=headers,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise LunaProviderError(
                "preflight_request_failed",
                "OpenRouter preflight failed",
            ) from exc
        if not isinstance(payload, dict):
            raise LunaProviderError(
                "preflight_metadata_invalid",
                "OpenRouter preflight returned invalid metadata",
            )
        return payload

    def _post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            response = httpx.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            )
            response.raise_for_status()
            response_payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise LunaProviderError(
                "provider_request_failed",
                "The Luna specialist request failed",
            ) from exc
        if not isinstance(response_payload, dict):
            raise LunaProviderError(
                "provider_response_invalid",
                "The Luna specialist returned an invalid response",
            )
        return response_payload


def _api_key_from_env() -> str:
    if not is_openrouter_configured():
        raise LunaProviderError(
            "provider_not_configured",
            "OPENROUTER_API_KEY is not configured",
        )
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise LunaProviderError(
            "provider_not_configured",
            "OPENROUTER_API_KEY is not configured",
        )
    return api_key


def _receipt_from_response(
    payload: dict[str, Any],
    *,
    latency_ms: int,
) -> ProviderCallReceipt:
    usage = payload.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    prompt_tokens = _strict_optional_int(usage.get("prompt_tokens"))
    completion_tokens = _strict_optional_int(usage.get("completion_tokens"))
    total_tokens = _strict_optional_int(usage.get("total_tokens"))
    details = usage.get("completion_tokens_details")
    details = details if isinstance(details, dict) else {}
    reasoning_tokens = _strict_optional_int(details.get("reasoning_tokens"))
    cost = _optional_decimal(usage.get("cost"))

    choices = payload.get("choices")
    first_choice = choices[0] if isinstance(choices, list) and choices else {}
    first_choice = first_choice if isinstance(first_choice, dict) else {}
    metadata_value = payload.get("openrouter_metadata")
    metadata_present = isinstance(metadata_value, dict)
    metadata = metadata_value if metadata_present else {}
    attempts = metadata.get("attempts")
    attempt_number = _strict_optional_int(metadata.get("attempt"))
    routing_identity_valid = (
        metadata.get("requested") == MODEL_ID
        and metadata.get("strategy") == "direct"
    )
    attempt_provider: str | None = None
    attempt_model: str | None = None
    attempt_identity_valid = attempts is None
    if isinstance(attempts, list):
        attempt = attempts[0] if len(attempts) == 1 else None
        if isinstance(attempt, dict):
            attempt_provider = (
                attempt.get("provider")
                if isinstance(attempt.get("provider"), str)
                else None
            )
            attempt_model = (
                attempt.get("model")
                if isinstance(attempt.get("model"), str)
                else None
            )
            attempt_identity_valid = (
                attempt_provider == PROVIDER_NAME
                and attempt_model is not None
                and is_canonical_luna_model(attempt_model)
                and attempt.get("status") == 200
            )
    router_attempt_count = 1 if attempt_number == 1 else None
    if not routing_identity_valid or not attempt_identity_valid:
        router_attempt_count = None

    endpoints = metadata.get("endpoints")
    endpoints = endpoints if isinstance(endpoints, dict) else {}
    available = endpoints.get("available")
    available = available if isinstance(available, list) else []
    selected = [
        item
        for item in available
        if isinstance(item, dict) and item.get("selected") is True
    ]
    selected_provider = (
        selected[0].get("provider")
        if len(selected) == 1 and isinstance(selected[0].get("provider"), str)
        else None
    )
    selected_model = (
        selected[0].get("model")
        if len(selected) == 1 and isinstance(selected[0].get("model"), str)
        else None
    )
    response_model = payload.get("model")
    provider_returned = (
        selected_provider
        if routing_identity_valid
        and attempt_identity_valid
        and (attempt_provider is None or attempt_provider == selected_provider)
        and (attempt_model is None or attempt_model == selected_model)
        and selected_model is not None
        and is_canonical_luna_model(selected_model)
        and isinstance(response_model, str)
        and is_canonical_luna_model(response_model)
        and response_model == selected_model
        else None
    )
    cached_value = payload.get("cached")
    cache_hit = (
        True
        if cached_value is True
        else False
        if metadata_present
        else None
    )
    pipeline = metadata.get("pipeline", [])
    if not isinstance(pipeline, list):
        router_pipeline_stages = ["invalid"]
    else:
        router_pipeline_stages = []
        for stage in pipeline:
            if not isinstance(stage, dict):
                router_pipeline_stages.append("invalid")
                continue
            stage_type = stage.get("type")
            stage_name = stage.get("name")
            if not isinstance(stage_type, str) or not isinstance(stage_name, str):
                router_pipeline_stages.append("invalid")
                continue
            router_pipeline_stages.append(f"{stage_type}:{stage_name}")

    accounting_complete = (
        prompt_tokens is not None
        and completion_tokens is not None
        and total_tokens is not None
        and cost is not None
    )
    return ProviderCallReceipt(
        model_requested=MODEL_ID,
        endpoint_requested=ENDPOINT_TAG,
        generation_id=(payload.get("id") if isinstance(payload.get("id"), str) else None),
        model_returned=(response_model if isinstance(response_model, str) else None),
        provider_returned=provider_returned,
        router_attempt_count=router_attempt_count,
        cache_hit=cache_hit,
        router_pipeline_stages=router_pipeline_stages,
        finish_reason=(
            first_choice.get("finish_reason")
            if isinstance(first_choice.get("finish_reason"), str)
            else None
        ),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        cost_usd=_decimal_text(cost) if cost is not None else None,
        accounting_complete=accounting_complete,
        latency_ms=latency_ms,
    )


def _validate_response_envelope(
    payload: dict[str, Any],
    receipt: ProviderCallReceipt,
) -> None:
    if receipt.finish_reason != "stop":
        raise ValueError("completion did not finish cleanly")
    if not receipt.generation_id:
        raise ValueError("provider generation identity is missing")
    if receipt.model_returned is None or not is_canonical_luna_model(
        receipt.model_returned
    ):
        raise ValueError("returned model does not match Luna")
    if receipt.provider_returned != PROVIDER_NAME:
        raise ValueError("returned provider does not match the pin")
    if receipt.cache_hit is not False:
        raise ValueError("cached response is not a fresh specialist call")
    if receipt.router_pipeline_stages:
        raise ValueError("router pipeline altered the bounded specialist request")
    if receipt.router_attempt_count != 1:
        raise ValueError("router attempt provenance is incomplete")
    if not receipt.accounting_complete:
        raise ValueError("native usage accounting is incomplete")
    if receipt.reasoning_tokens != 0:
        raise ValueError("reasoning usage violates the bounded request")
    if (
        receipt.prompt_tokens is None
        or receipt.prompt_tokens > PROMPT_TOKEN_CEILING
        or receipt.completion_tokens is None
        or receipt.completion_tokens > MAX_COMPLETION_TOKENS
        or receipt.total_tokens != receipt.prompt_tokens + receipt.completion_tokens
    ):
        raise ValueError("native token accounting violates the bounded request")
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("expected exactly one completion choice")


def is_canonical_luna_model(value: str) -> bool:
    return bool(
        re.fullmatch(
            rf"{re.escape(MODEL_ID)}(?:-[0-9]{{8}})?",
            value,
        )
    )


def _strict_optional_int(value: Any) -> int | None:
    if type(value) is int and value >= 0:
        return value
    return None


def _decimal_value(value: Any) -> Decimal:
    parsed = _optional_decimal(value)
    if parsed is None:
        raise LunaProviderError(
            "preflight_pricing_invalid",
            "Luna pricing metadata was invalid",
        )
    return parsed


def _optional_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return parsed


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")
