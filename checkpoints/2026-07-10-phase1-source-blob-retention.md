# 2026-07-10 Phase 1 Source Blob Retention

## Goal

Retain the original bytes behind every new import and detect mismatch or
corruption before treating those bytes as research evidence.

## Files Changed

- `backend/storage/atomic.py`
- `backend/storage/source_blob_store.py`
- `backend/analysis/pipeline.py`
- `backend/segmentation/pipeline.py`
- `backend/storage/local_store.py`
- `backend/storage/study_store.py`
- `backend/app/main.py`
- `tests/test_source_blob_store.py`
- `tests/test_pipeline_storage.py`
- `tests/test_segmentation_core.py`
- `tests/test_study_workspaces.py`
- `tests/test_api.py`
- `README.md`
- `checkpoints/README.md`
- `goals/psychology-research-platform-roadmap.md`

## Implemented

- Added atomic binary writes using same-directory temporary files, file sync,
  atomic replacement, and POSIX directory sync.
- Stores source blobs under
  `source_blobs/sha256/<first-two-hex>/<full-sha256>.blob` inside the configured
  local data root.
- Verifies incoming bytes against the recorded hash before writing.
- Rehashes an existing blob before deduplicating a repeated import.
- Rehashes every blob returned by the storage reader and fails visibly on
  corruption, invalid hash syntax, or expected-hash mismatch.
- Retains exact uploaded TXT and DOCX container bytes. Pasted, generated, and
  synthetic content is retained as its exact UTF-8 input bytes.
- Integrated retention into standalone analysis, study-batch, and segmentation
  ingestion without adding raw source bytes to API or JSON response payloads.
- Added `GET /api/evidence/blobs/{source_blob_sha256}/verify`, which returns only
  digest, verification status, and byte size; it does not return source content.
- Legacy records with no truthful original-source hash remain unstored rather than
  receiving reconstructed or fabricated blobs.

## CLI Verification

- Atomic and blob integrity tests passed: 7/7.
- Integrated source retention, study, segmentation, storage, and API slice passed:
  72/72.
- `.venv/bin/pytest -q` passed: 142/142.
- `cd frontend && npm run build` passed.
- All frontend helper suites passed: 30/30.
- `git diff --check` passed for both implementation slices.

## UI Verification

No visual control changed. Exact TXT and DOCX bytes, pasted text, study sources,
and segmentation sources were proven through storage reads and the verification
endpoint. Existing frontend behavior was protected by the production build and
all frontend helper suites.

## Git Commits

- `ea796db Add verified source blob storage`
- `dca27ad Retain source blobs across ingestion`

## Known Limitations And Rollback

- Blobs are local and unencrypted at the application layer. Device encryption,
  restrictive permissions, and encrypted backup policy remain production work.
- There is no deletion, retention, withdrawal, or orphan-garbage-collection
  workflow yet. Content-addressed deduplication can retain bytes referenced by
  multiple imports.
- Blob retention and catalog/run-artifact writes are not one transaction. A later
  failure can leave a valid orphan blob, which is safer than missing evidence but
  requires reconciliation tooling.
- No upload-size budget or disk-capacity guard exists yet.
- Historical records without an original blob hash cannot be backfilled safely and
  must be re-imported.
- The API verifies blobs but intentionally does not offer a raw download endpoint.
- Reverting the two implementation commits stops new blob retention. Existing
  content-addressed blobs are inert and can remain until an explicit retention or
  deletion workflow removes them.
