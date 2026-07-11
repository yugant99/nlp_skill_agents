# 2026-07-10 Phase 1 Source Revision Lineage

## Goal

Group immutable transcript revisions under project-owned source records and prove
parent/child ancestry without relying on filenames, run order, or mutable UI state.

## Files Changed

- `backend/evidence/identifiers.py`
- `backend/storage/evidence_catalog.py`
- `backend/analysis/pipeline.py`
- `backend/segmentation/pipeline.py`
- `backend/storage/local_store.py`
- `backend/storage/study_store.py`
- `backend/app/main.py`
- `frontend/src/types.ts`
- `tests/test_evidence_catalog.py`
- `tests/test_evidence_identifiers.py`
- `tests/test_pipeline_storage.py`
- `tests/test_segmentation_core.py`
- `tests/test_study_workspaces.py`
- `tests/test_api.py`
- `README.md`
- `checkpoints/README.md`
- `goals/psychology-research-platform-roadmap.md`

## Implemented

- Added generated `psrc_...` project-source IDs. Revisions can reuse an existing
  source only when the caller explicitly supplies that ID.
- Added `project_sources` and `source_revisions` catalog tables. Sources belong to
  a workspace; study-batch sources use the study ID and standalone analysis or
  segmentation sources use `local-default`.
- A child revision records `parent_transcript_revision_id`; its parent must already
  belong to the same project source and workspace.
- Reimporting an existing revision is allowed, but new transcript content under an
  existing project source must name a parent and cannot create a second root.
- Rejects missing parents, parents from another source, source/workspace conflicts,
  self-parenting, and attempts to rewrite established ancestry.
- Carries project-source, parent-revision, and workspace identity through analysis,
  study batches, segmentation runs, JSON evidence, SQLite run history, HTTP
  responses, and TypeScript contracts.
- Added `GET /api/evidence/sources/{project_source_id}` to inspect source ownership
  and ordered revision history.
- Text, file, and segmentation run APIs accept optional project-source and parent
  revision fields. Study text batches accept the same fields per transcript.
- Validates ancestry before creating run artifacts. Invalid analysis and
  segmentation revisions fail visibly and do not create result JSON.
- Migrates pre-feature import catalogs by assigning every old import its own
  deterministic `psrc_legacy_...` source with no parent. This preserves evidence
  without inventing relationships that were never recorded.

## CLI Verification

- Catalog identity, lineage, conflict, and migration tests passed: 6/6.
- Existing persistence and API compatibility slice passed: 67/67.
- Integrated analysis, study, segmentation, catalog, API, and migration slice
  passed: 76/76.
- `.venv/bin/pytest -q` passed: 140/140.
- `cd frontend && npm run build` passed.
- All frontend helper suites passed: 30/30.
- `git diff --check` passed for both implementation slices.

## UI Verification

No visual lineage editor was added. The contract is proven through standalone
analysis API revisions, study-batch revisions, segmentation-store revisions, the
source-history endpoint, persisted SQLite/JSON, the production frontend build, and
all frontend helper suites.

## Git Commits

- `168e47a Add project source revision lineage`
- `1804b88 Wire source lineage through research runs`
- `f79f679 Require parents for new source revisions`

## Known Limitations And Rollback

- The current contract records one parent per revision. It permits branches but
  does not model merge commits or multiple parents.
- The backend/API supports creating revisions, but the researcher UI does not yet
  expose source history or a revise-source action.
- `local-default` is a transitional workspace for standalone runs, not yet a full
  canonical project entity with ownership metadata.
- Existing imports are intentionally isolated into separate legacy sources. A
  researcher must explicitly reconcile them; automatic filename-based merging
  would fabricate provenance.
- Original source blobs are still not retained, only their hashes. Backup/restore
  integrity and canonical blob storage remain Phase 1 work.
- Catalog writes and run artifacts still span separate stores. Validation happens
  first, but a later I/O failure can leave a catalog record without its run
  artifact; transactional workflow recovery remains open.
- Reverting the two implementation commits removes lineage capture. The additive
  tables and fields can remain without affecting older readers.
