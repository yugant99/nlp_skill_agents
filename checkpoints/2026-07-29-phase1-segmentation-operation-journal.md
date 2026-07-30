# 2026-07-29 Phase 1 Segmentation Operation Journal

## Goal

Record segmentation persistence attempts before their first side effect, detect
conflicting snapshot updates, and expose content-safe failure diagnostics across
source-blob, evidence-catalog, specialist-packet, and run-snapshot writes.

## Files Changed

- `backend/storage/segmentation_operation_store.py`
- `backend/segmentation/pipeline.py`
- `backend/app/main.py`
- `tests/test_segmentation_operation_store.py`
- `tests/test_segmentation_core.py`
- `tests/test_api.py`
- `README.md`
- `goals/psychology-research-platform-roadmap.md`
- `checkpoints/README.md`
- `checkpoints/2026-07-29-phase1-segmentation-operation-journal.md`

## Implemented

- Added a root-level `segmentation.sqlite3` database with an ordered migration
  ledger and one `segmentation_operations` table.
- Records `create`, `patch`, `verify`, and explicit `rewrite` attempts before
  source-blob, evidence-catalog, specialist-packet, or snapshot writes begin.
- Binds each attempt to its run/import identity, previous canonical payload hash,
  target payload hash, status, last completed stage, attempt count, timestamps,
  and exception class.
- Advances only through the ordered stages `prepared`, `source_blob_stored`,
  `evidence_cataloged`, `specialist_artifacts_written`, `snapshot_written`, and
  `completed`.
- Rejects conflicting run/import identities, unsupported hashes or operation
  kinds, overlapping live operations for one run, and stale snapshot writers.
- Claims the operation slot before rechecking the stored snapshot, preventing a
  stale caller from overwriting a competing completed update.
- Requires every mutable write, including a legacy rewrite, to provide the exact
  predecessor payload hash.
- Allows exact replay of failed or completed operations when the caller retains
  and resubmits the same run payload. Live `running` operations remain protected
  from a second caller.
- Records exception class only. Raw transcript text, filenames, packet content,
  and exception messages are excluded from the journal and status response.
- Added `GET /api/storage/segmentation-operations` with incomplete filtering and
  bounded result limits.
- Added the segmentation journal to `GET /api/storage/schema-status`.
- Maps journal conflicts, stale snapshots, source-integrity conflicts, and
  unsupported newer schemas to HTTP 409 on segmentation mutation endpoints.

## Failure Proof

- Injected failures at source-blob storage, evidence cataloging,
  specialist-packet writes, snapshot writes, post-snapshot stage recording, and
  final completion recording.
- Verified that caught failures retain the last completed stage and exception
  class without persisting sensitive exception messages.
- Verified exact replay after each injected caught failure, including mutable
  operations where the target snapshot existed before the journal recorded that
  stage or completion.
- Forced a subprocess to exit after source-blob storage. A new process observed
  one `running` operation at `source_blob_stored` and no completed run snapshot.
- Started both identical attempts and distinct mutations concurrently. In each
  case one caller acquired the live attempt and the other received a domain
  conflict rather than a database-lock error.
- Simulated a competing snapshot update before a stale caller acquired its
  operation slot. The stale attempt failed and the winning snapshot remained
  unchanged.
- Verified the create-to-patch-to-verify payload-hash chain and guarded legacy
  rewrite journaling.
- Verified that distinctive transcript content and filenames did not appear in
  either the API response or any journal-table value.

## CLI Verification

- Segmentation journal, segmentation core, and API tests passed: 88/88.
- Complete backend suite passed: 194/194.
- Frontend production build passed.
- All frontend helper suites passed: 30/30.
- `git diff --check` passed.

## UI Verification

No researcher-facing control changed. The journal is currently an operator HTTP
contract. Existing UI behavior is protected by the production build and frontend
helper regression suites.

## Git Commits

- `91a688b Add segmentation operation journal schema`
- `adcb4d8 Journal segmentation persistence attempts`
- `79be8a5 Expose segmentation recovery operations`
- `4127b74 Close segmentation recovery race conditions`
- `eba17c0 Require guarded segmentation rewrites`
- `c51722c Replay applied segmentation targets`

## Known Limitations And Rollback

- This is a persistence-attempt journal, not an automatic recovery system. It has
  no resume worker, replay endpoint, startup scanner, or payload reconstruction.
- A hard-stopped operation remains `running` and blocks exact replay. A future
  lease, takeover, or administrative reconciliation path is required before that
  attempt can continue.
- The journal does not make the source blob, evidence catalog, specialist files,
  run snapshot, and journal one transaction. Earlier successful side effects can
  remain after a later failure; replay revalidates or rewrites those side effects.
- The journal is root-level and has no study ownership, authentication, or
  authorization boundary.
- `segmentation.sqlite3`, `segmentation_runs`, and specialist artifacts are not
  included in existing per-study project archives.
- The conflict tests cover the current shared-filesystem, single-host design.
  They do not establish multi-user, multi-host, lease, or distributed-write
  safety.
- Corpus summary persistence, segmentation export writes, agent jobs, study-batch
  persistence, and archive restore remain outside this journal.
- Phase 1 remains open. The project still cannot prove that all project data can
  be closed, reopened, backed up, and restored without loss.
- Reverting the pipeline and API integration restores the previous unjournaled
  segmentation path. Migration 1 is additive, so `segmentation.sqlite3` can
  remain inert. Back it up before deletion if its diagnostic history is needed.
