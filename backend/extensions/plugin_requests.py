from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PluginRequestExample:
    transcript: str
    expected_behavior: str


@dataclass(frozen=True)
class PluginRequest:
    id: str
    title: str
    research_question: str
    requested_metric_id: str
    output_columns: list[str]
    examples: list[PluginRequestExample]
    status: str = "draft"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class StoredPluginRequest:
    request: PluginRequest
    artifact_path: Path


class PluginRequestStore:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.requests_dir = self.root / "plugin_requests"

    def persist(self, request: PluginRequest) -> StoredPluginRequest:
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = self.requests_dir / f"{request.id}.json"
        artifact_path.write_text(
            json.dumps(plugin_request_to_payload(request), indent=2),
            encoding="utf-8",
        )
        return StoredPluginRequest(request=request, artifact_path=artifact_path)

    def list_requests(self) -> list[PluginRequest]:
        if not self.requests_dir.exists():
            return []
        requests = [
            plugin_request_from_payload(json.loads(path.read_text(encoding="utf-8")))
            for path in self.requests_dir.glob("*.json")
        ]
        return sorted(requests, key=lambda request: request.created_at, reverse=True)


def create_plugin_request(
    payload: dict[str, Any],
    store: PluginRequestStore | None = None,
) -> PluginRequest:
    title = _required_string(payload, "title")
    research_question = _required_string(payload, "research_question")
    requested_metric_id = _slugify(
        str(payload.get("requested_metric_id") or title)
    )
    output_columns = _normalize_columns(payload.get("output_columns"))
    examples = _normalize_examples(payload)
    request = PluginRequest(
        id=requested_metric_id,
        title=title,
        research_question=research_question,
        requested_metric_id=requested_metric_id,
        output_columns=output_columns,
        examples=examples,
    )
    (store or PluginRequestStore()).persist(request)
    return request


def plugin_request_to_payload(request: PluginRequest) -> dict[str, Any]:
    return asdict(request)


def plugin_request_from_payload(payload: dict[str, Any]) -> PluginRequest:
    examples = [
        PluginRequestExample(
            transcript=str(example.get("transcript", "")),
            expected_behavior=str(example.get("expected_behavior", "")),
        )
        for example in payload.get("examples", [])
        if isinstance(example, dict)
    ]
    return PluginRequest(
        id=str(payload["id"]),
        title=str(payload["title"]),
        research_question=str(payload["research_question"]),
        requested_metric_id=str(payload["requested_metric_id"]),
        output_columns=[str(column) for column in payload.get("output_columns", [])],
        examples=examples,
        status=str(payload.get("status") or "draft"),
        created_at=str(payload.get("created_at") or datetime.now(UTC).isoformat()),
    )


def _required_string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"plugin request field '{field}' must be a non-empty string")
    return value.strip()


def _normalize_columns(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = value.split(",")
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = []
    columns = [_slugify(str(column)) for column in candidates if str(column).strip()]
    if not columns:
        raise ValueError("plugin request field 'output_columns' must include at least one column")
    return columns


def _normalize_examples(payload: dict[str, Any]) -> list[PluginRequestExample]:
    examples = payload.get("examples")
    if isinstance(examples, list) and examples:
        normalized = [
            PluginRequestExample(
                transcript=str(example.get("transcript", "")).strip(),
                expected_behavior=str(example.get("expected_behavior", "")).strip(),
            )
            for example in examples
            if isinstance(example, dict)
        ]
    else:
        normalized = [
            PluginRequestExample(
                transcript=str(payload.get("example_transcript", "")).strip(),
                expected_behavior=str(payload.get("expected_behavior", "")).strip(),
            )
        ]
    normalized = [
        example
        for example in normalized
        if example.transcript and example.expected_behavior
    ]
    if not normalized:
        raise ValueError("plugin request must include at least one synthetic example")
    return normalized


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "metric_plugin_request"
