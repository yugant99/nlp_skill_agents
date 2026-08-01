# 2026-07-31 Phase 1 Study Batch Hardening

## Goal

Finish the paused review of the Phase 1 study-batch operation journal, preserve
pre-journal history, and make completed reads plus project backup/restore fail
visibly without partial destination evidence writes.

## Files Changed

- `backend/app/main.py`
- `backend/storage/audit_log.py`
- `backend/storage/evidence_catalog.py`
- `backend/storage/project_archive.py`
- `backend/storage/source_blob_store.py`
- `backend/storage/sqlite_migrations.py`
- `backend/storage/study_batch_operation_store.py`
- `backend/storage/study_store.py`
- `backend/storage/workspace_lock.py`
- `tests/test_api.py`
- `tests/test_project_archive.py`
- `tests/test_sqlite_migrations.py`
- `tests/test_study_batch_operation_store.py`
- `tests/test_study_workspaces.py`
- `README.md`
- `goals/psychology-research-platform-roadmap.md`
- `checkpoints/README.md`

## Implemented

- Serialized and revalidated SQLite migrations under an immediate transaction,
  with exact migration-ledger names, timestamps, and version agreement.
- Hardened study and skill-pack publication against duplicate, partial, overlong,
  non-portable, or semantically invalid identities before side effects.
- Made completed batch readers validate exact manifest types, journal identity,
  skill-pack content, run snapshots, source blobs, evidence records, aggregate
  hashes, CSV bytes, and the stable audit event. Tampered filesystem objects map to
  domain conflicts instead of raw server errors.
- Preserved truly pre-journal batch list, detail, run-list, and run-detail behavior.
  Backup and restore now validate legacy manifests, run snapshots, aggregates, CSV
  exports, skill packs, and completion audit events rather than skipping them.
- Legacy runs are validated against their actual persisted generation. Current
  lineage-aware provenance requires its exact evidence row and verified blob;
  first-generation import provenance validates any retained catalog row without
  requiring a blob that writer never stored; early deterministic-hash,
  metadata-only, and pre-audit shapes remain readable at reduced trust without
  synthesizing missing identity or audit events.
- Kept journal-known running and failed batches out of completed history and made
  direct reads return a conflict.
- Hardened archive parsing against malformed manifest/record types, Unicode and
  case collisions, Win32-invalid names and device aliases, component bounds,
  file/directory prefix collisions, symlinks, encrypted members, and unsupported
  compression.
- Added collision-resistant backup names and translated ZIP, SQLite, blob, and
  completed-artifact integrity failures into archive-domain errors.
- Added a re-entrant process/workspace mutation lock for shared blob, catalog,
  audit, study-create, and restore publication paths.
- Restore now validates in staging, preflights destination catalog/audit/blob
  state, publishes the verified shared state, and restores the original catalog,
  audit, and newly introduced blobs if final study publication fails.

## Failure And Recovery Proof

- Rejected boolean/string manifest counters that previously coerced to valid
  integers and rejected overlong persisted skill-pack identifiers before path
  construction.
- Replaced completed manifests, aggregates, run snapshots, CSVs, and skill-pack
  artifacts with directories or symlinks and observed controlled conflicts.
- Rejected tampered pre-journal aggregate, run, CSV, and audit artifacts during
  both backup and restore while retaining valid legacy history.
- Generated a real pre-journal, zero-row-metric batch from `origin/master` and
  loaded/listed it with the hardened reader, preserving its independently created
  aggregate, manifest, and audit timestamps.
- Rejected missing and corrupt evidence blobs as HTTP 409 backup conflicts.
- Returned controlled HTTP 409 conflicts for directory, corrupt, and symbolic-link
  study-batch journals across operation status and completed-history endpoints.
- Loaded the four historical pre-journal generations represented in repository
  history and round-tripped the original pre-audit generation through backup and
  restore.
- Rejected unsafe portable paths, prefix collisions, encrypted ZIP entries, and
  unsupported compression without extraction or HTTP 500 responses.
- Injected a failure at final study publication and verified the destination
  catalog, audit log, existing blobs, and existing study files were byte-for-byte
  unchanged, with no restored study published.

## CLI Verification

- Focused backend integration suite passed: 258/258.
- Complete backend suite passed: 388/388.
- Frontend production build passed with 1,710 modules transformed.
- All eight frontend helper suites passed: 30/30.
- `git diff --check` passed.

## UI Verification

No researcher-facing UI changed. API history, direct-read conflict behavior, and
backup error mapping were covered through `TestClient`; the production frontend
build and all helper suites are the UI regression gate.

## Known Limitations And Rollback

- The workspace lock is a single-host filesystem/process boundary, not a
  distributed coordination protocol. Its Windows branch still requires execution
  on the target Phase 5 appliance.
- Archive creation and restore still buffer member bytes within hard limits;
  streaming and background progress remain future work.
- Restore is create-only. Merge, overwrite, rename-on-conflict, selective restore,
  encryption, signing, scheduled backup, retention, deletion, and withdrawal are
  separate lifecycle work.
- Hard-stopped study batches still require an explicit operator recovery design;
  the journal does not retain transcript content or automatically take over a
  `running` operation.
- Historical early-hash, metadata-only, and pre-audit run snapshots cannot be
  retrospectively bound to an evidence row or source blob. First-generation
  import snapshots may also lack their original blob because that writer did not
  retain it. They remain historical artifacts without invented stronger evidence.
- Reverting this hardening restores the earlier journal and archive behavior.
  Preserve `batch_operations.sqlite3`, evidence storage, audit JSONL, source blobs,
  and project archives before any rollback that could remove recovery evidence.
