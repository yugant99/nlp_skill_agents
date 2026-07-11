from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.analysis.pipeline import execute_analysis
from backend.analysis.skill_packs import parse_skill_pack
from backend.analysis.transcripts import StudyConfig
from backend.storage.audit_log import AuditLogStore
from backend.storage.atomic import atomic_text_writer, atomic_write_text


MAX_STUDY_PARTICIPANTS = 10_000


@dataclass(frozen=True)
class StudyWorkspace:
    id: str
    name: str
    description: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class StudySkillPackVersion:
    study_id: str
    version_id: str
    payload: dict[str, Any]
    artifact_path: Path
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class StudySchema:
    study_id: str
    participant_count: int
    participants: list[str]
    conditions: list[str]
    week_count: int
    weeks: list[str]
    custom_fields: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class StudyBatchRun:
    study_id: str
    batch_id: str
    skill_pack_version_id: str
    run_count: int
    failure_count: int
    aggregate_dir: Path
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class StudyBundleExport:
    study_id: str
    bundle_id: str
    bundle_dir: Path
    manifest_path: Path
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class StudyWorkspaceStore:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.studies_dir = self.root / "studies"
        self.audit_log = AuditLogStore(self.root)

    def create_study(self, payload: dict[str, Any]) -> StudyWorkspace:
        name = _required_string(payload, "name")
        study = StudyWorkspace(
            id=_slugify(str(payload.get("id") or name)),
            name=name,
            description=str(payload.get("description") or ""),
        )
        study_dir = self._study_dir(study.id)
        study_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            study_dir / "study.json",
            json.dumps(asdict(study), indent=2),
        )
        self.audit_log.record(
            "study.created",
            "study",
            study.id,
            {"name": study.name},
        )
        return study

    def list_studies(self) -> list[StudyWorkspace]:
        if not self.studies_dir.exists():
            return []
        studies = [
            StudyWorkspace(**json.loads(path.read_text(encoding="utf-8")))
            for path in self.studies_dir.glob("*/study.json")
        ]
        return sorted(studies, key=lambda study: study.created_at, reverse=True)

    def save_study_schema(self, study_id: str, payload: dict[str, Any]) -> StudySchema:
        self._require_study(study_id)
        schema = _study_schema_from_payload(study_id, payload)
        atomic_write_text(
            self._study_dir(study_id) / "study_schema.json",
            json.dumps(asdict(schema), indent=2),
        )
        self.audit_log.record(
            "study.schema.updated",
            "study",
            study_id,
            {
                "participant_count": schema.participant_count,
                "conditions": schema.conditions,
                "week_count": schema.week_count,
                "custom_fields": schema.custom_fields,
            },
        )
        return schema

    def load_study_schema(self, study_id: str) -> StudySchema:
        self._require_study(study_id)
        path = self._study_dir(study_id) / "study_schema.json"
        if not path.exists():
            raise FileNotFoundError("study_schema.json")
        return StudySchema(**json.loads(path.read_text(encoding="utf-8")))

    def add_skill_pack_version(
        self,
        study_id: str,
        payload: dict[str, Any],
        *,
        validate: bool = True,
    ) -> StudySkillPackVersion:
        self._require_study(study_id)
        if validate:
            parse_skill_pack(payload)
        version_id = _skill_pack_version_id(payload)
        version_dir = self._study_dir(study_id) / "skill_packs"
        version_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = version_dir / f"{version_id}.json"
        atomic_write_text(artifact_path, json.dumps(payload, indent=2))
        metadata = StudySkillPackVersion(
            study_id=study_id,
            version_id=version_id,
            payload=payload,
            artifact_path=artifact_path,
        )
        atomic_write_text(
            version_dir / f"{version_id}.metadata.json",
            json.dumps(
                {
                    "study_id": metadata.study_id,
                    "version_id": metadata.version_id,
                    "artifact_path": str(metadata.artifact_path),
                    "created_at": metadata.created_at,
                },
                indent=2,
            ),
        )
        self.audit_log.record(
            "skill_pack.versioned",
            "study",
            study_id,
            {
                "version_id": version_id,
                "skill_pack_id": str(payload["id"]),
                "skill_pack_version": str(payload["version"]),
            },
        )
        return metadata

    def run_text_batch(
        self,
        study_id: str,
        skill_pack_version_id: str,
        transcripts: list[dict[str, Any]],
    ) -> StudyBatchRun:
        self._require_study(study_id)
        skill_pack_payload = self._load_skill_pack_version(
            study_id,
            skill_pack_version_id,
        )
        batch_id = f"batch_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"
        aggregate_dir = self._study_dir(study_id) / "batches" / batch_id
        runs_dir = aggregate_dir / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)

        successes = []
        failures = []
        for item in transcripts:
            source_filename = _required_string(item, "source_filename")
            metadata = _normalized_metadata(item.get("metadata", {}))
            try:
                run = execute_analysis(
                    _required_string(item, "content"),
                    _study_config_for_batch_item(skill_pack_payload, metadata),
                    source_filename=source_filename,
                )
            except (ValueError, KeyError) as exc:
                failures.append(
                    {
                        "source_filename": source_filename,
                        "error": str(exc),
                    }
                )
                continue

            run_payload = {
                "run_id": run.run_id,
                "source_id": run.source_id,
                "transcript_sha256": run.transcript_sha256,
                "transcript_revision_id": run.transcript_revision_id,
                "source_filename": run.source_filename,
                "metadata": metadata,
                "created_at": run.created_at,
                "turn_count": len(run.transcript.turns),
                "turns": [asdict(turn) for turn in run.transcript.turns],
                "results": [asdict(result) for result in run.results],
            }
            atomic_write_text(
                runs_dir / f"{run.run_id}.json",
                json.dumps(run_payload, indent=2),
            )
            successes.append(run_payload)

        aggregate_payload = _aggregate_batch_payload(
            study_id,
            batch_id,
            skill_pack_version_id,
            self._load_optional_study_schema(study_id),
            successes,
            failures,
        )
        atomic_write_text(
            aggregate_dir / "aggregate_results.json",
            json.dumps(aggregate_payload, indent=2),
        )
        for result in aggregate_payload["results"]:
            _write_csv(aggregate_dir / f"{result['metric_id']}.csv", result["rows"])

        batch = StudyBatchRun(
            study_id=study_id,
            batch_id=batch_id,
            skill_pack_version_id=skill_pack_version_id,
            run_count=len(successes),
            failure_count=len(failures),
            aggregate_dir=aggregate_dir,
        )
        atomic_write_text(
            aggregate_dir / "batch.json",
            json.dumps(
                {
                    **asdict(batch),
                    "aggregate_dir": str(batch.aggregate_dir),
                },
                indent=2,
            ),
        )
        self.audit_log.record(
            "batch.completed",
            "study",
            study_id,
            {
                "batch_id": batch.batch_id,
                "skill_pack_version_id": skill_pack_version_id,
                "run_count": batch.run_count,
                "failure_count": batch.failure_count,
            },
        )
        return batch

    def list_batches(self, study_id: str) -> list[StudyBatchRun]:
        self._require_study(study_id)
        batches_dir = self._study_dir(study_id) / "batches"
        if not batches_dir.exists():
            return []
        batches = [
            _batch_run_from_payload(json.loads(path.read_text(encoding="utf-8")))
            for path in batches_dir.glob("*/batch.json")
        ]
        return sorted(batches, key=lambda batch: batch.created_at, reverse=True)

    def load_batch(self, study_id: str, batch_id: str) -> StudyBatchRun:
        self._require_study(study_id)
        batch_path = self._study_dir(study_id) / "batches" / batch_id / "batch.json"
        if not batch_path.exists():
            raise FileNotFoundError(batch_id)
        return _batch_run_from_payload(json.loads(batch_path.read_text(encoding="utf-8")))

    def list_batch_runs(self, study_id: str, batch_id: str) -> list[dict[str, Any]]:
        batch = self.load_batch(study_id, batch_id)
        runs_dir = batch.aggregate_dir / "runs"
        if not runs_dir.exists():
            return []
        runs = [
            _batch_run_summary(json.loads(path.read_text(encoding="utf-8")))
            for path in runs_dir.glob("*.json")
        ]
        return sorted(runs, key=lambda run: str(run["source_filename"]))

    def load_batch_run(self, study_id: str, batch_id: str, run_id: str) -> dict[str, Any]:
        batch = self.load_batch(study_id, batch_id)
        run_path = batch.aggregate_dir / "runs" / f"{run_id}.json"
        if not run_path.exists():
            raise FileNotFoundError(run_id)
        return json.loads(run_path.read_text(encoding="utf-8"))

    def export_study_bundle(self, study_id: str) -> StudyBundleExport:
        self._require_study(study_id)
        bundle_id = f"{study_id}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        bundle_dir = self.root / "bundles" / bundle_id
        bundle_dir.mkdir(parents=True, exist_ok=True)
        study_dir = self._study_dir(study_id)
        study_payload = json.loads((study_dir / "study.json").read_text(encoding="utf-8"))
        manifest = {
            "bundle_id": bundle_id,
            "study": study_payload,
            "created_at": datetime.now(UTC).isoformat(),
            "files": [
                _file_record(path, self.root)
                for path in sorted(study_dir.rglob("*"))
                if path.is_file()
            ],
        }
        manifest_path = bundle_dir / "manifest.json"
        atomic_write_text(manifest_path, json.dumps(manifest, indent=2))
        bundle = StudyBundleExport(
            study_id=study_id,
            bundle_id=bundle_id,
            bundle_dir=bundle_dir,
            manifest_path=manifest_path,
        )
        self.audit_log.record(
            "bundle.exported",
            "study",
            study_id,
            {
                "bundle_id": bundle.bundle_id,
                "manifest_path": str(bundle.manifest_path),
            },
        )
        return bundle

    def _study_dir(self, study_id: str) -> Path:
        return self.studies_dir / study_id

    def _require_study(self, study_id: str) -> None:
        if not (self._study_dir(study_id) / "study.json").exists():
            raise FileNotFoundError(study_id)

    def _load_skill_pack_version(
        self,
        study_id: str,
        skill_pack_version_id: str,
    ) -> dict[str, Any]:
        path = self._study_dir(study_id) / "skill_packs" / f"{skill_pack_version_id}.json"
        if not path.exists():
            raise FileNotFoundError(skill_pack_version_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_optional_study_schema(self, study_id: str) -> StudySchema | None:
        path = self._study_dir(study_id) / "study_schema.json"
        if not path.exists():
            return None
        return StudySchema(**json.loads(path.read_text(encoding="utf-8")))


def _aggregate_batch_payload(
    study_id: str,
    batch_id: str,
    skill_pack_version_id: str,
    study_schema: StudySchema | None,
    runs: list[dict[str, Any]],
    failures: list[dict[str, str]],
) -> dict[str, Any]:
    results_by_metric: dict[str, dict[str, Any]] = {}
    for run in runs:
        for result in run["results"]:
            aggregate = results_by_metric.setdefault(
                result["metric_id"],
                {
                    "metric_id": result["metric_id"],
                    "label": result["label"],
                    "rows": [],
                },
            )
            aggregate["rows"].extend(
                {
                    **_ordered_metadata(run.get("metadata", {})),
                    "source_filename": run["source_filename"],
                    "run_id": run["run_id"],
                    **row,
                }
                for row in result["rows"]
            )
    return {
        "study_id": study_id,
        "batch_id": batch_id,
        "skill_pack_version_id": skill_pack_version_id,
        "study_schema": asdict(study_schema) if study_schema else None,
        "created_at": datetime.now(UTC).isoformat(),
        "run_count": len(runs),
        "failure_count": len(failures),
        "failures": failures,
        "results": list(results_by_metric.values()),
    }


def _batch_run_from_payload(payload: dict[str, Any]) -> StudyBatchRun:
    return StudyBatchRun(
        study_id=str(payload["study_id"]),
        batch_id=str(payload["batch_id"]),
        skill_pack_version_id=str(payload["skill_pack_version_id"]),
        run_count=int(payload["run_count"]),
        failure_count=int(payload["failure_count"]),
        aggregate_dir=Path(str(payload["aggregate_dir"])),
        created_at=str(payload["created_at"]),
    )


def _batch_run_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": payload["run_id"],
        "source_id": str(payload.get("source_id") or ""),
        "transcript_sha256": str(
            payload.get("transcript_sha256") or payload.get("source_sha256") or ""
        ),
        "transcript_revision_id": str(payload.get("transcript_revision_id") or ""),
        "source_filename": payload["source_filename"],
        "metadata": payload.get("metadata", {}),
        "created_at": payload["created_at"],
        "turn_count": payload["turn_count"],
        "metric_ids": [result["metric_id"] for result in payload.get("results", [])],
    }


def _study_config_from_skill_pack_payload(payload: dict[str, Any]) -> StudyConfig:
    pack = parse_skill_pack(payload)
    return StudyConfig(
        participant_id="",
        speaker_prefixes=pack.speaker_prefixes,
        speaker_labels=pack.speaker_roles,
        selected_metrics=[metric.id for metric in pack.metrics],
        disfluency_tokens=pack.disfluency_tokens,
        concept_lexicons=pack.concept_lexicons,
        nonverbal_cues=pack.nonverbal_cues,
        skill_pack_id=pack.id,
        skill_pack_name=pack.name,
        skill_pack_version=pack.version,
    )


def _study_config_for_batch_item(
    skill_pack_payload: dict[str, Any],
    metadata: dict[str, str],
) -> StudyConfig:
    config = _study_config_from_skill_pack_payload(skill_pack_payload)
    participant_id = metadata.get("participant_id", "").strip()
    if not participant_id:
        return config
    return replace(config, participant_id=participant_id)


def _skill_pack_version_id(payload: dict[str, Any]) -> str:
    return f"{_identifier(str(payload['id']))}-{_identifier(str(payload['version']))}"


def _identifier(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return slug or f"study-{uuid4().hex[:8]}"


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _normalized_metadata(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    metadata: dict[str, str] = {}
    for key, value in payload.items():
        normalized_key = str(key).strip()
        normalized_value = str(value).strip()
        if normalized_key and normalized_value:
            metadata[normalized_key] = normalized_value
    return metadata


def _study_schema_from_payload(study_id: str, payload: dict[str, Any]) -> StudySchema:
    participant_count = _bounded_positive_int(
        payload.get("participant_count"),
        1,
        MAX_STUDY_PARTICIPANTS,
    )
    week_count = _bounded_positive_int(payload.get("week_count"), 1, 52)
    conditions = _normalized_string_list(payload.get("conditions")) or ["home", "lab"]
    custom_fields = _normalized_string_list(payload.get("custom_fields"))
    return StudySchema(
        study_id=study_id,
        participant_count=participant_count,
        participants=[f"P{index + 1}" for index in range(participant_count)],
        conditions=conditions,
        week_count=week_count,
        weeks=[f"week_{index + 1}" for index in range(week_count)],
        custom_fields=custom_fields,
    )


def _bounded_positive_int(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, 1), maximum)


def _normalized_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    normalized: list[str] = []
    for item in raw_items:
        text = str(item).strip().lower()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _ordered_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    normalized = _normalized_metadata(metadata)
    ordered: dict[str, str] = {}
    for key in ["participant_id", "condition", "week"]:
        if key in normalized:
            ordered[key] = normalized[key]
    for key in sorted(normalized):
        if key not in ordered:
            ordered[key] = normalized[key]
    return ordered


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = _ordered_fieldnames(rows)
    with atomic_text_writer(path, newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _ordered_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    return fieldnames


def _file_record(path: Path, root: Path) -> dict[str, str | int]:
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }
