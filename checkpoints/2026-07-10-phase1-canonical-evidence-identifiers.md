# 2026-07-10 Phase 1 Canonical Evidence Identifiers

## Goal

Give current analysis and segmentation evidence stable identifiers that future
coding, memo, retrieval, review, and audit records can reference without depending
on a run ID, filename, or mutable UI position.

## Files Changed

- `backend/evidence/__init__.py`
- `backend/evidence/identifiers.py`
- `backend/analysis/pipeline.py`
- `backend/analysis/transcripts.py`
- `backend/app/main.py`
- `backend/storage/local_store.py`
- `backend/storage/study_store.py`
- `backend/segmentation/adjudicator.py`
- `backend/segmentation/descript.py`
- `backend/segmentation/models.py`
- `backend/segmentation/pipeline.py`
- `frontend/src/types.ts`
- `tests/test_evidence_identifiers.py`
- `tests/test_api.py`
- `tests/test_pipeline_storage.py`
- `tests/test_segmentation_core.py`
- `tests/test_study_workspaces.py`
- `README.md`
- `checkpoints/README.md`
- `goals/psychology-research-platform-roadmap.md`

## Implemented

- Added a shared content-addressed evidence identity contract with a transcript
  content SHA-256, source ID, transcript-revision ID, passage IDs, and C-unit IDs.
- Analysis runs, API responses, local result JSON, run history, study-batch run
  details, and study-batch summaries now carry source and revision identity.
- Parsed analysis turns and Descript segmentation events now carry passage IDs.
- Every counted C-unit candidate receives a stable C-unit ID. A coordinated turn
  that produces two candidates receives two distinct IDs under one passage ID.
- Identical transcript bytes produce the same evidence IDs across separate runs
  and filenames, while each execution keeps its own run ID.
- Existing `analysis_runs` SQLite databases gain the three identity columns in
  place before new runs are recorded.
- Legacy segmentation JSON derives missing source, revision, passage, and C-unit
  IDs during load and persists the current schema when rewritten.
- Legacy study-batch summaries remain readable and expose empty identity strings
  rather than inventing hashes from incomplete stored data.

## CLI Verification

- Shared identifier, analysis persistence, transcript, study-workspace, and API
  slices passed: 56/56.
- Shared identifier, segmentation, and API slices passed: 56/56.
- `.venv/bin/pytest -q` passed: 133/133.
- `cd frontend && npm run build` passed.
- All frontend helper suites passed: 30/30.
- `git diff --check` passed for both implementation slices.

## UI Verification

No new visual control was added. The additive identity fields are proven through
the HTTP API, persisted JSON, SQLite metadata, study-batch drilldowns, segmentation
evidence exports, and TypeScript response contracts. Existing frontend behavior
was protected by the production build and all frontend helper suites.

## Git Commits

- `4d7b3cc Add stable analysis evidence identifiers`
- `eeeffa8 Attach stable IDs to segmentation evidence`
- `2dc19cc Clarify transcript content hashing`

## Known Limitations And Rollback

- Source identity is content-addressed because the current product does not yet
  have a project-owned source registry. Two identical transcript-text inputs
  intentionally share an ID; any transcript-text change creates a new source and
  revision ID.
- `transcript_sha256` covers the exact text supplied to the analysis or
  segmentation pipeline. It is not a hash of the original TXT or DOCX file bytes;
  immutable source-blob storage and hashing remain open Phase 1 work.
- Revision ancestry, import-instance identity, filename history, and explicit
  replacement relationships still need canonical SQLite entities and migrations.
- Passage IDs are revision-and-ordinal based. Editing transcript bytes creates a
  new revision namespace rather than pretending old offsets still identify the
  same evidence.
- The public IDs use 128 bits of the SHA-256-derived value for readability while
  every record also carries the complete transcript-content SHA-256 for integrity
  checks.
- Pre-feature SQLite rows and legacy study-batch summaries have empty identity
  values because the original transcript bytes are not available there. They must
  be re-imported to obtain truthful content hashes.
- Analysis metadata is in SQLite, while segmentation artifacts remain JSON. This
  feature establishes the foreign-key values but not the Phase 1 transactional
  entity schema, backup, restore, or revision-lineage workflow.
- Reverting the two implementation commits removes the additive API fields. The
  migrated SQLite columns and additive JSON fields can remain without harming the
  older readers.
