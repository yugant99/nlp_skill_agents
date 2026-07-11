# 2026-07-10 Phase 1 SQLite Migration Policy

## Goal

Replace implicit create-or-alter startup behavior with explicit, transactional,
forward-only schema migrations for the two authoritative SQLite catalogs, preserve
historical evidence during upgrades, and fail visibly when application and
database versions are incompatible.

## Files Changed

- `backend/storage/sqlite_migrations.py`
- `backend/storage/local_store.py`
- `backend/storage/evidence_catalog.py`
- `backend/app/main.py`
- `tests/test_sqlite_migrations.py`
- `tests/test_pipeline_storage.py`
- `tests/test_evidence_catalog.py`
- `tests/test_api.py`
- `README.md`
- `checkpoints/README.md`
- `goals/psychology-research-platform-roadmap.md`

## Implemented

- Added contiguous, named migration definitions backed by SQLite `user_version`
  and an applied-at `schema_migrations` ledger.
- Applies each migration inside a transaction and rolls back its schema and ledger
  writes together when SQLite reports a failure.
- Refuses databases newer than the application, ledger/version disagreement, and
  changed migration identities instead of silently continuing.
- Versions `runs.sqlite3` through five migrations covering the base run table,
  evidence identity, import identity, project-source lineage, and history index.
- Versions `evidence.sqlite3` through three migrations covering the import
  catalog, project-source/revision lineage, foreign-key-backed import records, and
  workspace/history indexes.
- Upgrades the original run database and original import catalog in place. Legacy
  imports receive deterministic isolated source IDs and no fabricated ancestry.
- Re-versions the immediately previous full evidence schema without losing source
  workspace ownership, parent ancestry, import identity, or transcript identity.
- Enforces real `source_imports` foreign keys to `project_sources` and
  `transcript_revisions`; migration tests run `foreign_key_check` after upgrade.
- Added `GET /api/storage/schema-status` to report the applied names and current
  version for both catalogs. It returns HTTP 409 for an unsupported newer schema.

## CLI Verification

- Migration engine, evidence catalog, run storage, archive, and API slice passed:
  55/55.
- API suite, including compatible and incompatible status responses, passed:
  42/42.
- `.venv/bin/pytest -q` passed: 153/153.
- `cd frontend && npm run build` passed.
- All frontend helper suites passed: 30/30.
- `git diff --check` passed for every implementation slice.

## UI Verification

No visual control changed. Migration behavior and status are operator-facing HTTP
and startup contracts. Existing researcher-facing behavior was protected by the
complete backend suite, production frontend build, and every frontend helper
suite.

## Git Commits

- `1ad63dc Add transactional SQLite migrations`
- `775a158 Version analysis run schema`
- `3861452 Version evidence catalog schema`
- `6821141 Expose storage schema compatibility`

## Known Limitations And Rollback

- Migrations cover `runs.sqlite3` and `evidence.sqlite3`. Versioned JSON artifacts
  still rely on loader compatibility and do not have a general migration engine.
- Each SQLite migration is transactional within one database. Analysis and restore
  workflows still span databases, blobs, JSON, CSV, and directories and therefore
  are not one cross-store transaction. A durable operation journal and
  reconciliation remain the next workflow-boundary work.
- There is no downgrade migration. Before rolling the application back across a
  schema version, restore a backup created by the older application version.
- Existing databases that predate version metadata are identified by their
  idempotent historical schema steps and stamped as they advance. Tests cover the
  original database shapes and the immediately previous full evidence shape.
- Schema changes assume the current single-process, local appliance topology.
  Startup locking or maintenance mode is still needed before concurrent writers or
  background workers can migrate the same database.
- Reverting the feature code removes migration support but does not downgrade a
  database already stamped at the new versions. Use a pre-feature backup for a
  complete rollback.
