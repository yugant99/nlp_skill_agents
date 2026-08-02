# 2026-08-01 Phase 1 Memo And Annotation Service

## Goal

Make study-, source-, case-, code-, and excerpt-linked memos and annotations
durable, revisioned, attributable, auditable, and portable through backend and
API operations without changing applied migrations or starting the Phase 2
manual authoring workbench.

## Files Changed

- `backend/qualitative/database.py`
- `backend/qualitative/notes.py`
- `backend/storage/project_archive.py`
- `backend/app/main.py`
- `tests/test_qualitative_database.py`
- `tests/test_qualitative_notes.py`
- `tests/test_qualitative_cases.py`
- `tests/test_qualitative_coding_references.py`
- `tests/test_api.py`
- `tests/test_project_archive.py`
- `docs/architecture/memo-annotation-service-design.md`
- `README.md`
- `goals/psychology-research-platform-roadmap.md`
- `checkpoints/README.md`

## Implemented

- Added only qualitative migration 3,
  `add-memo-annotation-contract`; migrations 1 and 2 remain unchanged.
- Added shared `qualitative_notes` and append-only
  `qualitative_note_revisions` tables with exact nullable target shapes,
  restrictive local relations, frozen-code enforcement, immutable headers,
  sequential revisions, and one-way attributable tombstones.
- Added stable `mem_`, `ann_`, and `nrv_` identities with strict stored kind and
  prefix agreement.
- Added a project-scoped `NoteService` for create, strict read, deterministic
  bounded list, compare-and-append revision, bounded revision history, removal,
  and whole-project validation.
- Bound every note to exactly one study, project source, case, frozen
  code/version pair, or canonical evidence-set passage/C-unit span. Excerpt text
  and hashes are never copied into the qualitative database or audit log.
- Preserved exact researcher-authored body text while applying one explicit
  memo-title strip operation. NUL/lone-surrogate content, blank or oversized
  fields, and a project total above 256 MiB of exact UTF-8 note bytes fail
  visibly.
- Appended one canonical privacy-minimized audit event in the same immediate
  qualitative transaction as each accepted create, revision, or removal.
  Exact retries are idempotent; stale, divergent, malformed, or unmatched audit
  state conflicts.
- Kept workspace and study locks non-overlapping. External targets are validated
  outside qualitative reads/transactions, while a final immediate transaction
  re-reads local state before revision.
- Added strict keyset pagination with a default of 20 and maximum of 50 complete
  snapshots/revisions per response.

## API Boundary

- `POST|GET /api/studies/{study_id}/qualitative/memos`
- `GET|DELETE /api/studies/{study_id}/qualitative/memos/{memo_id}`
- `POST|GET /api/studies/{study_id}/qualitative/memos/{memo_id}/revisions`
- `POST|GET /api/studies/{study_id}/qualitative/annotations`
- `GET|DELETE /api/studies/{study_id}/qualitative/annotations/{annotation_id}`
- `POST|GET /api/studies/{study_id}/qualitative/annotations/{annotation_id}/revisions`

All request structures forbid extra fields and use strict target discriminators.
Route-scoped validation and domain mapping return content-safe 422, 400, 404,
or 409 responses without echoing note content, evidence text, paths, hashes,
SQL, or raw stored values.

## Archive And Recovery Proof

- Project archive format remains version 2. The existing study database member
  carries note headers, revisions, attribution, tombstones, and audit events;
  canonical excerpt text remains in the evidence target/blob closure.
- Archive capture and restore invoke complete note validation only against an
  isolated captured/staged root before publication.
- Format 1 accepts study/source/case/code notes but directly rejects any populated
  qualitative-note evidence-set column. Researcher body text containing the
  literal `evidence_set_id` is not misclassified as a target reference.
- Rehashed audit tampering fails before destination mutation. Concurrent archive
  versus create, revise, and remove captures the complete domain+audit mutation
  or none, without deadlock.
- Format-2 restore preserves all five target kinds, every exact revision field,
  every note audit row, and removal state.

## CLI Verification

- Migration plus note-service suites passed: 67/67; the final note suite passed
  40/40.
- Full API suite passed: 94/94.
- Full archive suite passed: 145/145.
- The combined affected database, note, case, coding-reference, API, and archive
  regression passed: 382/382.
- Complete backend suite passed: 657/657.
- Frontend production build passed with 1,710 modules transformed.
- All eight frontend helper suites passed: 30/30.
- Ruff, Python compilation, and `git diff --check` passed.

## UI Verification

This Phase 1 slice intentionally adds no researcher-facing UI. The existing
frontend passed its production build and every documented helper suite.

## Review

- Three independent discovery reviews froze canonical targets, migration 3,
  API envelopes, lock sequences, audit metadata, archive behavior, and the
  Phase 1/2 boundary before schema or runtime implementation.
- Contract review found and closed ambiguous envelopes, contradictory lock
  wording, HTTP boundary conflicts, unbounded full-content responses, and an
  incomplete format-1 compatibility rule before implementation began.
- Runtime review found and closed a malformed audit-event discovery bypass and
  added synchronized divergent-revision plus strict stored-corruption proofs.
- Archive review isolated the version-1 note-row proof, required independent
  note-validator lock instrumentation, exact history/audit equality, and distinct
  create/revise/remove concurrency coverage before approval.
- Final pre-landing review found and closed full-history pagination
  materialization, BLOB/padded unmatched-audit restore-preflight bypass,
  deactivated exact-retry ordering, wrong-kind cursor status, and missing
  wrong-version/foreign/distinct-evidence-set proof. The amended bounded-read
  contract and complete corrective diff were independently re-reviewed after all
  focused and regression gates.

## Known Limitations And Rollback

- The Phase 2 authoring and selection UI, rich-text presentation, search,
  keyboard undo, coding stripes, and target-specific retrieval interactions are
  not part of this slice.
- Local researcher IDs provide attribution, not authenticated user identity or
  role-based authorization. The audit log is not cryptographically chained.
- Archives are local, unsigned, and unencrypted. The 256 MiB note-content budget
  reserves room below the 512 MiB archive member ceiling, but other qualitative
  state can still make an archive exceed global limits and fail visibly.
- This slice adds no agent-authored notes, reviewer decisions, saved queries,
  exports, provider calls, source-retention policy, or affected-note lifecycle
  reconciliation.
- Reverting runtime code leaves qualitative migration 3 and accepted note rows
  that older code cannot understand. Preserve a verified format-2 project archive
  before running older code.

## Git Commits

- `f0ec85f Define memo and annotation service contract`
- `a24ee84 Implement durable memo and annotation services`
- `2b11382 Preserve qualitative notes in project archives`
- `e24a008 Harden qualitative note pagination and recovery`
- `6851c38 Stream qualitative note project validation`
