# 2026-07-10 Phase 1 Analysis Operation Journal

## Goal

Make standalone analysis persistence fail visibly and retry safely across its
SQLite and file stores, so a stopped process cannot leave untracked partial work
that looks complete.

## Files Changed

- `backend/storage/local_store.py`
- `backend/app/main.py`
- `tests/test_pipeline_storage.py`
- `tests/test_api.py`
- `README.md`
- `checkpoints/README.md`

## Implemented

- Added analysis-run schema migration 6 with a constrained
  `analysis_operations` journal and status/history index.
- Writes a committed `running` operation before the first persistence side effect.
  The journal binds the run ID and import ID to a canonical SHA-256 of the exact
  persisted run payload.
- Advances through `validated`, `source_blob_stored`, `evidence_cataloged`,
  `artifacts_written`, and `completed` only after each stage succeeds.
- Records `failed`, the last completed stage, attempt count, and exception class.
  It intentionally excludes exception messages and transcript content.
- Exact retries replay source-blob verification, evidence identity checks, and
  atomic artifact writes before completing. A second attempt produces one import
  and one run row rather than duplicates.
- Replaced run-index `insert or replace` with immutable insert-and-verify behavior.
  Conflicting journal payloads or pre-journal run rows fail before blob, catalog,
  or artifact writes can overwrite evidence.
- Commits the final `analysis_runs` row and journal `completed` marker in one SQLite
  transaction.
- Added `GET /api/storage/analysis-operations`, including an incomplete-only
  filter. Responses expose hashes, stages, status, attempts, timestamps, and error
  class but no research content.

## CLI Verification

- Journal-specific pipeline storage tests passed: 8/8.
- Complete API suite passed: 43/43.
- Expanded storage, evidence, archive, segmentation, study, and API slice passed
  before the final identity guard: 83/83; the final storage/API slice passed
  51/51 after that guard.
- `.venv/bin/pytest -q` passed: 158/158.
- `cd frontend && npm run build` passed.
- All frontend helper suites passed: 30/30.
- `git diff --check` passed for every implementation slice.

## Failure Proof

- Injected a failure after verified source-blob storage and before catalog insert.
- A new store instance reported one failed operation at `source_blob_stored`, with
  `OSError` but not the sensitive test message.
- The blob remained verifiable while run history and evidence imports remained
  empty.
- Retrying the exact run completed attempt 2 with one run row, one import, and the
  expected JSON/CSV artifacts.
- A conflicting pre-journal run row was rejected before any operation row, blob,
  import, or run directory was created, and the original index row was preserved.

## UI Verification

No researcher-facing control changed. The recovery state is currently an operator
HTTP contract. Existing UI behavior was protected by the production build and all
frontend helper suites.

## Git Commits

- `0a4fef7 Add analysis operation journal schema`
- `5392837 Journal analysis persistence attempts`
- `02fb530 Expose analysis persistence operations`
- `493e285 Reject run conflicts before persistence`

## Known Limitations And Rollback

- This journal covers standalone `LocalRunStore` analysis only. Study-batch,
  segmentation, archive-restore, agent-job, and other multi-artifact workflows
  still need explicit workflow boundaries; the broader roadmap checkbox remains
  open.
- Failed or hard-interrupted operations are visible, but there is no HTTP retry or
  automatic startup reconciler yet. Early-stage recovery requires resubmitting the
  source; a new API submission currently creates a new run ID.
- A hard stop between an idempotent side effect and its stage update may leave the
  journal one stage behind. Replaying the exact operation is designed to verify or
  repeat that side effect safely.
- Completed retries intentionally re-run integrity checks rather than trusting the
  journal alone. They increment the attempt count.
- The current single-process appliance does not support two concurrent writers for
  the same run ID. Normal API calls generate unique run IDs.
- Journal rows do not delete orphan blobs or catalog records. Reconciliation and
  retention/garbage collection remain separate lifecycle work.
- Reverting the feature removes journaling and restores the prior persistence
  path. Migration 6 is additive; its journal table can remain inert. Reverting to
  code that supports schema version 5 requires restoring a pre-migration backup.
