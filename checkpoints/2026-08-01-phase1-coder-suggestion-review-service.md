# 2026-08-01 Phase 1 Coder Suggestion And Reviewer-Decision Service

## Goal

Make local researcher identity, imported agent coding suggestions, and human
reviewer decisions durable, attributable, strictly validated, and portable
without starting the Phase 2 review workbench or the Phase 4 provider runtime.

## Files Changed

- `backend/qualitative/database.py`
- `backend/qualitative/research_reviews.py`
- `backend/storage/project_archive.py`
- `backend/app/main.py`
- `tests/test_qualitative_database.py`
- `tests/test_research_reviews.py`
- `tests/test_qualitative_cases.py`
- `tests/test_qualitative_coding_references.py`
- `tests/test_api.py`
- `tests/test_project_archive.py`
- `docs/architecture/coder-suggestion-review-service-design.md`
- `README.md`
- `goals/psychology-research-platform-roadmap.md`
- `checkpoints/README.md`

## Implemented

- Added only qualitative migration 4,
  `add-coder-suggestion-review-contract`; migrations 1 through 3 remain
  unchanged.
- Reused the canonical `researchers` table and classified researcher provenance
  as bootstrap, registered, or legacy-unverified. Registration is attributable,
  exact-retry idempotent, and limited to active existing actors.
- Added immutable `agent_coding_suggestions` with caller-supplied `ags_`
  identities, exact origin kind/id/key replay identity, canonical source,
  transcript-revision, evidence-set, passage/C-unit span, and frozen code.
- Limited Phase 1 suggestion origins to `synthetic_fixture` and
  `imported_agent_output`. The service performs no provider or network call and
  stores no prompt, rationale, confidence, model identity, latency, or raw model
  response.
- Added append-only `reviewer_decisions` with caller-supplied `rvd_` identities,
  expected decision numbers, `deferred` history, terminal
  `accepted|edited|rejected` outcomes, exact retries, and stale/concurrent append
  rejection.
- Required accepted and edited results to be pre-existing active coding
  references created by the reviewer. Accepted results match the suggestion
  exactly; edited results retain the same source/revision/evidence-set lineage
  while changing at least one candidate field. The review service never creates,
  removes, or changes a coding reference.
- Used three-stage read, external-target preflight, and final immediate-write
  transactions for suggestion creation and decision append. Final writes recheck
  every mutable local dependency after external validation.
- Added one canonical privacy-minimized qualitative audit event in the same
  transaction as each registration, suggestion, or decision. Strict reads reject
  missing, duplicate, padded, binary-marker, extra-key, content-bearing, or
  unmatched review audits.
- Added bounded, project/endpoint/filter-bound canonical cursors and streaming
  whole-project validation with fixed-width external-target accumulation.

## API Boundary

- `PUT|GET /api/studies/{study_id}/qualitative/researchers/{researcher_id}`
- `GET /api/studies/{study_id}/qualitative/researchers`
- `POST|GET /api/studies/{study_id}/qualitative/agent-suggestions`
- `GET /api/studies/{study_id}/qualitative/agent-suggestions/{suggestion_id}`
- `POST|GET /api/studies/{study_id}/qualitative/agent-suggestions/{suggestion_id}/decisions`

Request models reject extra fields and coercive primitive types. Conditional
decision-reference presence, including explicit null, is a structural 422.
Domain validation maps to content-safe 400, 404, or 409 responses without
echoing origin values, display names, evidence text, paths, hashes, SQL, or raw
stored values.

## Archive And Recovery Proof

- Archive format remains 2. Researcher rows, exact suggestion origins and
  candidates, complete decision history, decision-linked coding-reference
  tombstones, and qualitative audit rows round-trip through the existing
  qualitative database member.
- Archive creation and restore preflight invoke complete research-review
  validation only against the isolated captured or staged root.
- Format 1 directly rejects every populated agent-suggestion row, including a
  corrupt row whose evidence closure cannot be represented.
- Rehashed semantic SQLite tampering fails before destination mutation. A
  concurrent archive versus suggestion creation contains the suggestion and its
  audit together or contains neither.

## CLI Verification

- Research-review service suite passed: 18/18.
- Full qualitative migration, research-review, archive, and API group passed:
  299/299 across the individually rerun green suites.
- Full API suite passed: 96/96.
- Full archive suite passed: 149/149.
- Complete backend suite passed: 690/690.
- Frontend production build passed with 1,710 modules transformed.
- All eight frontend helper suites passed: 30/30.
- Ruff passed for every Python file changed by this branch. Python compilation
  and `git diff --check` passed. Repository-wide Ruff still reports three
  pre-existing findings in files unchanged by this branch.

## UI Verification

This Phase 1 slice intentionally adds no researcher-facing UI. The existing
frontend passed its production build and every documented helper suite.

## Review

- Three independent discovery reviews froze the canonical source/revision/
  passage/C-unit identities, migration sequence, API envelopes, audit metadata,
  archive behavior, and Phase 1 scope before implementation.
- Contract review closed ambiguous identity reuse, decision/result chronology,
  lock-order, pagination, error-mapping, archive-format, and audit-privacy rules
  before migration or runtime changes.
- Runtime review found and fixed cursor-anchor under-validation, binary/padded
  unmatched-audit bypasses, decision-result tombstone chronology, unbounded audit
  candidate materialization, project-wide bootstrap ambiguity, decision-ID
  collision precedence, noncanonical cursor timestamps, and stored-dependency
  error misclassification.
- Archive review required exact review-audit equality, decision-linked result
  tombstone preservation, isolated-root validator instrumentation, direct format-1
  rejection, rehashed semantic-tamper rollback, and archive/write atomicity before
  approval.
- The repository's optional gstack `/review` workflow could not run because its
  required AskUserQuestion tool is unavailable in this Codex runtime. Independent
  branch-wide SQL/concurrency and API/security reviews were used instead.

## Known Limitations And Rollback

- Researcher IDs are attributable local identities, not authenticated accounts or
  role-based authorization. Audit events are not cryptographically chained.
- Archives remain local, unsigned, and unencrypted.
- Suggestions are imported records only. Live local/cloud inference, provider
  credentials, queues, workers, model/prompt provenance, confidence, and rationale
  remain Phase 4 work.
- The Phase 2 reviewer UI, manual coding workbench, source selection, coding
  stripes, retrieval, keyboard undo, and identity administration are not part of
  this slice.
- Saved-query and export entities plus final Phase 1 reconciliation remain the
  next durable-storage work.
- Reverting runtime code leaves qualitative migration 4 and accepted review rows
  that older code cannot understand. Preserve a verified format-2 archive before
  running older code.

## Git Commits

- `37fad92 Define coder suggestion review contract`
- `77595e1 Add coder suggestion review migration`
- `1b441c4 Implement strict research review service`
- `6ff0f2b Harden research review validation`
- `5aa2fa6 Expose and archive research review workflow`
