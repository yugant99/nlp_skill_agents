from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.storage.audit_log import AuditLogStore


@dataclass(frozen=True)
class LibraryEntry:
    id: str
    version: str
    entry_type: str
    artifact_path: Path
    approved_by: str
    notes: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class LibraryStore:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.library_dir = self.root / "library"
        self.skill_packs_dir = self.library_dir / "skill_packs"
        self.metric_plugins_dir = self.library_dir / "metric_plugins"
        self.manifest_path = self.library_dir / "library_manifest.json"
        self.audit_log = AuditLogStore(self.root)

    def approve_skill_pack(
        self,
        payload: dict[str, Any],
        *,
        reviewer: str,
        notes: str = "",
    ) -> LibraryEntry:
        return self._approve(
            payload,
            entry_type="skill_pack",
            artifact_dir=self.skill_packs_dir,
            reviewer=reviewer,
            notes=notes,
        )

    def approve_metric_plugin(
        self,
        payload: dict[str, Any],
        *,
        reviewer: str,
        notes: str = "",
    ) -> LibraryEntry:
        return self._approve(
            payload,
            entry_type="metric_plugin",
            artifact_dir=self.metric_plugins_dir,
            reviewer=reviewer,
            notes=notes,
        )

    def list_entries(self) -> list[LibraryEntry]:
        entries = []
        for metadata_path in self.library_dir.glob("*/*.metadata.json"):
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            entries.append(
                LibraryEntry(
                    id=payload["id"],
                    version=payload["version"],
                    entry_type=payload["entry_type"],
                    artifact_path=Path(payload["artifact_path"]),
                    approved_by=payload["approved_by"],
                    notes=payload["notes"],
                    created_at=payload["created_at"],
                )
            )
        return sorted(entries, key=lambda entry: entry.created_at, reverse=True)

    def _approve(
        self,
        payload: dict[str, Any],
        *,
        entry_type: str,
        artifact_dir: Path,
        reviewer: str,
        notes: str,
    ) -> LibraryEntry:
        entry_id = _required_string(payload, "id")
        version = _version(payload)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{_identifier(entry_id)}-{_identifier(version)}.json"
        artifact_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        entry = LibraryEntry(
            id=entry_id,
            version=version,
            entry_type=entry_type,
            artifact_path=artifact_path,
            approved_by=reviewer,
            notes=notes,
        )
        metadata_path = artifact_path.with_suffix(".metadata.json")
        metadata_path.write_text(
            json.dumps({**asdict(entry), "artifact_path": str(entry.artifact_path)}, indent=2),
            encoding="utf-8",
        )
        self._write_manifest()
        self.audit_log.record(
            f"library.{entry_type}.approved",
            entry_type,
            entry.id,
            {
                "version": entry.version,
                "approved_by": reviewer,
                "notes": notes,
                "artifact_path": str(artifact_path),
            },
        )
        return entry

    def _write_manifest(self) -> None:
        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(
                {
                    "created_at": datetime.now(UTC).isoformat(),
                    "entries": [
                        {
                            **asdict(entry),
                            "artifact_path": str(entry.artifact_path),
                        }
                        for entry in self.list_entries()
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _version(payload: dict[str, Any]) -> str:
    return str(payload.get("version") or "1.0.0")


def _identifier(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()
