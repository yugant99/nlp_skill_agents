# 2026-07-28 Phase 1 Qualitative Core Contract

## Goal

Establish one executable, per-study persistence and provenance contract before
separate contributors implement the codebook and case/attribute subsystems.

## Files Changed

- `backend/qualitative/__init__.py`
- `backend/qualitative/database.py`
- `backend/app/main.py`
- `tests/test_qualitative_database.py`
- `tests/test_api.py`
- `docs/architecture/qualitative-core-contract.md`
- `assignments/rahfay-codebook-subsystem.md`
- `assignments/mark-case-attribute-subsystem.md`
- `README.md`
- `goals/psychology-research-platform-roadmap.md`
- `checkpoints/README.md`

## Implemented

- Added `qualitative.sqlite3` inside each existing study directory so accepted
  qualitative metadata shares one transaction boundary and is automatically
  included by the existing project archive.
- Added a transactional migration baseline for project identity, researchers,
  codebooks, immutable codebook versions, hierarchical codes, cases, typed
  attribute definitions and values, source-case links, and qualitative audit
  events.
- Added stable entity-ID generation and required explicit named-researcher
  initialization with conflict detection and an idempotent initialization event.
- Added transaction handling with foreign-key enforcement, immediate writer
  locking, commit, and rollback.
- Added database triggers that prevent changes to frozen codebook versions and
  prevent updates or deletion of append-only audit events. The schema also
  rejects attempts to bind one per-study database to a second project identity.
- Closed an update path that could otherwise move a draft code into a frozen
  version, and now rejects a reused initialization-event ID with conflicting
  content.
- Avoided `sqlite3.executescript` so migration DDL stays inside the migration
  engine's explicit transaction.
- Added a per-study schema compatibility endpoint with missing-study and
  unsupported-newer-schema errors.
- Recorded the shared identifier, evidence-link, provenance, mutation, migration,
  API, backup, and contributor ownership decisions.
- Added separate implementation packets addressed to Rahfay and Mark without
  requiring API keys, model access, or sensitive research data.

## Verification

- Qualitative database focused tests passed: 8/8.
- Qualitative schema-status API tests passed: 2/2.
- `.venv/bin/pytest` passed: 168/168.
- `cd frontend && npm run build` passed.
- All frontend helper suites passed: 30/30.
- `git diff --check` passed for the complete branch diff.
- `npm audit` reported three pre-existing frontend toolchain advisories: one low
  and two high across esbuild, PostCSS, and Vite. This branch does not change the
  dependency manifests or lockfile; upgrades remain a separate reviewed slice.

## Git Commits

- `fd8d65b Add qualitative project schema contract`
- `70d4c6a Expose qualitative schema compatibility`
- `78edf9a Document qualitative architecture assignments`
- `d878ae0 Enforce qualitative schema boundaries`
- `8bcd256 Close qualitative immutability bypasses`

## Known Limitations And Rollback

- The baseline exposes schema and transaction contracts, not codebook or case CRUD
  services and not researcher-facing controls.
- `source_case_links` cannot have a SQLite foreign key into the separate evidence
  database. The case service must validate source ownership through
  `EvidenceCatalog` before writing a link.
- Researcher records establish attribution but are not yet backed by Windows
  identity or role authorization.
- Qualitative audit events are append-only but not authenticated or
  cryptographically chained.
- The existing frontend dependency audit is not clean. Toolchain upgrades and
  their Windows regression checks remain separate production-readiness work.
- The current JSON `StudySchema` remains active. Mark's assigned design must define
  compatibility before any replacement or migration.
- Codebook hierarchy-cycle and typed-value validation are service responsibilities
  assigned to the next slices.
- Reverting this feature removes schema initialization and compatibility reporting.
  Existing per-study `qualitative.sqlite3` files remain in study directories and
  backups. Restore a pre-feature project archive before running older code if the
  file must be removed.
