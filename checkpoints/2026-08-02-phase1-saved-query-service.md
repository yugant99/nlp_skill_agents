# 2026-08-02 Phase 1 Saved-Query Service

## Goal

Persist attributable, immutable qualitative query definitions with strict storage,
API, audit, pagination, and archive validation without executing searches, adding a
researcher-facing workbench, or starting provider inference.

## Files Changed

- `backend/qualitative/database.py`
- `backend/qualitative/saved_queries.py`
- `backend/qualitative/__init__.py`
- `backend/storage/project_archive.py`
- `backend/app/main.py`
- `tests/test_qualitative_database.py`
- `tests/test_qualitative_cases.py`
- `tests/test_qualitative_coding_references.py`
- `tests/test_saved_queries.py`
- `tests/test_api.py`
- `tests/test_project_archive.py`
- `docs/architecture/saved-query-service-design.md`
- `README.md`
- `goals/psychology-research-platform-roadmap.md`
- `checkpoints/README.md`

## Implemented

- Added only qualitative migration 5, `add-saved-query-contract`; migrations 1
  through 4 remain unchanged.
- Added immutable caller-identified `qry_` records for one versioned
  `coding_reference_filter` definition. Every definition stores the full five-key
  filter shape and a canonical request digest, but no query result, source text,
  prompt, model response, or unrestricted content.
- Added exact-retry create, strict read, deterministic bounded list, canonical
  project/filter-bound cursors, and complete project-state validation.
- Required an active attributable researcher for a new create while allowing an
  exact retry after actor deactivation or project capacity is reached.
- Revalidated the complete saved-query family, local code/researcher relations,
  source ownership, canonical storage bytes, request digests, and exactly one
  privacy-minimized audit event at every public read boundary.
- Committed each new query and its `saved_query.created` audit atomically in one
  immediate qualitative transaction. Concurrent identical requests converge;
  divergent identity reuse conflicts.
- Rejected hidden corrupt rows, malformed SQLite types, over-capacity stored
  families, unmatched or content-bearing audits, unavailable stored sources, and
  malformed or noncanonical pagination cursors.

## API Boundary

- `POST /api/studies/{study_id}/qualitative/saved-queries`
- `GET /api/studies/{study_id}/qualitative/saved-queries`
- `GET /api/studies/{study_id}/qualitative/saved-queries/{saved_query_id}`

Create bodies and nested definitions require every key, reject extra fields and
coercive primitives, and never return `request_sha256` or raw `filters_json`.
Repeated or unknown scalar query parameters are a scrubbed 422. Domain failures
map to fixed content-safe 400, 404, or 409 details.

## Archive And Recovery Proof

- Archive format remains 2; saved queries remain inside the existing
  `study/qualitative.sqlite3` member.
- Archive creation and staged restore invoke complete saved-query validation only
  against the isolated captured or staged root after reconstructing the evidence
  catalog.
- A format-1 archive directly rejects any populated `saved_queries` table without
  scanning free-form JSON. An empty or absent table remains compatible and is
  migrated normally.
- A rehashed database with a structurally valid but digest-divergent saved query is
  rejected before destination mutation without returning its private title.
- A concurrent archive versus saved-query create contains both the row and its
  audit or contains neither.
- Backup and restore APIs now translate archive conflicts and invalid state to
  fixed generic messages rather than returning raw exception, schema, path, hash,
  title, or filter content.

## CLI Verification

- Focused migration, saved-query, API, and archive suites passed: 341/341.
- Complete backend suite passed: 752/752.
- Frontend production build passed with 1,710 modules transformed.
- All eight frontend helper suites passed: 30/30.
- Ruff passed for every Python file changed by this slice. Python compilation and
  `git diff --check` passed.

## UI Verification

This Phase 1 slice intentionally changes no researcher-facing UI. Query execution,
search results, filter interaction, and saved-query management belong to the Phase
2 manual workbench.

## Review

- Read-only source, revision, passage/C-unit, API, migration/journal, and
  archive/security discovery preceded the design contract.
- Three independent contract reviews closed retry/collision ordering, exact digest
  serialization, cursor bounds and closure, API wire fields, archive error privacy,
  and format-1 compatibility before migration or runtime work.
- Service review found and fixed filtered-out foreign rows, deep-JSON exception
  leakage, missing concurrency proof, exact-retry source disappearance, audit
  pairing gaps, cursor adversarial gaps, and over-capacity stored-family handling.
- API review found and fixed an unbounded all-digit `limit` conversion that could
  otherwise escape as a 500.
- Final archive review approved staged-root validation and lock ordering. Final
  contract review found and fixed a vacuous tamper fixture by recreating the exact
  immutable trigger, proving saved-query digest validation receives the forged row.
- The exact post-fix focused state passed 341 tests and all final independent
  reviews have no remaining P0, P1, or P2 findings.

## Known Limitations And Rollback

- This service stores definitions only. It does not execute a coding-reference
  search, materialize results, or provide a search/saved-query UI.
- Researcher IDs are attributable local identities, not authenticated accounts or
  authorization roles. Audit records are not cryptographically chained.
- The recoverable qualitative-export entity is the next contiguous Phase 1
  migration and requires a cross-SQLite/filesystem recovery journal. No export row
  or artifact was added to migration 5.
- No provider, OpenRouter, model, network, retry, judge, or response-healing call
  occurs in this slice.
- Reverting runtime code leaves qualitative migration 5 and accepted saved-query
  rows that older code cannot understand. Preserve a verified format-2 archive
  before running older code.

## Git Commits

- `8415e9c Define saved query service contract`
- `d740420 Add saved query migration`
- `5695f7d Harden saved query migration proof`
- `a00eb13 Implement strict saved query service`
- `7fc1a2e Expose and archive saved queries`
