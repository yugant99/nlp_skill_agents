# 2026-08-01 Phase 1 Coding-Reference Service

## Goal

Make exact passage and C-unit evidence durably codable through attributable backend
and API operations without copying source text into qualitative rows, modifying any
existing migration, or starting the Phase 2 manual workbench.

## Files Changed

- `backend/evidence/identifiers.py`
- `backend/storage/evidence_catalog.py`
- `backend/storage/evidence_target_registry.py`
- `backend/storage/evidence_text_blob_store.py`
- `backend/storage/local_store.py`
- `backend/storage/study_store.py`
- `backend/storage/project_archive.py`
- `backend/storage/source_blob_store.py`
- `backend/storage/workspace_lock.py`
- `backend/analysis/pipeline.py`
- `backend/segmentation/models.py`
- `backend/segmentation/pipeline.py`
- `backend/segmentation/adjudicator.py`
- `backend/qualitative/database.py`
- `backend/qualitative/coding_references.py`
- `backend/app/main.py`
- `frontend/src/types.ts`
- `tests/test_evidence_identifiers.py`
- `tests/test_evidence_catalog.py`
- `tests/test_evidence_target_registry.py`
- `tests/test_evidence_text_blob_store.py`
- `tests/test_pipeline_storage.py`
- `tests/test_segmentation_core.py`
- `tests/test_segmentation_operation_store.py`
- `tests/test_study_workspaces.py`
- `tests/test_qualitative_database.py`
- `tests/test_qualitative_cases.py`
- `tests/test_qualitative_coding_references.py`
- `tests/test_api.py`
- `tests/test_project_archive.py`
- `tests/test_workspace_lock.py`
- `docs/architecture/coding-reference-service-design.md`
- `README.md`
- `goals/psychology-research-platform-roadmap.md`
- `checkpoints/README.md`

## Implemented

- Added evidence-catalog migration 4, `add-canonical-evidence-targets`, while
  preserving migrations 1-3 byte-for-byte and forward-only.
- Added immutable evidence target-set manifests and content-addressed UTF-8 evidence
  text blobs. Manifests bind the producer contract, source/revision ownership,
  canonical passage/C-unit identity and ordinals, exact text digest and length, and
  deterministic evidence-set ID.
- Registered target sets during persisted analysis and study-scoped segmentation.
  Segmentation snapshots now retain canonical passage and counted C-unit identities
  so coding-eligible, current-lineage, pipeline-verified outputs can be referenced
  durably. This eligibility is not a claim of psychology-domain validation.
- Added qualitative migration 2, `add-coding-reference-contract`, without modifying
  migration 1. Coding rows are immutable except for one attributable removal
  tombstone and never contain transcript text.
- Added a project-scoped `CodingReferenceService` for apply, focused read,
  deterministic list, removal, and whole-project validation. References require an
  active researcher, exact persisted target, and exact code in a frozen codebook
  version.
- Revalidated stored rows, local relations, and audit history on every service
  boundary. Create/read/list and whole-project validation also revalidate external
  evidence targets; removal intentionally remains possible after external evidence
  loss. External validation covers canonical IDs, hashes, ordinals, producer
  semantics, ownership, and exact evidence-text bytes.
- Appended one privacy-minimized audit event in the same immediate qualitative
  transaction as each accepted coding mutation. Same-actor retries are idempotent;
  conflicting replays and missing removals fail visibly.
- Enforced one workspace lock order across study mutations, evidence/qualitative
  reads, archive capture, and restore preflight. Direct lock instrumentation and a
  concurrent archive/coding regression prove no lock inversion and no split
  row/audit outcome.
- Added privacy-safe API error mapping and strict request structures without echoing
  invalid research content or storage paths.

## API Boundary

- `POST|GET /api/studies/{study_id}/qualitative/coding-references`
- `GET|DELETE /api/studies/{study_id}/qualitative/coding-references/{coding_reference_id}`
- `POST|GET /api/studies/{study_id}/segmentation/runs`
- `GET /api/studies/{study_id}/segmentation/runs/{run_id}`
- `POST /api/studies/{study_id}/segmentation/runs/{run_id}/verify`
- `POST /api/studies/{study_id}/segmentation/runs/{run_id}/specialists/{specialist_id}/patches`

## Archive And Recovery Proof

- Advanced the emitted project archive to format 2 with an exact target registry and
  flat content-addressed evidence-text members. The reader accepts only exact
  versions 1 and 2.
- Isolated backup capture replays source blobs, imports, target sets, text blobs,
  audit events, study batches, skill packs, cases, and coding references before an
  archive is published.
- Restore validates every member and cross-store reference in staging, preflights
  exact destination compatibility, then publishes the study only after shared state
  can be replayed safely.
- Format-1 compatibility rejects all newer evidence-set references, including nested
  study JSON, qualitative rows, and audit metadata, instead of inventing history.
- Adversarial tests cover missing, extra, malformed, wrong-hash, self-consistently
  forged, symlinked, non-regular, oversized, duplicate-key, deeply nested, and
  non-finite archive/storage input.
- Failure injection before and after blob writes and final replacement proves exact
  rollback of database rows, audit events, blobs, directories, lock artifacts, and
  an originally absent destination root.

## CLI Verification

- Focused archive, source-blob, workspace-lock, and coding-reference suites passed:
  168/168.
- Complete backend suite passed: 596/596.
- Frontend production build passed with 1,710 modules transformed.
- All eight frontend helper suites passed: 30/30.
- Python compilation passed for `backend` and `tests`.
- `git diff --check` passed.

## UI Verification

- This Phase 1 slice intentionally changes no researcher-facing UI. The frontend
  passed its production build and all eight documented helper suites.

## Review

- The design contract received independent database/lock-order, API, migration,
  journal, archive, and security review before schema or runtime implementation.
- Independent runtime reviews found and closed request-validation content leakage,
  archive compatibility gaps, forged-target acceptance, lock inversion risk,
  symlink/non-regular lock handling, incomplete rollback registration, impossible
  removal chronology, and a recursion leak in legacy-archive target scanning.
- The final archive/security and direct lock-order reviews approved the corrected
  tree before a last whole-branch review expanded the focused adversarial gate to
  168 tests.
- A complete pre-landing branch review and the full backend/frontend regression gate
  were run before pull-request creation.

## Known Limitations And Rollback

- There is no researcher-facing source selection, coding editor, multiple-code
  interaction, undo, coding stripe, retrieval, memo, annotation, or search workflow.
- Coding targets are immutable accepted evidence sets. Source deletion, withdrawal,
  retention, and affected-coding reconciliation remain later Phase 1 lifecycle work.
- Researcher records are local project identities, not authenticated Windows users
  or an authorization system. Roles, audit integrity protection, encryption, and
  encrypted backups remain production work.
- This slice adds no provider calls, agent proposals, review decisions, or other
  Phase 4 behavior.
- Reverting the runtime leaves evidence migration 4, qualitative migration 2,
  evidence text, and accepted coding rows that older code cannot understand. Preserve
  a verified format-2 project archive before running older code.

## Git Commits

- `f87fca0 Document coding reference evidence contract`
- `7b467a6 Build immutable coding reference storage`
- `e782ae1 Register analysis evidence target sets`
- `9d24dc2 Expose study coding reference APIs`
- `40cf216 Preserve coding evidence in project archives`
