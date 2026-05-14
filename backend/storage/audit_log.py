from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class AuditEvent:
    id: str
    event_type: str
    subject_type: str
    subject_id: str
    actor: str
    metadata: dict[str, Any]
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class AuditLogStore:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.audit_dir = self.root / "audit"
        self.events_path = self.audit_dir / "events.jsonl"

    def record(
        self,
        event_type: str,
        subject_type: str,
        subject_id: str,
        metadata: dict[str, Any] | None = None,
        *,
        actor: str = "local-system",
    ) -> AuditEvent:
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        event = AuditEvent(
            id=uuid4().hex,
            event_type=event_type,
            subject_type=subject_type,
            subject_id=subject_id,
            actor=actor,
            metadata=metadata or {},
        )
        with self.events_path.open("a", encoding="utf-8") as events_file:
            events_file.write(json.dumps(asdict(event)) + "\n")
        return event

    def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        events = [
            json.loads(line)
            for line in self.events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return events[-limit:]
