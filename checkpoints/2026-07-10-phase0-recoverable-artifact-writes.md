# 2026-07-10 Phase 0 Recoverable Artifact Writes

## Goal

Replace direct final-path writes with same-directory atomic replacement so an
interrupted write cannot truncate the last complete research or workflow artifact.

## Files Changed

- `backend/storage/atomic.py`
- `backend/storage/audit_log.py`
- `backend/storage/library_store.py`
- `backend/storage/local_store.py`
- `backend/storage/study_store.py`
- `backend/extensions/agent_jobs.py`
- `backend/extensions/plugin_requests.py`
- `backend/segmentation/pipeline.py`
- `tests/test_atomic_writes.py`
- `README.md`
- `checkpoints/README.md`
- `goals/psychology-research-platform-roadmap.md`

## Implemented

- Added a shared text writer that creates a hidden temporary file beside its
  destination, flushes and syncs complete content, atomically replaces the final
  path, and syncs the containing directory on POSIX systems.
- Preserved the prior complete destination and removed the temporary file when
  content generation or final replacement fails.
- Migrated analysis results and CSV exports, study and batch state, bundle
  manifests, approved-library artifacts, agent jobs and evidence, plugin requests,
  segmentation state, final transcripts, evidence bundles, and HTML packets.
- Serialized audit updates inside the current process and atomically replaced the
  complete JSONL history.
- Rejected an incomplete existing audit-log tail instead of appending new data to
  a corrupt record.

## CLI Verification

- `.venv/bin/pytest tests/test_atomic_writes.py -q` passed: 5/5.
- Storage and API migration slices passed: 54/54, 13/13, 57/57, and 52/52.
- `.venv/bin/pytest -q` passed: 129/129.
- `cd frontend && npm run build` passed.
- All frontend helper suites passed: 27/27.
- A production-source audit found no remaining direct text/byte final-path writes,
  truncating opens, or JSON/YAML dump writers outside the atomic implementation.
- `git diff --check` passed for every implementation slice.

## UI Verification

No visible UI contract changed. Existing API, export, study-workspace, agent-job,
and segmentation tests exercised the user-facing persistence paths, while explicit
failure-injection tests proved the interrupted-write behavior.

## Git Commits

- `9cbf610 Add recoverable atomic text writes`
- `871562f Write research artifacts atomically`
- `4c7730f Protect agent workflow artifacts`
- `53893f1 Protect segmentation state and exports`
- `1b4d12d Make audit log updates recoverable`

## Known Limitations And Rollback

- Atomicity is per file. A study batch or other workflow that produces several
  files can still stop between complete artifacts; Phase 1 needs transactional
  workflow boundaries and explicit recovery state.
- Audit JSONL replacement is serialized only within one Python process and rewrites
  the complete log. The initial loopback topology uses one process; Phase 1 should
  move authoritative audit state into transactional SQLite before adding workers.
- Windows skips directory-handle syncing because portable directory `fsync` is not
  available there. File content is synced and `os.replace` still prevents readers
  from seeing a partially generated destination.
- A hard process or power loss can leave an unused hidden temporary file, but the
  last complete final artifact remains intact. Startup orphan cleanup remains an
  operations follow-up.
- Reverting the five implementation commits restores direct writes. Stored artifact
  formats are unchanged, so rollback does not require data migration.
