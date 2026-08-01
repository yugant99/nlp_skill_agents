# 2026-08-01 Phase 1 Versioned Codebook Service

## Goal

Turn the accepted qualitative codebook schema into a working, attributable
backend and API workflow without adding a parallel database, weakening frozen
research records, or starting the Phase 2 editor.

## Files Changed

- `backend/qualitative/database.py`
- `backend/qualitative/codebooks.py`
- `backend/qualitative/__init__.py`
- `backend/app/main.py`
- `tests/test_qualitative_database.py`
- `tests/test_qualitative_codebooks.py`
- `tests/test_api.py`
- `docs/architecture/codebook-service-design.md`
- `README.md`
- `goals/psychology-research-platform-roadmap.md`
- `checkpoints/README.md`

## Implemented

- Added an explicit, idempotent first-researcher bootstrap endpoint for a fresh
  study without introducing an anonymous/default identity or broader account
  management.
- Added immutable codebook, version, code, and snapshot records plus a domain
  service for create/list, first draft, code add/update/move, freeze, later-draft
  derivation, complete version reads, and portable JSON import/export.
- Required an active project researcher for every accepted mutation and appended
  one attributable audit event in the same immediate SQLite transaction.
- Kept `stable_code_key` immutable within a draft and preserved it across derived
  versions while generating new version-local code IDs and rebuilding parent
  links.
- Added deterministic depth-first hierarchy ordering and rejected missing,
  cross-version, self, indirect-cycle, duplicate-key, empty-freeze, frozen-state,
  and corrupt stored hierarchy conditions.
- Fully validated portable documents before opening a write transaction. Imports
  require the exact format/version and research-content shape, distrust supplied
  database/actor identity, generate new IDs, and always create a separately
  attributable version-1 draft.
- Added a guarded, query-only qualitative read boundary and hardened both reads
  and writes against symlinked/non-regular paths, exact schema/table/index/trigger
  drift, integrity and foreign-key failures, and foreign project ownership.
- Added study-scoped FastAPI endpoints with explicit response envelopes and stable
  `400` domain-validation, `404` missing-state, `409` conflict/integrity, and `422`
  structural-request behavior.

## Failure And Recovery Proof

- Rejected alternate bootstrap researcher IDs without adding a researcher or
  audit row, while preserving exact bootstrap retries even after a future second
  legitimate researcher exists.
- Proved audit-insertion failure rolls back the paired domain mutation.
- Rejected invalid import formats, versions, source metadata, duplicate keys,
  missing parents, cycles, booleans as integers, and non-string examples before a
  transaction or partial codebook write.
- Rejected boolean, string, and floating-point `sort_order` values structurally
  on both code creation and update instead of accepting Pydantic coercion.
- Rejected direct and indirect hierarchy cycles, cross-version parents, duplicate
  stable keys, empty freezes, draft mutation after freeze, and derivation from a
  non-frozen source.
- Detected corrupt persisted cycles, malformed examples JSON, and BLOB research
  labels/titles instead of coercing them into fabricated text.
- Rejected dropped and unexpected tables, indexes, or triggers, foreign-key
  violations, a foreign project binding, a symlinked database, and a symlinked
  study directory. The symlinked-study regression proves neither the qualitative
  database nor the study-batch journal is created outside the workspace.
- Returned content-safe API conflicts for a newer or structurally tampered
  qualitative database rather than exposing SQLite/storage details.

## API Boundary

- `PUT /api/studies/{study_id}/qualitative/project`
- `POST|GET /api/studies/{study_id}/qualitative/codebooks`
- `POST /api/studies/{study_id}/qualitative/codebooks/import`
- `POST /api/studies/{study_id}/qualitative/codebooks/{codebook_id}/versions`
- `GET /api/studies/{study_id}/qualitative/codebooks/{codebook_id}/versions/{codebook_version_id}`
- `GET .../{codebook_version_id}/export`
- `POST .../{codebook_version_id}/codes`
- `PUT .../{codebook_version_id}/codes/{code_id}`
- `POST .../{codebook_version_id}/freeze`

## CLI Verification

- Focused qualitative database/service/API suite passed: 54/54.
- Complete backend suite passed: 442/442.
- Frontend production build passed with 1,710 modules transformed.
- All eight frontend helper suites passed: 30/30.
- Python compilation passed for the database, service, and FastAPI modules.
- Ruff passed for every changed Python file.
- `git diff --check` passed.

## Review

- The design contract was independently reviewed for architecture/migration,
  API/coverage, and archive/security concerns before implementation.
- The integrated runtime diff was cross-reviewed by the same three specialties.
  All findings were fixed and all three reviewers approved the final runtime tree.

## Known Limitations And Rollback

- This slice has no codebook editor UI. Phase 2 researcher-facing editing,
  selection, manual coding, undo, stripes, retrieval, and search remain open.
- Bootstrap establishes only the first named local researcher. Windows identity,
  researcher administration, roles/authorization, and cryptographic audit
  integrity remain later production work.
- Cases, typed attributes, coding references, memos, annotations, saved queries,
  reliability/adjudication, agent proposals, and affected-coding review remain
  separate slices.
- There is no codebook deletion, draft merge, or single-active-draft policy. A
  later draft may branch from any frozen version and receives the next project
  version number.
- Portable round-trip equality covers normalized research content, hierarchy,
  stable keys, and ordering. Import intentionally replaces database IDs, actors,
  timestamps, source status, and source version number.
- Reverting the service/API leaves migration 1 and any accepted codebook rows in
  `qualitative.sqlite3`. Preserve the study database or a verified project archive
  before running older code that does not understand the service workflow.

## Git Commits

- `8fcd89b Define versioned codebook service contract`
- `b3d86bf Implement versioned codebook workflows`
