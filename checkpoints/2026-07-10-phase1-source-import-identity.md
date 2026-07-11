# 2026-07-10 Phase 1 Source Import Identity

## Goal

Distinguish the original imported blob from extracted transcript content and make
each ingestion independently attributable without changing stable transcript,
passage, or C-unit identity.

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

- Every analysis, study-batch, segmentation, and synthetic-corpus ingestion gets
  a distinct `imp_...` import ID.
- `source_blob_sha256` covers the original uploaded TXT or DOCX bytes. Pasted and
  generated sources hash the exact UTF-8 bytes supplied to the pipeline.
- `source_media_type` records the upload media type or the local text default.
- The existing `transcript_sha256` remains separate and continues to identify the
  exact extracted transcript text used for analysis.
- Added `evidence.sqlite3` with canonical `source_imports` and deduplicated
  `transcript_revisions` tables plus a revision lookup index.
- Import metadata flows through run objects, JSON artifacts, SQLite run history,
  study-batch details and summaries, segmentation evidence, HTTP responses, and
  TypeScript contracts.
- Added `GET /api/evidence/imports` so the local import catalog is inspectable.
- Re-recording the exact same import is idempotent. Reusing an import or revision
  ID with conflicting identity fields fails visibly instead of rewriting history.
- Existing `analysis_runs` databases gain the additive import columns in place.
  Legacy segmentation and study-batch artifacts expose unknown/empty import
  evidence rather than fabricating an original-blob hash.

## CLI Verification

- Analysis, study, catalog, identifier, and API slice passed: 55/55.
- Segmentation, study, catalog, identifier, and API slice passed: 67/67.
- Catalog immutability and persistence slice passed: 59/59.
- `.venv/bin/pytest -q` passed: 135/135.
- `cd frontend && npm run build` passed.
- All frontend helper suites passed: 30/30.
- `git diff --check` passed for each implementation slice.

## UI Verification

No visual control changed. The import contract is proven through upload and paste
HTTP paths, the inspectable evidence-catalog endpoint, persisted JSON and SQLite,
study-batch drilldowns, segmentation evidence exports, the production frontend
build, and all frontend helper suites.

## Git Commits

- `af487b3 Catalog original analysis imports`
- `dbc13d7 Catalog segmentation source imports`
- `5650645 Reject evidence identity rewrites`

## Known Limitations And Rollback

- The catalog stores the original blob hash and media type, not a retained copy of
  the original blob. Immutable source-blob storage and restore verification remain
  Phase 1 work.
- `source_id` is still content-addressed from extracted transcript text. A
  project-owned source record and parent/child transcript-revision lineage are not
  yet implemented.
- Media type comes from the local upload client and is not independently detected.
- Import catalog writes and run artifact writes use separate stores, so a process
  failure between them can leave one complete side without the other. A later
  transactional workflow boundary must reconcile this.
- Pre-feature artifacts cannot recover original bytes. Legacy study batches and
  segmentation runs therefore keep empty hashes and `unknown` media type until
  re-imported.
- The import-catalog endpoint is designed for the current loopback-only topology;
  it must be access-controlled before any LAN or multi-user deployment.
- Reverting the three implementation commits removes import capture and catalog
  writes. The additive SQLite columns and `evidence.sqlite3` can remain without
  affecting older readers.
