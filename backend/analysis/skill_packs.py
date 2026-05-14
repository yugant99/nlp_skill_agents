from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SkillPackValidationError(ValueError):
    """Raised when a declarative skill pack is missing required or valid data."""


@dataclass(frozen=True)
class SkillPackMetric:
    id: str
    output_schema: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class SkillPack:
    id: str
    name: str
    version: str
    metrics: list[SkillPackMetric]
    description: str | None
    speaker_roles: dict[str, str]
    disfluency_tokens: list[str]
    raw: dict[str, Any]
    path: Path


def load_skill_pack(identifier: str | Path, skill_pack_dir: Path | None = None) -> SkillPack:
    path = _resolve_skill_pack_path(identifier, skill_pack_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SkillPackValidationError(f"Skill pack not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SkillPackValidationError(f"Skill pack must be valid JSON: {path}") from exc

    return parse_skill_pack(payload, path=path)


def parse_skill_pack(payload: Any, path: Path | None = None) -> SkillPack:
    if not isinstance(payload, dict):
        raise SkillPackValidationError("Skill pack JSON must be an object")

    missing_fields = [
        field for field in ("id", "name", "version", "metrics") if field not in payload
    ]
    if missing_fields:
        raise SkillPackValidationError(
            f"Skill pack missing required field(s): {', '.join(missing_fields)}"
        )

    pack_id = _required_string(payload, "id")
    name = _required_string(payload, "name")
    version = _required_string(payload, "version")
    metrics = _parse_metrics(payload["metrics"])
    _validate_registered_metric_ids(metrics)

    return SkillPack(
        id=pack_id,
        name=name,
        version=version,
        metrics=metrics,
        description=_optional_string(payload, "description"),
        speaker_roles=_string_dict(payload.get("speaker_roles", {}), "speaker_roles"),
        disfluency_tokens=_string_list(
            payload.get("disfluency_tokens", []), "disfluency_tokens"
        ),
        raw=dict(payload),
        path=path or Path(),
    )


def _resolve_skill_pack_path(
    identifier: str | Path, skill_pack_dir: Path | None = None
) -> Path:
    identifier_path = Path(identifier)
    if identifier_path.suffix == ".json" or len(identifier_path.parts) > 1:
        return identifier_path

    base_dir = skill_pack_dir or _default_skill_pack_dir()
    return base_dir / f"{identifier_path.name}.json"


def _default_skill_pack_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "study_skill_packs"


def _parse_metrics(value: Any) -> list[SkillPackMetric]:
    if not isinstance(value, list) or not value:
        raise SkillPackValidationError("Skill pack field 'metrics' must be a non-empty list")

    metrics = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            if not item:
                raise SkillPackValidationError(f"Metric at index {index} must not be empty")
            metrics.append(SkillPackMetric(id=item))
            continue

        if not isinstance(item, dict):
            raise SkillPackValidationError(
                f"Metric at index {index} must be a string id or object"
            )
        metric_id = _required_string(item, "id", context=f"metric at index {index}")
        output_schema = item.get("output_schema")
        if output_schema is not None and not isinstance(output_schema, dict):
            raise SkillPackValidationError(
                f"Metric '{metric_id}' field 'output_schema' must be an object"
            )
        metrics.append(
            SkillPackMetric(
                id=metric_id,
                output_schema=output_schema,
                raw=dict(item),
            )
        )
    return metrics


def _validate_registered_metric_ids(metrics: list[SkillPackMetric]) -> None:
    from backend.analysis.pipeline import METRIC_REGISTRY

    registered_metric_ids = set(METRIC_REGISTRY)
    unknown_metric_ids = [
        metric.id for metric in metrics if metric.id not in registered_metric_ids
    ]
    if unknown_metric_ids:
        raise SkillPackValidationError(
            "Skill pack references unknown metric id(s): "
            + ", ".join(unknown_metric_ids)
        )


def _required_string(payload: dict[str, Any], field: str, context: str = "skill pack") -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise SkillPackValidationError(f"{context} field '{field}' must be a non-empty string")
    return value


def _optional_string(payload: dict[str, Any], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SkillPackValidationError(f"Skill pack field '{field}' must be a string")
    return value


def _string_dict(value: Any, field: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise SkillPackValidationError(f"Skill pack field '{field}' must be an object")
    if not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        raise SkillPackValidationError(
            f"Skill pack field '{field}' must contain only string keys and values"
        )
    return dict(value)


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise SkillPackValidationError(f"Skill pack field '{field}' must be a list")
    if not all(isinstance(item, str) for item in value):
        raise SkillPackValidationError(
            f"Skill pack field '{field}' must contain only strings"
        )
    return list(value)
