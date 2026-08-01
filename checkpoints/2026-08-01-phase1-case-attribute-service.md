# 2026-08-01 Phase 1 Case And Typed-Attribute Service

## Goal

Turn the accepted qualitative case tables into an attributable backend and API
workflow without changing migration 1, duplicating evidence content, or silently
replacing the existing JSON `StudySchema` and casebook CSV behavior.

## Files Changed

- `backend/qualitative/cases.py`
- `backend/qualitative/__init__.py`
- `backend/app/main.py`
- `backend/storage/project_archive.py`
- `tests/test_qualitative_cases.py`
- `tests/test_api.py`
- `tests/test_project_archive.py`
- `docs/architecture/case-attribute-service-design.md`
- `README.md`
- `goals/psychology-research-platform-roadmap.md`
- `checkpoints/README.md`

## Implemented

- Added immutable public records and a project-scoped `CaseService` for creating,
  updating, reading, and deterministically listing participant, session, dyad,
  condition, and timepoint cases.
- Added immutable create/list attribute definitions for exact text, number,
  Boolean, calendar-date, and categorical values. Category choices are trimmed,
  non-empty, unique, and exact; non-categorical definitions reject choices.
- Added set, focused read, replace, and clear operations with deterministic compact
  JSON storage and strict validation on both writes and reads. Text whitespace is
  preserved; numbers reject booleans, subclasses, non-finite floats, and coercion.
- Required a named active project researcher for every accepted mutation,
  canonicalized the actor identity once, and appended one privacy-minimized audit
  event in the same immediate qualitative transaction.
- Added project-source link/unlink operations that validate exact ownership through
  `EvidenceCatalog.source_history` while holding only the workspace lock, release
  it before the qualitative transaction, and store only the stable source ID.
- Made exact same-actor source-link retries idempotent. A different actor conflicts;
  missing clear/unlink targets fail visibly.
- Added a non-mutating `validate_project_state()` boundary for archive restore. It
  validates every case-domain row, releases the qualitative read guard, and then
  validates every distinct link against the staged evidence catalog.
- Added ten FastAPI routes with exact response envelopes, strict request structure,
  body-transported external source IDs, canonical deletion acknowledgements, and
  content-safe `400`, `404`, `409`, and `422` behavior.
- Preserved legacy archives without a qualitative database. Archives that include
  one now reject newer schema versions, malformed typed rows, and missing or
  foreign linked sources before destination preflight or publication.

## Failure And Recovery Proof

- Covered all five case kinds and all five value types, including huge integers,
  Boolean-as-number rejection, non-finite floats, impossible dates, unknown
  categories, duplicate choices, empty choices, and duplicate attribute keys.
- Rejected malformed, oversized, deeply nested, non-canonical, or wrong-type stored
  JSON as content-safe conflicts instead of coercing or leaking research data.
- Rejected corrupt stored case, definition, value, researcher, and link fields,
  including non-exact SQLite Boolean integers and foreign project ownership.
- Proved audit insertion failure rolls back each paired domain mutation.
- Proved padded internal actor and route IDs resolve to the same canonical identity,
  while external source IDs remain exact and are never normalized through a path.
- Contained evidence database and complete workspace-lock lifecycle failures without
  exposing file paths, SQLite details, source content, labels, values, or category
  choices.
- Proved archive round-trip of a real case, numeric value, and source link, plus
  pre-publication rejection for missing/foreign sources, corrupt stored values,
  and newer qualitative schemas.

## API Boundary

- `POST|GET /api/studies/{study_id}/qualitative/cases`
- `GET|PUT /api/studies/{study_id}/qualitative/cases/{case_id}`
- `POST|GET /api/studies/{study_id}/qualitative/attribute-definitions`
- `PUT|DELETE /api/studies/{study_id}/qualitative/cases/{case_id}/attributes/{attribute_definition_id}`
- `PUT|DELETE /api/studies/{study_id}/qualitative/cases/{case_id}/sources`

## CLI Verification

- Focused case-service suite passed: 42/42.
- Integrated qualitative database, codebook, case, API, and archive slice passed.
- Complete backend suite passed: 493/493.
- Complete project-archive module passed.
- Frontend production build passed with 1,710 modules transformed.
- All eight frontend helper suites passed: 30/30.
- Python compilation passed for every changed Python file.
- Ruff passed for every changed Python file.
- `git diff --check` passed.

## Review

- The design contract was independently reviewed for schema/migration and journal
  ordering, API/coverage, and archive/security behavior before implementation.
- Independent runtime reviews found and closed unsafe raw missing-file detail,
  non-canonical deletion acknowledgements, huge-integer finiteness overflow,
  uncontained pathological stored JSON, workspace-lock lifecycle leakage, and
  inconsistent padded-actor persistence/retry behavior.
- The final core, API, and archive trees received independent approval after the
  corrections and focused regressions.

## Known Limitations And Rollback

- Attribute definitions are intentionally immutable and create/list only. Editing
  or deleting a definition needs a separate compatibility and stored-value plan.
- There is no case deletion, relationship graph, completeness state, bulk import,
  or UI editor in this slice.
- The relational case service and legacy JSON `StudySchema` coexist without
  projection or dual writes. A later migration must define stable identity,
  conflicts, attribution, rollback, and ownership before either replaces the
  other.
- Source ownership validation spans two databases rather than one transaction.
  Current source IDs are immutable and have no deletion workflow; the later source
  lifecycle slice must reconcile or block deletion against qualitative links.
- Windows identity, role-based authorization, authenticated audit integrity,
  retention, deletion, withdrawal, and encrypted backups remain later production
  work.
- No schema migration was added. Reverting the service/API leaves accepted rows in
  migration-1 tables; preserve `qualitative.sqlite3` or a verified project archive
  before running older code that cannot expose those rows.

## Git Commits

- `1f6b1a8 Define cases and typed attributes contract`
- `cff7be0 Implement cases and typed attributes`
- `980c4f4 Expose cases and typed attributes API`
- `53c625f Validate qualitative state during restore`
- `0647d9a Update qualitative platform status`
