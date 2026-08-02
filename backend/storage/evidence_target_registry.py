from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from backend.evidence.identifiers import (
    cunit_evidence_id,
    evidence_set_id,
    passage_evidence_id,
    transcript_evidence_identity,
)
from backend.storage.evidence_catalog import (
    EvidenceCatalog,
    EvidenceCatalogConflict,
)
from backend.storage.evidence_text_blob_store import (
    EvidenceTextBlobIntegrityError,
    EvidenceTextBlobStore,
    evidence_text_sha256,
)
from backend.storage.sqlite_migrations import SchemaCompatibilityError
from backend.storage.workspace_lock import workspace_mutation_lock


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REVISION_ID_PATTERN = re.compile(r"^trv_[0-9a-f]{32}$")
_PASSAGE_ID_PATTERN = re.compile(r"^psg_[0-9a-f]{32}$")
_CUNIT_ID_PATTERN = re.compile(r"^cun_[0-9a-f]{32}$")
_EVIDENCE_SET_ID_PATTERN = re.compile(r"^evs_[0-9a-f]{32}$")
_PRODUCER_CONTRACTS = {
    ("analysis_turns", 1): ("verified", "not_applicable"),
    ("cunit_segmentation", 1): ("verified", "not_domain_validated"),
}
_MAX_IDENTIFIER_LENGTH = 256
_MAX_ROLE_LENGTH = 1024
_MAX_TARGETS_PER_SET = 100_000


class EvidenceTargetValidationError(ValueError):
    pass


class EvidenceTargetNotFoundError(LookupError):
    pass


class EvidenceTargetConflictError(RuntimeError):
    pass


class EvidenceTargetBlobConflict(EvidenceTargetConflictError):
    pass


@dataclass(frozen=True)
class EvidenceCUnitInput:
    cunit_id: str
    cunit_ordinal: int
    text: str


@dataclass(frozen=True)
class EvidencePassageInput:
    passage_id: str
    passage_ordinal: int
    role: str
    text: str
    cunits: tuple[EvidenceCUnitInput, ...] = ()


@dataclass(frozen=True)
class PreparedEvidenceCUnit:
    cunit_id: str
    cunit_ordinal: int
    text: str
    text_sha256: str
    text_length: int

    def to_manifest(self) -> dict[str, object]:
        return {
            "cunit_id": self.cunit_id,
            "cunit_ordinal": self.cunit_ordinal,
            "text_sha256": self.text_sha256,
            "text_length": self.text_length,
        }


@dataclass(frozen=True)
class PreparedEvidencePassage:
    passage_id: str
    passage_ordinal: int
    role: str
    text: str
    text_sha256: str
    text_length: int
    cunits: tuple[PreparedEvidenceCUnit, ...]

    def to_manifest(self) -> dict[str, object]:
        return {
            "passage_id": self.passage_id,
            "passage_ordinal": self.passage_ordinal,
            "role": self.role,
            "text_sha256": self.text_sha256,
            "text_length": self.text_length,
            "cunits": [cunit.to_manifest() for cunit in self.cunits],
        }


@dataclass(frozen=True)
class PreparedEvidenceSet:
    import_id: str
    workspace_id: str
    project_source_id: str
    transcript_revision_id: str
    transcript_text: str
    transcript_text_sha256: str
    producer_kind: str
    producer_version: int
    producer_status: str
    review_status: str
    passage_count: int
    cunit_count: int
    passages: tuple[PreparedEvidencePassage, ...]
    snapshot_sha256: str
    evidence_set_id: str

    def to_manifest(self) -> dict[str, object]:
        return {
            "format": "nlp-skill-agents.evidence-target-set",
            "format_version": 1,
            "import_id": self.import_id,
            "workspace_id": self.workspace_id,
            "project_source_id": self.project_source_id,
            "transcript_revision_id": self.transcript_revision_id,
            "transcript_text_sha256": self.transcript_text_sha256,
            "producer_kind": self.producer_kind,
            "producer_version": self.producer_version,
            "producer_status": self.producer_status,
            "review_status": self.review_status,
            "passage_count": self.passage_count,
            "cunit_count": self.cunit_count,
            "passages": [passage.to_manifest() for passage in self.passages],
        }


@dataclass(frozen=True)
class EvidenceCUnitSnapshot:
    cunit_id: str
    cunit_ordinal: int
    text_sha256: str
    text_length: int

    def to_manifest(self) -> dict[str, object]:
        return {
            "cunit_id": self.cunit_id,
            "cunit_ordinal": self.cunit_ordinal,
            "text_sha256": self.text_sha256,
            "text_length": self.text_length,
        }


@dataclass(frozen=True)
class EvidencePassageSnapshot:
    passage_id: str
    passage_ordinal: int
    role: str
    text_sha256: str
    text_length: int
    cunits: tuple[EvidenceCUnitSnapshot, ...]

    def to_manifest(self) -> dict[str, object]:
        return {
            "passage_id": self.passage_id,
            "passage_ordinal": self.passage_ordinal,
            "role": self.role,
            "text_sha256": self.text_sha256,
            "text_length": self.text_length,
            "cunits": [cunit.to_manifest() for cunit in self.cunits],
        }


@dataclass(frozen=True)
class EvidenceSetSnapshot:
    evidence_set_id: str
    import_id: str
    workspace_id: str
    project_source_id: str
    transcript_revision_id: str
    transcript_text_sha256: str
    producer_kind: str
    producer_version: int
    producer_status: str
    review_status: str
    passage_count: int
    cunit_count: int
    passages: tuple[EvidencePassageSnapshot, ...]
    snapshot_sha256: str
    created_at: str

    def to_manifest(self) -> dict[str, object]:
        return {
            "format": "nlp-skill-agents.evidence-target-set",
            "format_version": 1,
            "import_id": self.import_id,
            "workspace_id": self.workspace_id,
            "project_source_id": self.project_source_id,
            "transcript_revision_id": self.transcript_revision_id,
            "transcript_text_sha256": self.transcript_text_sha256,
            "producer_kind": self.producer_kind,
            "producer_version": self.producer_version,
            "producer_status": self.producer_status,
            "review_status": self.review_status,
            "passage_count": self.passage_count,
            "cunit_count": self.cunit_count,
            "passages": [passage.to_manifest() for passage in self.passages],
        }

    @property
    def text_blob_sha256s(self) -> tuple[str, ...]:
        digests = {self.transcript_text_sha256}
        for passage in self.passages:
            digests.add(passage.text_sha256)
            digests.update(cunit.text_sha256 for cunit in passage.cunits)
        return tuple(sorted(digests))


@dataclass(frozen=True)
class ResolvedEvidenceTarget:
    import_id: str
    workspace_id: str
    project_source_id: str
    transcript_revision_id: str
    evidence_set_id: str
    producer_kind: str
    producer_version: int
    producer_status: str
    review_status: str
    target_kind: str
    passage_id: str
    passage_ordinal: int
    cunit_id: str
    cunit_ordinal: int | None
    role: str
    text: str
    text_sha256: str
    text_length: int


class EvidenceTargetRegistry:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.catalog = EvidenceCatalog(self.root)
        self.text_blobs = EvidenceTextBlobStore(self.root)

    def prepare_complete_set(
        self,
        *,
        import_id: str,
        workspace_id: str,
        project_source_id: str,
        transcript_revision_id: str,
        transcript_text: str,
        producer_kind: str,
        producer_version: int,
        producer_status: str,
        review_status: str,
        passages: Sequence[EvidencePassageInput],
    ) -> PreparedEvidenceSet:
        return prepare_complete_evidence_set(
            import_id=import_id,
            workspace_id=workspace_id,
            project_source_id=project_source_id,
            transcript_revision_id=transcript_revision_id,
            transcript_text=transcript_text,
            producer_kind=producer_kind,
            producer_version=producer_version,
            producer_status=producer_status,
            review_status=review_status,
            passages=passages,
        )

    def register_complete_set(
        self,
        prepared: PreparedEvidenceSet,
    ) -> EvidenceSetSnapshot:
        canonical = _reprepare(prepared)
        if canonical != prepared:
            raise EvidenceTargetValidationError(
                "Prepared evidence set is not canonical"
        )
        try:
            with workspace_mutation_lock(self.root):
                with self.catalog.transaction() as connection:
                    created_at = self._require_import(connection, prepared)
                    existing = connection.execute(
                        """
                        select 1 from evidence_sets where evidence_set_id = ?
                        """,
                        (prepared.evidence_set_id,),
                    ).fetchone()
                    if existing is not None:
                        stored = self._load_snapshot(
                            connection,
                            prepared.evidence_set_id,
                            validate_blobs=True,
                        )
                        self._require_exact_retry(stored, prepared, created_at)
                        return stored
                    orphan = connection.execute(
                        """
                        select 1 from evidence_passages where evidence_set_id = ?
                        union all
                        select 1 from evidence_cunits where evidence_set_id = ?
                        limit 1
                        """,
                        (prepared.evidence_set_id, prepared.evidence_set_id),
                    ).fetchone()
                    if orphan is not None:
                        raise EvidenceTargetConflictError(
                            "Evidence target set contains incomplete stored state"
                        )
                    self._store_prepared_blobs(prepared)
                    for passage in prepared.passages:
                        connection.execute(
                            """
                            insert into evidence_passages (
                              evidence_set_id, passage_id, passage_ordinal,
                              role, text_sha256, text_length
                            ) values (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                prepared.evidence_set_id,
                                passage.passage_id,
                                passage.passage_ordinal,
                                passage.role,
                                passage.text_sha256,
                                passage.text_length,
                            ),
                        )
                    for passage in prepared.passages:
                        for cunit in passage.cunits:
                            connection.execute(
                                """
                                insert into evidence_cunits (
                                  evidence_set_id, cunit_id, passage_id,
                                  cunit_ordinal, text_sha256, text_length
                                ) values (?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    prepared.evidence_set_id,
                                    cunit.cunit_id,
                                    passage.passage_id,
                                    cunit.cunit_ordinal,
                                    cunit.text_sha256,
                                    cunit.text_length,
                                ),
                            )
                    connection.execute(
                        """
                        insert into evidence_sets (
                          evidence_set_id, import_id, project_source_id,
                          transcript_revision_id, producer_kind,
                          producer_version, producer_status, review_status,
                          transcript_text_sha256, snapshot_sha256,
                          passage_count, cunit_count, created_at
                        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            prepared.evidence_set_id,
                            prepared.import_id,
                            prepared.project_source_id,
                            prepared.transcript_revision_id,
                            prepared.producer_kind,
                            prepared.producer_version,
                            prepared.producer_status,
                            prepared.review_status,
                            prepared.transcript_text_sha256,
                            prepared.snapshot_sha256,
                            prepared.passage_count,
                            prepared.cunit_count,
                            created_at,
                        ),
                    )
                    stored = self._load_snapshot(
                        connection,
                        prepared.evidence_set_id,
                        validate_blobs=True,
                    )
                    self._require_exact_retry(stored, prepared, created_at)
                    return stored
        except EvidenceTargetBlobConflict:
            raise
        except EvidenceTextBlobIntegrityError as exc:
            raise EvidenceTargetBlobConflict(
                "Evidence target text storage is invalid"
            ) from exc
        except FileNotFoundError as exc:
            raise EvidenceTargetBlobConflict(
                "Evidence target text storage is incomplete"
            ) from exc
        except (
            EvidenceTargetValidationError,
            EvidenceTargetNotFoundError,
            EvidenceTargetConflictError,
        ):
            raise
        except (SchemaCompatibilityError, EvidenceCatalogConflict) as exc:
            raise EvidenceTargetConflictError(
                "Evidence target storage is unavailable or invalid"
            ) from exc
        except sqlite3.Error as exc:
            raise EvidenceTargetConflictError(
                "Evidence target registration conflicts with stored state"
            ) from exc

    def resolve(
        self,
        workspace_id: str,
        project_source_id: str,
        transcript_revision_id: str,
        evidence_set_id: str,
        passage_id: str,
        cunit_id: str = "",
    ) -> ResolvedEvidenceTarget:
        exact_workspace = _exact_text(workspace_id, "workspace_id")
        exact_source = _exact_text(project_source_id, "project_source_id")
        exact_revision = _patterned_id(
            transcript_revision_id,
            "transcript_revision_id",
            _REVISION_ID_PATTERN,
        )
        exact_set = _patterned_id(
            evidence_set_id,
            "evidence_set_id",
            _EVIDENCE_SET_ID_PATTERN,
        )
        exact_passage = _patterned_id(
            passage_id,
            "passage_id",
            _PASSAGE_ID_PATTERN,
        )
        if not isinstance(cunit_id, str):
            raise EvidenceTargetValidationError("cunit_id must be a string")
        exact_cunit = (
            _patterned_id(cunit_id, "cunit_id", _CUNIT_ID_PATTERN)
            if cunit_id
            else ""
        )
        try:
            with workspace_mutation_lock(self.root):
                with self.catalog.read() as connection:
                    snapshot = self._load_snapshot(
                        connection,
                        exact_set,
                        validate_blobs=True,
                    )
                    if (
                        snapshot.workspace_id != exact_workspace
                        or snapshot.project_source_id != exact_source
                        or snapshot.transcript_revision_id != exact_revision
                    ):
                        raise EvidenceTargetConflictError(
                            "Evidence target ownership conflicts with the request"
                        )
                    passage = next(
                        (
                            candidate
                            for candidate in snapshot.passages
                            if candidate.passage_id == exact_passage
                        ),
                        None,
                    )
                    if passage is None:
                        raise EvidenceTargetNotFoundError(
                            "Evidence passage was not found"
                        )
                    if exact_cunit:
                        cunit = next(
                            (
                                candidate
                                for candidate in passage.cunits
                                if candidate.cunit_id == exact_cunit
                            ),
                            None,
                        )
                        if cunit is None:
                            raise EvidenceTargetNotFoundError(
                                "Evidence C-unit was not found"
                            )
                        text = self.text_blobs.read_verified(cunit.text_sha256)
                        return ResolvedEvidenceTarget(
                            import_id=snapshot.import_id,
                            workspace_id=snapshot.workspace_id,
                            project_source_id=snapshot.project_source_id,
                            transcript_revision_id=snapshot.transcript_revision_id,
                            evidence_set_id=snapshot.evidence_set_id,
                            producer_kind=snapshot.producer_kind,
                            producer_version=snapshot.producer_version,
                            producer_status=snapshot.producer_status,
                            review_status=snapshot.review_status,
                            target_kind="cunit",
                            passage_id=passage.passage_id,
                            passage_ordinal=passage.passage_ordinal,
                            cunit_id=cunit.cunit_id,
                            cunit_ordinal=cunit.cunit_ordinal,
                            role=passage.role,
                            text=text,
                            text_sha256=cunit.text_sha256,
                            text_length=cunit.text_length,
                        )
                    text = self.text_blobs.read_verified(passage.text_sha256)
                    return ResolvedEvidenceTarget(
                        import_id=snapshot.import_id,
                        workspace_id=snapshot.workspace_id,
                        project_source_id=snapshot.project_source_id,
                        transcript_revision_id=snapshot.transcript_revision_id,
                        evidence_set_id=snapshot.evidence_set_id,
                        producer_kind=snapshot.producer_kind,
                        producer_version=snapshot.producer_version,
                        producer_status=snapshot.producer_status,
                        review_status=snapshot.review_status,
                        target_kind="passage",
                        passage_id=passage.passage_id,
                        passage_ordinal=passage.passage_ordinal,
                        cunit_id="",
                        cunit_ordinal=None,
                        role=passage.role,
                        text=text,
                        text_sha256=passage.text_sha256,
                        text_length=passage.text_length,
                    )
        except (EvidenceTargetNotFoundError, EvidenceTargetConflictError):
            raise
        except EvidenceTextBlobIntegrityError as exc:
            raise EvidenceTargetBlobConflict(
                "Evidence target text storage is invalid"
            ) from exc
        except FileNotFoundError as exc:
            raise EvidenceTargetBlobConflict(
                "Evidence target text storage is incomplete"
            ) from exc
        except (SchemaCompatibilityError, EvidenceCatalogConflict) as exc:
            raise EvidenceTargetConflictError(
                "Evidence target storage is unavailable or invalid"
            ) from exc
        except sqlite3.Error as exc:
            raise EvidenceTargetConflictError(
                "Evidence target storage is unavailable or invalid"
            ) from exc

    def workspace_snapshot(
        self,
        workspace_id: str,
    ) -> tuple[EvidenceSetSnapshot, ...]:
        exact_workspace = _exact_text(workspace_id, "workspace_id")
        try:
            with workspace_mutation_lock(self.root):
                with self.catalog.read() as connection:
                    rows = connection.execute(
                        """
                        select es.evidence_set_id
                        from evidence_sets es
                        join project_sources ps
                          on ps.project_source_id = es.project_source_id
                        where ps.workspace_id = ?
                        order by es.evidence_set_id
                        """,
                        (exact_workspace,),
                    ).fetchall()
                    snapshots = tuple(
                        self._load_snapshot(
                            connection,
                            _stored_patterned_id(
                                row[0],
                                "evidence_set_id",
                                _EVIDENCE_SET_ID_PATTERN,
                            ),
                            validate_blobs=True,
                        )
                        for row in rows
                    )
                    if any(
                        snapshot.workspace_id != exact_workspace
                        for snapshot in snapshots
                    ):
                        raise EvidenceTargetConflictError(
                            "Evidence target workspace snapshot is invalid"
                        )
                    return snapshots
        except EvidenceTargetConflictError:
            raise
        except EvidenceTextBlobIntegrityError as exc:
            raise EvidenceTargetBlobConflict(
                "Evidence target text storage is invalid"
            ) from exc
        except FileNotFoundError as exc:
            raise EvidenceTargetBlobConflict(
                "Evidence target text storage is incomplete"
            ) from exc
        except (SchemaCompatibilityError, EvidenceCatalogConflict) as exc:
            raise EvidenceTargetConflictError(
                "Evidence target storage is unavailable or invalid"
            ) from exc
        except sqlite3.Error as exc:
            raise EvidenceTargetConflictError(
                "Evidence target storage is unavailable or invalid"
            ) from exc

    def _store_prepared_blobs(self, prepared: PreparedEvidenceSet) -> None:
        self.text_blobs._store(
            prepared.transcript_text,
            prepared.transcript_text_sha256,
        )
        for passage in prepared.passages:
            self.text_blobs._store(passage.text, passage.text_sha256)
            for cunit in passage.cunits:
                self.text_blobs._store(cunit.text, cunit.text_sha256)

    def _verify_prepared_blobs(self, prepared: PreparedEvidenceSet) -> None:
        if (
            self.text_blobs.read_verified(prepared.transcript_text_sha256)
            != prepared.transcript_text
        ):
            raise EvidenceTargetBlobConflict(
                "Evidence target transcript text conflicts with stored content"
            )
        for passage in prepared.passages:
            if self.text_blobs.read_verified(passage.text_sha256) != passage.text:
                raise EvidenceTargetBlobConflict(
                    "Evidence target passage text conflicts with stored content"
                )
            for cunit in passage.cunits:
                if self.text_blobs.read_verified(cunit.text_sha256) != cunit.text:
                    raise EvidenceTargetBlobConflict(
                        "Evidence target C-unit text conflicts with stored content"
                    )

    def _require_import(
        self,
        connection: sqlite3.Connection,
        prepared: PreparedEvidenceSet,
    ) -> str:
        row = connection.execute(
            """
            select si.project_source_id, si.transcript_revision_id,
                   si.imported_at, ps.workspace_id, tr.transcript_sha256
            from source_imports si
            join project_sources ps
              on ps.project_source_id = si.project_source_id
            join transcript_revisions tr
              on tr.transcript_revision_id = si.transcript_revision_id
            join source_revisions sr
              on sr.project_source_id = si.project_source_id
             and sr.transcript_revision_id = si.transcript_revision_id
            where si.import_id = ?
            """,
            (prepared.import_id,),
        ).fetchone()
        if row is None:
            raise EvidenceTargetNotFoundError("Evidence import was not found")
        stored_source = _stored_exact_text(row[0], "project_source_id")
        stored_revision = _stored_patterned_id(
            row[1],
            "transcript_revision_id",
            _REVISION_ID_PATTERN,
        )
        imported_at = _stored_timestamp(row[2], "imported_at")
        stored_workspace = _stored_exact_text(row[3], "workspace_id")
        stored_digest = _stored_digest(row[4], "transcript_sha256")
        if (
            stored_source != prepared.project_source_id
            or stored_revision != prepared.transcript_revision_id
            or stored_workspace != prepared.workspace_id
            or stored_digest != prepared.transcript_text_sha256
        ):
            raise EvidenceTargetConflictError(
                "Evidence import ownership conflicts with target set"
            )
        return imported_at

    def _require_exact_retry(
        self,
        stored: EvidenceSetSnapshot,
        prepared: PreparedEvidenceSet,
        created_at: str,
    ) -> None:
        if (
            stored.evidence_set_id != prepared.evidence_set_id
            or stored.snapshot_sha256 != prepared.snapshot_sha256
            or stored.created_at != created_at
            or stored.to_manifest() != prepared.to_manifest()
        ):
            raise EvidenceTargetConflictError(
                "Evidence target set identity conflicts with stored state"
            )
        self._verify_prepared_blobs(prepared)

    def _load_snapshot(
        self,
        connection: sqlite3.Connection,
        target_set_id: str,
        *,
        validate_blobs: bool,
    ) -> EvidenceSetSnapshot:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            select es.*, ps.workspace_id, si.imported_at,
                   tr.transcript_sha256 as revision_transcript_sha256
            from evidence_sets es
            join source_imports si
              on si.import_id = es.import_id
             and si.project_source_id = es.project_source_id
             and si.transcript_revision_id = es.transcript_revision_id
            join project_sources ps
              on ps.project_source_id = es.project_source_id
            join transcript_revisions tr
              on tr.transcript_revision_id = es.transcript_revision_id
            join source_revisions sr
              on sr.project_source_id = es.project_source_id
             and sr.transcript_revision_id = es.transcript_revision_id
            where es.evidence_set_id = ?
            """,
            (target_set_id,),
        ).fetchone()
        if row is None:
            raise EvidenceTargetNotFoundError("Evidence target set was not found")
        stored_set_id = _stored_patterned_id(
            row["evidence_set_id"],
            "evidence_set_id",
            _EVIDENCE_SET_ID_PATTERN,
        )
        import_id = _stored_exact_text(row["import_id"], "import_id")
        workspace_id = _stored_exact_text(row["workspace_id"], "workspace_id")
        project_source_id = _stored_exact_text(
            row["project_source_id"],
            "project_source_id",
        )
        transcript_revision_id = _stored_patterned_id(
            row["transcript_revision_id"],
            "transcript_revision_id",
            _REVISION_ID_PATTERN,
        )
        producer_kind = _stored_exact_text(row["producer_kind"], "producer_kind")
        producer_version = _stored_int(row["producer_version"], "producer_version")
        producer_status = _stored_exact_text(
            row["producer_status"],
            "producer_status",
        )
        review_status = _stored_exact_text(row["review_status"], "review_status")
        expected_statuses = _PRODUCER_CONTRACTS.get(
            (producer_kind, producer_version)
        )
        if expected_statuses != (producer_status, review_status):
            raise EvidenceTargetConflictError(
                "Stored evidence target producer contract is invalid"
            )
        transcript_text_digest = _stored_digest(
            row["transcript_text_sha256"],
            "transcript_text_sha256",
        )
        if transcript_text_digest != _stored_digest(
            row["revision_transcript_sha256"],
            "revision_transcript_sha256",
        ):
            raise EvidenceTargetConflictError(
                "Stored evidence target revision digest is invalid"
            )
        stored_snapshot_digest = _stored_digest(
            row["snapshot_sha256"],
            "snapshot_sha256",
        )
        passage_count = _stored_nonnegative_int(row["passage_count"], "passage_count")
        cunit_count = _stored_nonnegative_int(row["cunit_count"], "cunit_count")
        if (
            passage_count > _MAX_TARGETS_PER_SET
            or cunit_count > _MAX_TARGETS_PER_SET
        ):
            raise EvidenceTargetConflictError(
                "Stored evidence target counts are invalid"
            )
        created_at = _stored_timestamp(row["created_at"], "created_at")
        if created_at != _stored_timestamp(row["imported_at"], "imported_at"):
            raise EvidenceTargetConflictError(
                "Stored evidence target creation time is invalid"
            )

        passage_rows = connection.execute(
            """
            select * from evidence_passages
            where evidence_set_id = ?
            order by passage_ordinal, passage_id
            """,
            (stored_set_id,),
        ).fetchall()
        cunit_rows = connection.execute(
            """
            select * from evidence_cunits
            where evidence_set_id = ?
            order by passage_id, cunit_ordinal, cunit_id
            """,
            (stored_set_id,),
        ).fetchall()
        if len(passage_rows) != passage_count or len(cunit_rows) != cunit_count:
            raise EvidenceTargetConflictError(
                "Stored evidence target counts are invalid"
            )
        cunits_by_passage: dict[str, list[EvidenceCUnitSnapshot]] = {}
        for cunit_row in cunit_rows:
            cunit_set_id = _stored_patterned_id(
                cunit_row["evidence_set_id"],
                "evidence_set_id",
                _EVIDENCE_SET_ID_PATTERN,
            )
            cunit_id_value = _stored_patterned_id(
                cunit_row["cunit_id"],
                "cunit_id",
                _CUNIT_ID_PATTERN,
            )
            parent_passage_id = _stored_patterned_id(
                cunit_row["passage_id"],
                "passage_id",
                _PASSAGE_ID_PATTERN,
            )
            ordinal = _stored_nonnegative_int(
                cunit_row["cunit_ordinal"],
                "cunit_ordinal",
            )
            if (
                cunit_set_id != stored_set_id
                or cunit_evidence_id(parent_passage_id, ordinal) != cunit_id_value
            ):
                raise EvidenceTargetConflictError(
                    "Stored evidence C-unit identity is invalid"
                )
            cunit_snapshot = EvidenceCUnitSnapshot(
                cunit_id=cunit_id_value,
                cunit_ordinal=ordinal,
                text_sha256=_stored_digest(
                    cunit_row["text_sha256"],
                    "text_sha256",
                ),
                text_length=_stored_nonnegative_int(
                    cunit_row["text_length"],
                    "text_length",
                ),
            )
            cunits_by_passage.setdefault(parent_passage_id, []).append(
                cunit_snapshot
            )

        passages: list[EvidencePassageSnapshot] = []
        for expected_ordinal, passage_row in enumerate(passage_rows):
            passage_set_id = _stored_patterned_id(
                passage_row["evidence_set_id"],
                "evidence_set_id",
                _EVIDENCE_SET_ID_PATTERN,
            )
            passage_id_value = _stored_patterned_id(
                passage_row["passage_id"],
                "passage_id",
                _PASSAGE_ID_PATTERN,
            )
            passage_ordinal = _stored_nonnegative_int(
                passage_row["passage_ordinal"],
                "passage_ordinal",
            )
            role = _stored_role(passage_row["role"])
            if (
                passage_set_id != stored_set_id
                or passage_ordinal != expected_ordinal
                or passage_evidence_id(
                    transcript_revision_id,
                    passage_ordinal,
                )
                != passage_id_value
            ):
                raise EvidenceTargetConflictError(
                    "Stored evidence passage identity is invalid"
                )
            passage_cunits = tuple(cunits_by_passage.pop(passage_id_value, []))
            if tuple(cunit.cunit_ordinal for cunit in passage_cunits) != tuple(
                range(len(passage_cunits))
            ):
                raise EvidenceTargetConflictError(
                    "Stored evidence C-unit ordinals are invalid"
                )
            passages.append(
                EvidencePassageSnapshot(
                    passage_id=passage_id_value,
                    passage_ordinal=passage_ordinal,
                    role=role,
                    text_sha256=_stored_digest(
                        passage_row["text_sha256"],
                        "text_sha256",
                    ),
                    text_length=_stored_nonnegative_int(
                        passage_row["text_length"],
                        "text_length",
                    ),
                    cunits=passage_cunits,
                )
            )
        if cunits_by_passage:
            raise EvidenceTargetConflictError(
                "Stored evidence C-unit parent is invalid"
            )
        snapshot = EvidenceSetSnapshot(
            evidence_set_id=stored_set_id,
            import_id=import_id,
            workspace_id=workspace_id,
            project_source_id=project_source_id,
            transcript_revision_id=transcript_revision_id,
            transcript_text_sha256=transcript_text_digest,
            producer_kind=producer_kind,
            producer_version=producer_version,
            producer_status=producer_status,
            review_status=review_status,
            passage_count=passage_count,
            cunit_count=cunit_count,
            passages=tuple(passages),
            snapshot_sha256=stored_snapshot_digest,
            created_at=created_at,
        )
        canonical_digest = _manifest_sha256(snapshot.to_manifest())
        if (
            canonical_digest != snapshot.snapshot_sha256
            or evidence_set_id(canonical_digest) != snapshot.evidence_set_id
        ):
            raise EvidenceTargetConflictError(
                "Stored evidence target snapshot identity is invalid"
            )
        if validate_blobs:
            transcript_text = self.text_blobs.read_verified(
                snapshot.transcript_text_sha256
            )
            if not transcript_text:
                raise EvidenceTargetConflictError(
                    "Stored evidence transcript text is invalid"
                )
            identity = transcript_evidence_identity(transcript_text)
            if identity.transcript_revision_id != snapshot.transcript_revision_id:
                raise EvidenceTargetConflictError(
                    "Stored evidence transcript identity is invalid"
                )
            reconstructed_passages: list[EvidencePassageInput] = []
            for passage in snapshot.passages:
                passage_text = self.text_blobs.read_verified(passage.text_sha256)
                if len(passage_text) != passage.text_length or not passage_text:
                    raise EvidenceTargetConflictError(
                        "Stored evidence passage text is invalid"
                    )
                reconstructed_cunits: list[EvidenceCUnitInput] = []
                for cunit in passage.cunits:
                    cunit_text = self.text_blobs.read_verified(cunit.text_sha256)
                    if len(cunit_text) != cunit.text_length or not cunit_text:
                        raise EvidenceTargetConflictError(
                            "Stored evidence C-unit text is invalid"
                        )
                    reconstructed_cunits.append(
                        EvidenceCUnitInput(
                            cunit_id=cunit.cunit_id,
                            cunit_ordinal=cunit.cunit_ordinal,
                            text=cunit_text,
                        )
                    )
                reconstructed_passages.append(
                    EvidencePassageInput(
                        passage_id=passage.passage_id,
                        passage_ordinal=passage.passage_ordinal,
                        role=passage.role,
                        text=passage_text,
                        cunits=tuple(reconstructed_cunits),
                    )
                )
            try:
                reconstructed = prepare_complete_evidence_set(
                    import_id=snapshot.import_id,
                    workspace_id=snapshot.workspace_id,
                    project_source_id=snapshot.project_source_id,
                    transcript_revision_id=snapshot.transcript_revision_id,
                    transcript_text=transcript_text,
                    producer_kind=snapshot.producer_kind,
                    producer_version=snapshot.producer_version,
                    producer_status=snapshot.producer_status,
                    review_status=snapshot.review_status,
                    passages=tuple(reconstructed_passages),
                )
            except EvidenceTargetValidationError as exc:
                raise EvidenceTargetConflictError(
                    "Stored evidence target producer interpretation is invalid"
                ) from exc
            if (
                reconstructed.evidence_set_id != snapshot.evidence_set_id
                or reconstructed.snapshot_sha256 != snapshot.snapshot_sha256
                or reconstructed.to_manifest() != snapshot.to_manifest()
            ):
                raise EvidenceTargetConflictError(
                    "Stored evidence target producer interpretation is invalid"
                )
        return snapshot


def prepare_complete_evidence_set(
    *,
    import_id: str,
    workspace_id: str,
    project_source_id: str,
    transcript_revision_id: str,
    transcript_text: str,
    producer_kind: str,
    producer_version: int,
    producer_status: str,
    review_status: str,
    passages: Sequence[EvidencePassageInput],
) -> PreparedEvidenceSet:
    exact_import = _exact_text(import_id, "import_id")
    exact_workspace = _exact_text(workspace_id, "workspace_id")
    exact_source = _exact_text(project_source_id, "project_source_id")
    exact_revision = _patterned_id(
        transcript_revision_id,
        "transcript_revision_id",
        _REVISION_ID_PATTERN,
    )
    exact_transcript_text = _content_text(transcript_text, "transcript_text")
    if not isinstance(producer_kind, str):
        raise EvidenceTargetValidationError("producer_kind must be a string")
    if type(producer_version) is not int or producer_version <= 0:
        raise EvidenceTargetValidationError(
            "producer_version must be a positive integer"
        )
    if not isinstance(producer_status, str) or not isinstance(review_status, str):
        raise EvidenceTargetValidationError(
            "producer statuses must be strings"
        )
    if _PRODUCER_CONTRACTS.get((producer_kind, producer_version)) != (
        producer_status,
        review_status,
    ):
        raise EvidenceTargetValidationError(
            "Unsupported evidence target producer contract"
        )
    identity = transcript_evidence_identity(exact_transcript_text)
    if identity.transcript_revision_id != exact_revision:
        raise EvidenceTargetValidationError(
            "transcript_text does not match transcript_revision_id"
        )
    if isinstance(passages, (str, bytes)) or not isinstance(passages, Sequence):
        raise EvidenceTargetValidationError(
            "passages must be a sequence of EvidencePassageInput"
        )
    if len(passages) > _MAX_TARGETS_PER_SET:
        raise EvidenceTargetValidationError("Evidence set contains too many passages")
    ordered_inputs = sorted(
        passages,
        key=lambda passage: (
            passage.passage_ordinal
            if isinstance(passage, EvidencePassageInput)
            and type(passage.passage_ordinal) is int
            else -1
        ),
    )
    prepared_passages: list[PreparedEvidencePassage] = []
    total_cunits = 0
    for expected_ordinal, passage in enumerate(ordered_inputs):
        if not isinstance(passage, EvidencePassageInput):
            raise EvidenceTargetValidationError(
                "passages must contain only EvidencePassageInput"
            )
        if type(passage.passage_ordinal) is not int:
            raise EvidenceTargetValidationError(
                "passage_ordinal must be an integer"
            )
        if passage.passage_ordinal != expected_ordinal:
            raise EvidenceTargetValidationError(
                "passage ordinals must be contiguous from zero"
            )
        exact_passage_id = _patterned_id(
            passage.passage_id,
            "passage_id",
            _PASSAGE_ID_PATTERN,
        )
        if passage_evidence_id(exact_revision, expected_ordinal) != exact_passage_id:
            raise EvidenceTargetValidationError(
                "passage_id does not match transcript revision and ordinal"
            )
        role = _role(passage.role)
        passage_text = _content_text(passage.text, "passage text")
        if isinstance(passage.cunits, (str, bytes)) or not isinstance(
            passage.cunits,
            Sequence,
        ):
            raise EvidenceTargetValidationError(
                "cunits must be a sequence of EvidenceCUnitInput"
            )
        ordered_cunits = sorted(
            passage.cunits,
            key=lambda cunit: (
                cunit.cunit_ordinal
                if isinstance(cunit, EvidenceCUnitInput)
                and type(cunit.cunit_ordinal) is int
                else -1
            ),
        )
        total_cunits += len(ordered_cunits)
        if total_cunits > _MAX_TARGETS_PER_SET:
            raise EvidenceTargetValidationError(
                "Evidence set contains too many C-units"
            )
        prepared_cunits: list[PreparedEvidenceCUnit] = []
        for expected_cunit_ordinal, cunit in enumerate(ordered_cunits):
            if not isinstance(cunit, EvidenceCUnitInput):
                raise EvidenceTargetValidationError(
                    "cunits must contain only EvidenceCUnitInput"
                )
            if type(cunit.cunit_ordinal) is not int:
                raise EvidenceTargetValidationError(
                    "cunit_ordinal must be an integer"
                )
            if cunit.cunit_ordinal != expected_cunit_ordinal:
                raise EvidenceTargetValidationError(
                    "C-unit ordinals must be contiguous from zero"
                )
            exact_cunit_id = _patterned_id(
                cunit.cunit_id,
                "cunit_id",
                _CUNIT_ID_PATTERN,
            )
            if (
                cunit_evidence_id(exact_passage_id, expected_cunit_ordinal)
                != exact_cunit_id
            ):
                raise EvidenceTargetValidationError(
                    "cunit_id does not match passage and ordinal"
                )
            cunit_text = _content_text(cunit.text, "C-unit text")
            prepared_cunits.append(
                PreparedEvidenceCUnit(
                    cunit_id=exact_cunit_id,
                    cunit_ordinal=expected_cunit_ordinal,
                    text=cunit_text,
                    text_sha256=evidence_text_sha256(cunit_text),
                    text_length=len(cunit_text),
                )
            )
        prepared_passages.append(
            PreparedEvidencePassage(
                passage_id=exact_passage_id,
                passage_ordinal=expected_ordinal,
                role=role,
                text=passage_text,
                text_sha256=evidence_text_sha256(passage_text),
                text_length=len(passage_text),
                cunits=tuple(prepared_cunits),
            )
        )
    _validate_producer_interpretation(
        producer_kind=producer_kind,
        passages=tuple(prepared_passages),
    )
    partial = PreparedEvidenceSet(
        import_id=exact_import,
        workspace_id=exact_workspace,
        project_source_id=exact_source,
        transcript_revision_id=exact_revision,
        transcript_text=exact_transcript_text,
        transcript_text_sha256=identity.transcript_sha256,
        producer_kind=producer_kind,
        producer_version=producer_version,
        producer_status=producer_status,
        review_status=review_status,
        passage_count=len(prepared_passages),
        cunit_count=total_cunits,
        passages=tuple(prepared_passages),
        snapshot_sha256="0" * 64,
        evidence_set_id="evs_" + "0" * 32,
    )
    snapshot_digest = _manifest_sha256(partial.to_manifest())
    return replace(
        partial,
        snapshot_sha256=snapshot_digest,
        evidence_set_id=evidence_set_id(snapshot_digest),
    )


def _reprepare(prepared: PreparedEvidenceSet) -> PreparedEvidenceSet:
    if not isinstance(prepared, PreparedEvidenceSet):
        raise EvidenceTargetValidationError(
            "prepared must be a PreparedEvidenceSet"
        )
    return prepare_complete_evidence_set(
        import_id=prepared.import_id,
        workspace_id=prepared.workspace_id,
        project_source_id=prepared.project_source_id,
        transcript_revision_id=prepared.transcript_revision_id,
        transcript_text=prepared.transcript_text,
        producer_kind=prepared.producer_kind,
        producer_version=prepared.producer_version,
        producer_status=prepared.producer_status,
        review_status=prepared.review_status,
        passages=tuple(
            EvidencePassageInput(
                passage_id=passage.passage_id,
                passage_ordinal=passage.passage_ordinal,
                role=passage.role,
                text=passage.text,
                cunits=tuple(
                    EvidenceCUnitInput(
                        cunit_id=cunit.cunit_id,
                        cunit_ordinal=cunit.cunit_ordinal,
                        text=cunit.text,
                    )
                    for cunit in passage.cunits
                ),
            )
            for passage in prepared.passages
        ),
    )


def _validate_producer_interpretation(
    *,
    producer_kind: str,
    passages: tuple[PreparedEvidencePassage, ...],
) -> None:
    if producer_kind == "analysis_turns":
        if any(passage.cunits for passage in passages):
            raise EvidenceTargetValidationError(
                "Analysis-turn evidence cannot contain C-units"
            )
        return
    if producer_kind != "cunit_segmentation":
        raise EvidenceTargetValidationError(
            "Unsupported evidence target producer contract"
        )

    from backend.segmentation.adjudicator import adjudicate_cunit_boundaries
    from backend.segmentation.models import RawTranscriptEvent

    events = [
        RawTranscriptEvent(
            timestamp_seconds=passage.passage_ordinal,
            speaker=passage.role,
            text=passage.text,
            source_filename="",
            passage_id=passage.passage_id,
        )
        for passage in passages
    ]
    decisions = adjudicate_cunit_boundaries(events).decisions
    if len(decisions) != len(passages):
        raise EvidenceTargetValidationError(
            "C-unit evidence does not match the current producer"
        )
    for passage, decision in zip(passages, decisions, strict=True):
        expected = tuple(
            (cunit_id, ordinal, cunit_text)
            for ordinal, (cunit_id, cunit_text) in enumerate(
                zip(decision.cunit_ids, decision.cunit_texts, strict=True)
            )
        )
        actual = tuple(
            (cunit.cunit_id, cunit.cunit_ordinal, cunit.text)
            for cunit in passage.cunits
        )
        if decision.passage_id != passage.passage_id or actual != expected:
            raise EvidenceTargetValidationError(
                "C-unit evidence does not match the current producer"
            )


def _manifest_sha256(manifest: Mapping[str, object]) -> str:
    try:
        encoded = json.dumps(
            manifest,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise EvidenceTargetValidationError(
            "Evidence target manifest is not canonical JSON"
        ) from exc
    return sha256(encoded).hexdigest()


def _exact_text(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > _MAX_IDENTIFIER_LENGTH
    ):
        raise EvidenceTargetValidationError(
            f"{field_name} must be a non-empty exact bounded string"
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise EvidenceTargetValidationError(
            f"{field_name} must be valid Unicode"
        ) from exc
    return value


def _patterned_id(
    value: object,
    field_name: str,
    pattern: re.Pattern[str],
) -> str:
    exact = _exact_text(value, field_name)
    if not pattern.fullmatch(exact):
        raise EvidenceTargetValidationError(f"{field_name} is invalid")
    return exact


def _role(value: object) -> str:
    if not isinstance(value, str) or len(value) > _MAX_ROLE_LENGTH:
        raise EvidenceTargetValidationError("role must be an exact bounded string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise EvidenceTargetValidationError("role must be valid Unicode") from exc
    return value


def _content_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceTargetValidationError(
            f"{field_name} must be a non-empty string"
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise EvidenceTargetValidationError(
            f"{field_name} must be valid Unicode"
        ) from exc
    return value


def _stored_exact_text(value: object, field_name: str) -> str:
    try:
        return _exact_text(value, field_name)
    except EvidenceTargetValidationError as exc:
        raise EvidenceTargetConflictError(
            f"Stored evidence target {field_name} is invalid"
        ) from exc


def _stored_patterned_id(
    value: object,
    field_name: str,
    pattern: re.Pattern[str],
) -> str:
    try:
        return _patterned_id(value, field_name, pattern)
    except EvidenceTargetValidationError as exc:
        raise EvidenceTargetConflictError(
            f"Stored evidence target {field_name} is invalid"
        ) from exc


def _stored_role(value: object) -> str:
    try:
        return _role(value)
    except EvidenceTargetValidationError as exc:
        raise EvidenceTargetConflictError(
            "Stored evidence target role is invalid"
        ) from exc


def _stored_int(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise EvidenceTargetConflictError(
            f"Stored evidence target {field_name} is invalid"
        )
    return value


def _stored_nonnegative_int(value: object, field_name: str) -> int:
    stored = _stored_int(value, field_name)
    if stored < 0:
        raise EvidenceTargetConflictError(
            f"Stored evidence target {field_name} is invalid"
        )
    return stored


def _stored_digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise EvidenceTargetConflictError(
            f"Stored evidence target {field_name} is invalid"
        )
    return value


def _stored_timestamp(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 64
    ):
        raise EvidenceTargetConflictError(
            f"Stored evidence target {field_name} is invalid"
        )
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise EvidenceTargetConflictError(
            f"Stored evidence target {field_name} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidenceTargetConflictError(
            f"Stored evidence target {field_name} is invalid"
        )
    return value
