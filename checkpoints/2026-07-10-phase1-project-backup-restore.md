# 2026-07-10 Phase 1 Project Backup And Restore

## Goal

Create a real portable study archive and prove it can restore the study files,
evidence identity, original source blobs, and attributable audit history without
changing identifiers or trusting ZIP contents blindly.

## Files Changed

- `backend/storage/evidence_catalog.py`
- `backend/storage/project_archive.py`
- `backend/storage/audit_log.py`
- `backend/app/main.py`
- `tests/test_evidence_catalog.py`
- `tests/test_project_archive.py`
- `tests/test_api.py`
- `README.md`
- `checkpoints/README.md`
- `goals/psychology-research-platform-roadmap.md`

## Implemented

- Exports workspace-scoped evidence import records with source ownership,
  transcript hashes, parent ancestry, and source-blob hashes rather than copying
  environment-specific SQLite internals.
- Creates a versioned ZIP containing every regular file under the study directory,
  evidence import records, all referenced verified source blobs, and study-scoped
  audit events.
- Manifest records every member path, byte size, and SHA-256. The completed archive
  also receives an external SHA-256 in the API response.
- Rejects source-directory symlinks, ZIP symlinks, traversal and absolute paths,
  backslash paths, duplicates, missing/extra/undeclared members, missing required
  members, member size/hash mismatch, malformed ZIP/JSON, unsupported versions,
  workspace mismatches, blob-set mismatches, and study-ID mismatches.
- Enforces compressed archive, uncompressed content, and member-count limits.
- Restore writes study files into a staging directory, verifies and deduplicates
  source blobs, replays catalog records through existing lineage invariants,
  imports audit events idempotently, and atomically renames the staged study into
  place only after those checks pass.
- Restore refuses to overwrite an existing study.
- Audit import preserves IDs and rejects attempts to reuse an event ID with changed
  content.
- Added `POST /api/studies/{study_id}/backup` and multipart
  `POST /api/studies/restore` endpoints, including a bounded upload read.

## CLI Verification

- Workspace evidence snapshot tests passed: 3/3.
- Archive round-trip, corruption, traversal, malformed-input, and conflict tests
  plus API backup/restore tests passed: 42/42.
- Expanded archive, audit, atomic-write, study, and API slice passed: 57/57.
- `.venv/bin/pytest -q` passed: 145/145.
- `cd frontend && npm run build` passed.
- All frontend helper suites passed: 30/30.
- `git diff --check` passed for every implementation slice.

## UI Verification

No visual backup control was added. The complete flow was proven through the HTTP
API and direct restore into a separate data root, followed by study listing,
catalog inspection, source-blob verification, audit inspection, and a second
restore conflict. The production frontend build and all helper suites remain green.

## Git Commits

- `8d43263 Export workspace evidence records`
- `f6ec103 Add verified project backup and restore`
- `dc0c692 Preserve study audit history in backups`

## Known Limitations And Rollback

- Archives are local ZIP files and are not encrypted or cryptographically signed.
  Encrypted backup policy and signing remain production/security work.
- Archive creation and restore currently hold member bytes in memory within hard
  limits. Streaming archives and background progress are needed for larger
  projects.
- Restore validates everything before exposing the study directory, but catalog,
  blob, and audit writes are separate stores. A late failure can leave verified
  catalog/audit/blob records without the final study directory; reconciliation and
  a transactional restore journal remain open.
- Archives are study-scoped. They intentionally exclude unrelated standalone runs,
  agent jobs, plugin requests, and other studies.
- Restore is create-only; merge, overwrite, rename-on-conflict, and selective
  recovery are not supported.
- No researcher-facing backup/restore UI or scheduled backup policy exists yet.
- Deletion, retention, withdrawal, and orphan-blob garbage collection remain
  separate lifecycle work.
- Reverting the three implementation commits removes archive APIs and restore
  support. Existing `.nlpstudy.zip` files remain inert portable artifacts.
