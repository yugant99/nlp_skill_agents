# 2026-07-29 Phase 1 Study Batch Operation Journal

## Goal

Make multi-transcript study batches observable and exactly retryable across the
source-blob, evidence-catalog, run-snapshot, aggregate, CSV, batch-manifest, and
audit boundaries without storing transcript or result content in the journal.

## Files Changed

- `backend/storage/study_batch_operation_store.py`
- `backend/storage/study_store.py`
- `backend/storage/audit_log.py`
- `backend/storage/project_archive.py`
- `backend/qualitative/database.py`
- `backend/app/main.py`
- `tests/test_study_batch_operation_store.py`
- `tests/test_study_workspaces.py`
- `tests/test_atomic_writes.py`
- `tests/test_project_archive.py`
- `tests/test_api.py`
- `README.md`
- `goals/psychology-research-platform-roadmap.md`
- `checkpoints/README.md`
- `checkpoints/2026-07-29-phase1-study-batch-operation-journal.md`

## Implemented

- Added `studies/<study_id>/batch_operations.sqlite3` with an ordered migration
  ledger, operation rows, reserved child-item rows, database constraints, and
  bounded status queries.
- Records a batch before its directory or any cross-store side effect is created.
  The operation binds the study, batch ID, exact skill-pack artifact hash, ordered
  request hash, item count, stable audit-event ID, stage, attempt count,
  timestamps, and exception class.
- Reserves run, import, project-source, source-blob, transcript-revision, and
  created-at identities before analysis. A caught retry reuses those identities
  instead of creating a second logical import or batch.
- Advances each successful item through analysis, blob retention, evidence
  cataloging, run snapshot, and completion. Expected analytical `ValueError` or
  `KeyError` failures remain isolated rejected items with exception class only.
- Added exact JSON and CSV snapshot writes. Existing identical artifacts are
  accepted; different bytes fail visibly without overwrite. Writes use explicit
  UTF-8 bytes so retry comparisons are stable on Windows and POSIX.
- Uses one deterministic `batch.completed` audit identity. Retrying after the
  audit write but before journal completion produces one event.
- Added an OS-level audit lock so separate local Python processes cannot lose an
  event through concurrent read-and-replace writes.
- A completed replay performs read-only integrity verification of the batch
  manifest, terminal item set, run payload hashes and identities, source blobs,
  evidence records, aggregate results, CSV exports, and completion audit event.
- Added optional `batch_id` inputs to both text and multipart study-batch routes.
  Exact completed replay is a no-op; changed input under the same ID is HTTP 409.
- Added per-study operation-list and schema-status endpoints. Operation responses
  contain identifiers, hashes, counts, stages, attempt count, timestamps, and
  exception class, but no filename, transcript, metadata, result row, or exception
  message.
- Project backups hold the shared per-study mutation boundary, reject a live
  batch, and serialize batch starts, study-schema writes, skill-pack writes, and
  qualitative transactions while snapshot bytes are captured.
- Batch manifests persist a root-relative path, while loaders bind it to the
  active data root. Restored journals are migrated and integrity-checked before
  evidence or audit records are imported and before the study is published.

## Failure And Recovery Proof

- Injected failure after an evidence import committed. Exact retry retained the
  original run/import/project-source identities, produced one import, one batch,
  and one audit event, and completed at attempt two.
- Injected failure after the stable audit event was written. Retry deduplicated
  the event and completed the journal.
- Replayed a completed batch without executing analysis and without incrementing
  its attempt count.
- Changed transcript input under a completed batch ID and verified conflict before
  new side effects.
- Tampered an existing aggregate snapshot and verified retry returned a conflict
  without overwriting the tampered file.
- Tampered a completed aggregate and verified the read-only completed-replay check
  rejected it.
- Forced a subprocess to exit immediately after source-blob storage. A new process
  observed a `running` operation and reserved item, found no false run snapshot,
  and refused a second live attempt.
- Started identical and changed journal operations concurrently and verified one
  serialized owner with domain conflicts rather than raw database-lock errors.
- Held an archive snapshot guard while starting a batch and changing study
  metadata. Both mutations waited; a sufficiently long archive returned a
  content-safe busy conflict rather than HTTP 500.
- Started a backup with a live batch and verified HTTP 409 with no archive output.
- Archived and restored a completed journal, item rows, evidence, source blob, and
  audit event. The restored batch resolved only inside the restore root and exact
  replay remained a one-event, attempt-one no-op.
- Rejected a newer restored batch-journal schema before evidence/audit import or
  study publication.
- Started eight audit writers in separate processes and retained all eight events.
- Verified distinctive filename, transcript, metadata, and analytical error text
  were absent from the journal database and operation API response.

## CLI Verification

- Complete backend suite passed: 222/222.
- Frontend production build passed.
- All frontend helper suites passed: 30/30.
- `git diff --check` passed.

## UI Verification

No researcher-facing control changed. The feature adds API recovery inputs and an
operator diagnostic contract. The production frontend build and all existing
frontend helper suites passed as the UI regression gate.

## Git Commits

- `f757c3f Add study batch operation journal schema`
- `c212953 Enforce study batch journal boundaries`
- `c3d00fe Serialize audit writes across processes`
- `defc128 Make study batches exactly retryable`
- `788e73a Verify completed batch replay integrity`
- `eaea639 Expose study batch recovery status`
- `1deaf0f Guard consistent study archive snapshots`
- `aa99972 Prove hard-stopped study batch visibility`

## Known Limitations And Rollback

- A caught exception is marked failed and can be retried only when the caller
  retains the same explicit batch ID and exact ordered inputs. There is no retry
  button, retained request payload, background worker, or startup scanner yet.
- A process killed without an exception leaves `status=running`. That state is
  deliberately visible and blocks replay and backup, but there is no lease,
  heartbeat, takeover, abandon, or administrative reconciliation path.
- The journal coordinates and verifies cross-store effects; it does not turn the
  filesystem, evidence SQLite database, audit JSONL, and journal SQLite database
  into one transaction. Earlier valid effects may remain after a later failure.
- Journal rows retain opaque operational identities and hashes, including a
  project-source ID. They exclude transcript, filename, metadata, results, and
  exception messages. Callers must not encode sensitive human-readable data in
  opaque identifier fields.
- The mutation guard covers current managed study-schema, skill-pack, qualitative,
  and batch write paths. Manual filesystem edits and future write paths must join
  the same guard to participate in consistent backups.
- Audit and archive coordination is designed for the documented single-host local
  appliance. It is not a distributed lock or a multi-host collaboration protocol.
- Per-study archives still exclude root-level segmentation journals and artifacts.
- Phase 1 remains open: codebook, case/attribute, coding-reference, memo,
  annotation, saved-query, and complete project-lifecycle workflows are not done.
- The pipeline/API integration can be reverted while leaving the additive journal
  database inert. Preserve `batch_operations.sqlite3` and project backups before
  deletion if recovery history is required.
