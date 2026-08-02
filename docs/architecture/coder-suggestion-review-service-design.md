# Coder Identity, Agent Suggestion, And Reviewer Decision Design

Status: Reviewed Phase 1 implementation contract

Date: 2026-08-01

## Outcome And Phase Boundary

This slice completes the Phase 1 coder, agent-suggestion, and reviewer-decision
entities without introducing a second human identity model. Its review service
exposes no coding mutation or promotion operation.

An existing active project researcher can register another local researcher,
record one immutable imported or synthetic coding suggestion against an exact
canonical evidence span and frozen code, and append an attributable human review
decision. An accepted or edited decision must point to an active coding reference
whose complete row, audit, target, and code state pass the existing
coding-reference storage contract and whose `created_by` is that reviewer. The
review service never creates, edits, removes, or silently accepts a coding
reference.

This is a storage, service, API, audit, archive, and recovery contract. It does
not add the Phase 2 manual workbench, the Phase 3 dual-coder reliability or
adjudication workflow, or the Phase 4 provider boundary, prompt execution,
worker, queue, model provenance, latency, confidence, rationale, or network
calls. Existing root-level agent-job, library-approval, and segmentation records
are different domains and are not imported automatically.

## Discovery Decisions And Tradeoffs

- The existing study ID remains the qualitative `project_id`.
- `researchers.researcher_id` is the canonical human coder and reviewer
  identity. There is no `coders` table and no new `coder` role. Existing roles
  remain exactly `researcher`, `reviewer`, and `administrator`.
- Roles are declared workflow labels, not authenticated authorization. Until the
  later identity and RBAC phase, any active project researcher may register an
  identity, record a suggestion, or review one. The API must not claim that a
  request-supplied actor is cryptographically authenticated. Phase 1 also cannot
  prove that a caller is human or prevent an agent process from invoking another
  unauthenticated API with a valid researcher ID. System-wide capability denial
  belongs to the authenticated Phase 4 worker/provider boundary.
- Coding attribution already exists in `coding_references.created_by` and
  `removed_by`. This slice exposes the existing researcher entity so a second
  coder can be created through the public contract; it does not rewrite coding
  references.
- A suggestion is one proposed coding relationship, not generated prose. It
  stores the exact evidence and frozen-code tuple needed to compare it with an
  accepted coding reference. It stores no excerpt text, rationale, numeric
  confidence, prompt, model output, secret, path, or provider response.
- Phase 1 accepts only `synthetic_fixture` and `imported_agent_output` origins.
  Live deterministic/local/cloud execution, provider/model/prompt provenance,
  input/output digests, latency, egress policy, and durable jobs are added only
  under a separately reviewed Phase 4 migration.
- The origin triple `(origin_kind, origin_id, origin_suggestion_key)` is the
  durable idempotency identity. The caller also supplies a stable `ags_*`
  suggestion ID. Exact replay is non-mutating; reusing either identity for
  different content conflicts.
- Reviewer decisions are immutable, append-only history. They do not carry a
  mutable status column on the suggestion. Current review status is derived from
  the newest decision, or `unreviewed` when no decision exists.
- Decision values are exactly `accepted`, `edited`, `rejected`, and `deferred`.
  `deferred` may be followed by another decision. The other three values are
  terminal. Correcting a terminal decision requires a new suggestion in this
  slice; reopening or superseding decisions needs a later migration.
- `accepted` requires an active reviewer-owned coding reference that exactly
  matches every suggested evidence, span, codebook-version, and code field.
- `edited` requires an active reviewer-owned coding reference with the same
  project source, transcript revision, and evidence set, but with at least one
  target, span, codebook-version, or code field changed. This permits a human to
  correct selection or coding while preventing an unrelated source from being
  presented as the edited result.
- `rejected` and `deferred` carry no coding-reference ID.
- A result coding reference must be created no earlier than its suggestion and
  no later than its decision:
  `suggestion.created_at <= coding_reference.created_at <= decision.created_at`.
  Pre-existing manual coding is not retrospectively represented as a reviewed
  result.
- A historical accepted/edited decision remains valid history if its linked
  coding reference is later tombstoned. Consumers must inspect the coding
  reference's tombstone before treating it as currently active coding.
- Requiring a pre-existing human-created coding reference deliberately leaves
  coding creation and decision recording as two explicit human operations. It
  avoids duplicating the coding service inside the review service and makes the
  narrower endpoint boundary mechanically testable: suggestion and decision
  operations never call coding-reference create/remove and never change coding-
  reference row counts.
- No new operation journal is added. Every mutation in this slice fits in one
  `BEGIN IMMEDIATE` qualitative transaction with its audit event.

## Canonical Identities

The exact proposed-coding identity is:

```text
(
  project_id,
  project_source_id,
  transcript_revision_id,
  evidence_set_id,
  target_kind,
  passage_id,
  cunit_id,
  start_offset,
  end_offset,
  codebook_version_id,
  code_id
)
```

`target_kind` is `passage` or `cunit`. A passage has `cunit_id == ""`; a
C-unit requires its exact non-empty C-unit ID. Offsets are zero-based Python
Unicode code-point offsets into the exact canonical target text, start-inclusive
and end-exclusive. The required relation is
`0 <= start_offset < end_offset <= len(target_text)`.

`project_source_id`, not content-derived `source_id`, proves study ownership.
The evidence set is mandatory because passage and C-unit IDs do not encode the
producer interpretation. The code identity is the exact
`(codebook_version_id, code_id)` pair in one frozen version; labels and stable
code keys are not accepted identities.

The new shared generated-ID prefixes are:

| Entity | Exact generated shape |
|---|---|
| Agent coding suggestion | `ags_` plus 32 lowercase hexadecimal characters |
| Reviewer decision | `rvd_` plus 32 lowercase hexadecimal characters |

New suggestion and decision input requires these exact shapes. Existing
researcher IDs retain the accepted stable lowercase entity-ID contract for
compatibility; registration does not silently rewrite or tighten bootstrap and
legacy researcher IDs.

## Authoritative Storage And Migration Rule

The existing per-study database remains authoritative:

```text
local_data/studies/<project_id>/qualitative.sqlite3
```

The next and only qualitative migration in this slice is:

```python
Migration(
    4,
    "add-coder-suggestion-review-contract",
    _add_coder_suggestion_review_contract,
)
```

Migrations 1, 2, and 3 remain byte-for-byte unchanged. Migration 4 is
transactional and part of the exact expected `sqlite_master` signature. A
failed upgrade must leave a version-3 database with no version-4 table, index,
trigger, ledger row, or `user_version` change.

Migration 4 adds only `agent_coding_suggestions`, `reviewer_decisions`, their
consumed indexes, and their integrity triggers. It reuses `researchers`,
`coding_references`, `codes`, `codebook_versions`, and
`qualitative_audit_events`.

## Migration 4 Schema

### `agent_coding_suggestions`

| Column | Contract |
|---|---|
| `agent_suggestion_id` | `TEXT NOT NULL` primary key; exact `ags_*` ID. |
| `project_id` | `TEXT NOT NULL`; owning qualitative project. |
| `origin_kind` | `TEXT NOT NULL`; exactly `synthetic_fixture` or `imported_agent_output`. |
| `origin_id` | `TEXT NOT NULL`; exact bounded stable producer/run identity. |
| `origin_suggestion_key` | `TEXT NOT NULL`; exact bounded candidate identity within that origin. |
| `project_source_id` | `TEXT NOT NULL`; exact project-owned source. |
| `transcript_revision_id` | `TEXT NOT NULL`; exact imported revision. |
| `evidence_set_id` | `TEXT NOT NULL`; exact immutable producer interpretation. |
| `target_kind` | `TEXT NOT NULL`; exactly `passage` or `cunit`. |
| `passage_id` | `TEXT NOT NULL`; exact canonical passage identity. |
| `cunit_id` | `TEXT NOT NULL`; exact C-unit ID or exact empty passage sentinel. |
| `start_offset`, `end_offset` | `INTEGER NOT NULL`; exact Unicode span with integer SQLite storage. |
| `codebook_version_id`, `code_id` | `TEXT NOT NULL`; exact frozen version-local code. |
| `created_by`, `created_at` | `TEXT NOT NULL`; human recorder and aware UTC timestamp. |

The table has `unique(project_id, agent_suggestion_id)` and
`unique(project_id, origin_kind, origin_id, origin_suggestion_key)`. Composite
foreign keys bind the code to the exact project and codebook version and bind
the recorder to the project. External evidence relations remain service
validated because SQLite cannot enforce foreign keys into `evidence.sqlite3`.

`origin_id` and `origin_suggestion_key` are exact printable ASCII identifiers
from 1 through 128 characters and 128 UTF-8 bytes, using only letters, digits,
`.`, `_`, `:`, and `-`. Slash, backslash, leading/trailing whitespace, NUL,
controls, and Unicode lookalikes are rejected. Values are opaque identifiers,
are never dereferenced as paths, and are never normalized. Offsets require
SQLite integer storage, not merely numeric comparison, and Boolean inputs are
rejected by the service.

The migration adds only the list indexes consumed by the initial API:

- suggestions by `(project_id, created_at, agent_suggestion_id)`;
- suggestions by `(project_id, project_source_id, created_at,
  agent_suggestion_id)`; and
- suggestions by `(project_id, codebook_version_id, code_id, created_at,
  agent_suggestion_id)`.

Triggers reject every update and delete, draft-code inserts, malformed
passage/C-unit empty-sentinel shape, and non-integer or invalid offset relations.
Direct SQL still cannot prove external target existence; strict service and
archive validation provide that cross-store gate.

### `reviewer_decisions`

| Column | Contract |
|---|---|
| `reviewer_decision_id` | `TEXT NOT NULL` primary key; exact `rvd_*` ID. |
| `project_id` | `TEXT NOT NULL`; owning qualitative project. |
| `agent_suggestion_id` | `TEXT NOT NULL`; exact suggestion being reviewed. |
| `decision_number` | `INTEGER NOT NULL` with integer SQLite storage; positive and sequential per suggestion. |
| `decision` | `TEXT NOT NULL`; exactly `accepted`, `edited`, `rejected`, or `deferred`. |
| `coding_reference_id` | `TEXT`; non-null for accepted/edited and null otherwise. |
| `reviewed_by`, `created_at` | `TEXT NOT NULL`; human reviewer and aware UTC timestamp. |

The table has `unique(project_id, reviewer_decision_id)` and
`unique(project_id, agent_suggestion_id, decision_number)`. Composite foreign
keys bind the decision to the same-project suggestion, optional coding
reference, and researcher.

The unique sequence supplies ordered history lookup; no duplicate mutable
latest-decision pointer is stored. Triggers reject every update/delete, sequence
gaps, decisions after a terminal decision, time earlier than the suggestion or
previous decision, time earlier than the result coding reference, a result
created earlier than the suggestion, invalid result-reference shape, removed
results, a result created by a different researcher, and the accepted/edited
candidate mismatch rules defined above. Active reviewer validation and exact
stored-type checking remain service responsibilities.

## Researcher Registration And Legacy Compatibility

`ResearchReviewService.create_researcher(...)` uses the existing `researchers`
table. For a genuinely new target ID it requires an existing active project
actor and inserts the new researcher active with matching `created_at` and
`updated_at`, plus exactly one `qualitative.researcher.created` audit event in
the same transaction.

Registration is an idempotent `PUT` contract:

- absent target + active existing actor: create one row and one event;
- exact `registered` target + same historical creating actor, display name, role,
  and canonical audit: return without mutation, even if that actor is now
  inactive;
- exact `bootstrap` target + `actor_id == researcher_id`, exact stored name and
  role, and canonical initialization audit: return without mutation;
- any `legacy_unverified` target: conflict rather than manufacture historical
  attribution; and
- every divergent existing target, origin actor, active state, timestamp, or
  audit history: conflict.

The service inspects and validates an existing target and its audit provenance
before checking current actor activity. Current activity is required only for a
new mutation; it is never retroactively required for an exact historical replay.

Display names receive one `str.strip()` normalization and must then contain 1
through 256 valid Unicode code points. NUL and lone UTF-16 surrogates are
rejected. Roles use the existing exact enum. This slice provides read/list but
no rename, role-change, activation, deactivation, or deletion API; those changes
belong to the later project-lifecycle contract.

Existing databases may contain secondary researchers inserted before this API
contract. Migration 4 does not fabricate creator attribution or backdated audit
events. Read/list classifies a valid researcher as exactly one of:

- `bootstrap`, when it is the actor of the canonical project-initialization
  event;
- `registered`, when it has one exact canonical researcher-creation event; or
- `legacy_unverified`, when neither event exists.

Both events, duplicates, malformed or unmatched researcher-creation events, and
invalid row fields conflict. A legacy identity remains usable for compatibility
but is explicitly returned as unverified local provenance. This is not an
authentication claim.

The bootstrap event is a separate exact special case. Its ID is
`"qae_init_" + sha256((project_id + "\0" + researcher_id).encode("utf-8"))`
truncated to the first 32 lowercase hexadecimal digest characters. Its project
and actor are the owning project and bootstrap researcher, its event type is
`qualitative.project.initialized`, its subject is `(project, project_id)`, its
metadata is exact `{}`, and its time equals the researcher's creation and update
time. Exactly one such canonical project-initialization event may classify one
bootstrap researcher. Normal registration events retain exact `qae_*` IDs.

Researchers are ordered by `(created_at, researcher_id)`. The initial list may
filter by exact role and active state.

## Suggestion Service Contract

`create_agent_suggestion(...)` requires a caller-supplied exact `ags_*` ID,
origin triple, candidate tuple, and human `researcher_id`.

Creation uses three stages so identity collisions win over missing dependencies
without overlapping workspace and study locks:

1. under a qualitative read, inspect both the supplied suggestion ID and exact
   origin triple. Either identity mapped to different content conflicts
   immediately; both identities mapped to the same exact row/request identify a
   possible retry; neither identifies a new candidate. Capture and validate any
   existing row/audit, then release;
2. under the workspace lock, resolve the request's exact evidence target, then
   release; and
3. in `BEGIN IMMEDIATE`, re-read both identities and all local dependencies. An
   exact existing replay returns without mutation, including when its historical
   recorder is now inactive. A new mutation requires an active recorder and
   exact frozen code, then inserts the immutable suggestion and exactly one
   `qualitative.agent_suggestion.created` audit event.

The origin unique key and suggestion ID are both checked. A retry is exact only
when every stored field, actor, timestamp pairing, and canonical audit agrees.
Reusing either identity for a different value conflicts. Missing target/code/
actor input is not found; an inactive actor, draft code, wrong ownership,
ambiguous state, corrupt storage, or divergent replay conflicts.

Suggestion list order is `(created_at, agent_suggestion_id)`. Initial exact
filters are `project_source_id`, `codebook_version_id`, `code_id`, `created_by`,
and `origin_kind`. Derived `review_status` is intentionally not a Phase 1 list
filter because it can change between keyset pages and cause an inconsistent
traversal. A snapshot response still contains the suggestion, its newest
decision or null, and derived review status.

Read/list strictly validates the bounded local rows and decision chains before
returning. It resolves only the page's deduplicated external targets after
releasing the qualitative read guard. Missing or changed external state for a
stored suggestion is a conflict, not a filtered or partial result.

## Reviewer Decision Service Contract

`append_reviewer_decision(...)` requires a caller-supplied exact `rvd_*` ID,
suggestion ID, active human `researcher_id`, exact decision, optional result
reference according to the decision shape, and an exact non-negative
`expected_decision_number`. Zero means no decision exists yet.

Decision append uses a three-stage preflight so stored external dependencies are
validated without overlapping workspace and study locks:

1. under a qualitative read, strictly validate the complete suggestion,
   decision chain, audits, every linked historical coding-reference row/audit,
   and the request-supplied result reference when present; capture their
   immutable identities, then release;
2. under the workspace lock, resolve the suggestion's canonical evidence target
   and, for accepted/edited, the result coding reference's canonical target and
   span, then release; and
3. in `BEGIN IMMEDIATE`, re-read and compare every captured local identity,
   relationship, audit, decision-chain fact, result creator, result tombstone,
   and timestamp before applying compare-and-append.

The coding reference must pass the complete `CodingReferenceService` storage
contract: exact stored types and IDs, same-project frozen code relation,
canonical creation/removal audit history, canonical external target and span,
and `created_by == reviewed_by`. The database cannot prove which Python method
originally wrote a row, so the contract makes no stronger claim. Suggestion and
coding target identities are immutable; the final transaction rechecks the
mutable coding tombstone and all decision state after external preflight.

The compare-and-append contract is:

- If the latest number equals the expectation and no terminal decision exists,
  append decision `expected + 1` plus exactly one canonical audit event.
- If the latest number equals `expected + 1` and that decision has the exact
  supplied ID, reviewer, decision, coding-reference ID, timestamp pairing, and
  audit, return it as a non-mutating retry even if the historical reviewer has
  since become inactive or its result coding reference has since been
  tombstoned. Stored row/audit timestamps, not caller-supplied timestamps, are
  compared for retry validation.
- Any other stale expectation, ID reuse, divergent retry, sequence gap, or
  attempt after a terminal decision conflicts.

A new mutation requires an active reviewer and, for accepted/edited, an active
result with
`suggestion.created_at <= coding_reference.created_at <= decision.created_at`.
`BEGIN IMMEDIATE` serializes concurrent append attempts. The decision and its
audit event commit or roll back together. The referenced coding relationship
already has its own earlier attribution and atomic coding audit; the review
service never mutates it.

Decision history is ordered by
`(decision_number, reviewer_decision_id)`. Pagination does not weaken full-chain
validation: the service validates the complete chain before returning a bounded
page.

## Canonical Audit Contract

All events live only in `qualitative_audit_events`; the root audit log is not
transactional with this database and is not used.

| Mutation | Event type | Subject | Canonical metadata |
|---|---|---|---|
| Researcher registration | `qualitative.researcher.created` | `researcher`, new researcher ID | `{"role":"<exact role>"}` |
| Suggestion creation | `qualitative.agent_suggestion.created` | `agent_suggestion`, suggestion ID | `{"origin_kind":"<exact enum>"}` |
| Decision append | `qualitative.reviewer_decision.created` | `reviewer_decision`, decision ID | `{"agent_suggestion_id":"ags_...","coding_reference_id":null-or-string,"decision":"<exact enum>","decision_number":1}` |

Metadata uses compact canonical JSON with sorted keys, UTF-8-safe strings, no
extra keys, and no whitespace variants. The audit subject binds the suggestion;
raw `origin_id` and `origin_suggestion_key` remain only in its domain row. Audit
metadata never contains display names, transcript/excerpt text, prompts,
outputs, secrets, paths, SQL, hashes, or raw exception text. Event IDs retain
the existing `qae_*` contract. Event actor and time must exactly equal the
domain row's actor and time.

Reads and project validation reject missing, duplicate, malformed, noncanonical,
or unmatched events. Candidate discovery must treat padded, case-folded, and
binary audit markers as suspicious instead of bypassing unmatched-event checks.
Audit insertion failure rolls back the paired domain row.

## Lock Order And Project Validation

Within `ResearchReviewService` operations on one root, workspace and study locks
never overlap. The exact operation sequences are:

```text
suggestion create:
  study guard + qualitative read -> inspect both identities/capture state -> release
  workspace lock -> resolve supplied evidence target -> release
  study guard + BEGIN IMMEDIATE -> exact re-read/compare + write/audit -> release

researcher and decision writes:
  researcher:
    study guard + BEGIN IMMEDIATE -> validate local state + write/audit -> release
  decision:
    study guard + qualitative read -> validate/capture local state -> release
    workspace lock -> resolve suggestion and result evidence -> release
    study guard + BEGIN IMMEDIATE -> exact re-read/compare + write/audit -> release

suggestion read/list/project validation:
  study guard + qualitative read -> stream/validate local state -> release
  workspace lock -> resolve deduplicated stored targets -> release
```

No review-service operation may acquire the same root's workspace lock while
holding a qualitative read or transaction, or enter that root's qualitative
guard while holding its workspace lock.

The archive orchestrator is the intentional exception at a higher boundary: it
retains its existing live-root order
`study archive_snapshot_guard -> workspace_mutation_lock` while capturing a
closed snapshot. It must never call a public review validator against the live
root while those archive locks are held. It stages the captured members under an
isolated temporary root and runs `validate_project_state()` only against that
staged root; any locks acquired there are disjoint from the live-root locks.

`validate_project_state()` strictly streams all researchers, suggestions,
decisions, linked coding-reference facts, and relevant audit candidates without
unbounded `fetchall()`. It validates exact SQLite storage classes, IDs, enums,
timestamps, sequence/terminal rules, actor existence, origin uniqueness,
same-project foreign relations, frozen code state, accepted/edited result rules,
and canonical one-to-one audits. It stores only deduplicated fixed-width
external target identities while the database guard is held, then resolves
those targets under the workspace lock after release.

Stored external dependency absence is always a conflict. Historical actor
inactivity and later coding-reference tombstones remain readable history.

## API Contract

All bodies and queries use strict Pydantic models with `extra="forbid"`.
Routes use the established `200` response convention:

```text
PUT  /api/studies/{study_id}/qualitative/researchers/{researcher_id}
GET  /api/studies/{study_id}/qualitative/researchers
GET  /api/studies/{study_id}/qualitative/researchers/{researcher_id}

POST /api/studies/{study_id}/qualitative/agent-suggestions
GET  /api/studies/{study_id}/qualitative/agent-suggestions
GET  /api/studies/{study_id}/qualitative/agent-suggestions/{agent_suggestion_id}

POST /api/studies/{study_id}/qualitative/agent-suggestions/{agent_suggestion_id}/decisions
GET  /api/studies/{study_id}/qualitative/agent-suggestions/{agent_suggestion_id}/decisions
```

Request bodies have these exact keys and no others:

```text
researcher PUT (all required strings):
  actor_id, display_name, role

suggestion POST:
  agent_suggestion_id: string
  origin_kind: string
  origin_id: string
  origin_suggestion_key: string
  researcher_id: string
  project_source_id: string
  transcript_revision_id: string
  evidence_set_id: string
  target_kind: string
  passage_id: string
  cunit_id: string
  start_offset: strict integer
  end_offset: strict integer
  codebook_version_id: string
  code_id: string

decision POST:
  reviewer_decision_id: string
  researcher_id: string
  expected_decision_number: strict integer
  decision: string
  coding_reference_id: conditionally present string
```

The suggestion `cunit_id` key is always present and is exactly `""` for a
passage. For `accepted`/`edited`, `coding_reference_id` is required and must be a
string. For `rejected`/`deferred`, it must be absent. Explicit null is never
accepted. Pydantic enforces primitive types, required/extra keys, and conditional
presence only; semantic enum, ID, origin, and relation validation stays in the
service so its failures map to 400.

Response records have these exact fields:

```text
ResearcherRecord:
  project_id, researcher_id, display_name, role,
  active: boolean,
  created_at, updated_at,
  provenance_classification: bootstrap | registered | legacy_unverified,
  provenance_actor_id: string | null

AgentSuggestionRecord:
  agent_suggestion_id, project_id,
  origin_kind, origin_id, origin_suggestion_key,
  project_source_id, transcript_revision_id, evidence_set_id,
  target_kind, passage_id, cunit_id,
  start_offset: integer, end_offset: integer,
  codebook_version_id, code_id,
  created_by, created_at

ReviewerDecisionRecord:
  reviewer_decision_id, project_id, agent_suggestion_id,
  decision_number: integer,
  decision: accepted | edited | rejected | deferred,
  coding_reference_id: string | null,
  reviewed_by, created_at

AgentSuggestionSnapshot:
  suggestion: AgentSuggestionRecord,
  current_decision: ReviewerDecisionRecord | null,
  review_status: unreviewed | accepted | edited | rejected | deferred
```

`provenance_actor_id` is the bootstrap researcher for `bootstrap`, the
registration actor for `registered`, and null for `legacy_unverified`.
`coding_reference_id` is always present in a decision response and is null for
rejected/deferred. A later coding tombstone does not rewrite the historical
snapshot status. Every `agent_suggestions[]` collection element is a complete
`AgentSuggestionSnapshot`, not a bare suggestion.

Exact query keys are:

```text
researchers: role, active, limit, cursor
agent suggestions: project_source_id, codebook_version_id, code_id,
                   created_by, origin_kind, limit, cursor
decision history: limit, cursor
```

Every key is optional. `active` accepts only exact lowercase ASCII `true` or
`false`; enum filters accept only their exact stored spellings. `limit` accepts
only canonical ASCII decimals from `1` through `50`, defaults to `20`, and
rejects signs, whitespace, leading zeroes, decimals, exponent syntax, and
Boolean-like spellings. Repeated and unknown query keys are structural 422
failures. Derived `review_status` is returned but is not accepted as a filter.

Keyset pagination defaults to 20 and has a maximum of 50:

- researchers: `(created_at, researcher_id)`;
- suggestions: `(created_at, agent_suggestion_id)`; and
- decisions: `(decision_number, reviewer_decision_id)`.

Cursors are opaque, versioned, project/endpoint/filter-bound canonical base64url
JSON. Their decoded payloads have these exact shapes:

```json
{
  "after": {"created_at": "aware UTC timestamp", "id": "entity id"},
  "filters": {"every endpoint filter": null},
  "project_id": "study-id",
  "resource": "researchers | agent_suggestions",
  "v": 1
}
```

Decision cursors instead use
`"after":{"decision_number":1,"id":"rvd_..."}`, resource
`reviewer_decisions`, and filters
`{"agent_suggestion_id":"ags_..."}`. Filter objects always contain every
filter key in a fixed semantic shape, using null for omission and a JSON Boolean
for normalized researcher `active`.

Cursor JSON is UTF-8, compact, sorted-key, `ensure_ascii=True`, and
`allow_nan=False`. Encoding is unpadded base64url; `=` is rejected. An encoded
cursor must contain 1 through 4096 ASCII characters from `[A-Za-z0-9_-]`.
Decoding rejects invalid base64, invalid UTF-8/JSON, duplicate JSON keys, wrong
or extra keys/types, noncanonical timestamps/integers/IDs, endpoint/project/
filter mismatch, and any value whose canonical re-encoding is not byte-for-byte
equal to the supplied cursor. A fully valid cursor whose exact anchor row does
not exist returns 404; malformed or filter-mismatched cursors return 400.
The cursor `filters` object contains only domain filters; pagination controls
`limit` and `cursor` are never embedded in it.

Lists query in key order after the anchor and fetch at most `limit + 1` rows.
They return at most `limit`; `next_cursor` is generated from the last returned
row only when the extra row proves another page exists. No missing, invalid, or
changed anchor silently restarts traversal.

Exact response envelope types are:

```text
{"researcher": <ResearcherRecord>}
{"researchers": [<ResearcherRecord>, ...], "next_cursor": <string | null>}
{"agent_suggestion": <AgentSuggestionSnapshot>}
{"agent_suggestions": [<AgentSuggestionSnapshot>, ...], "next_cursor": <string | null>}
{"reviewer_decision": <ReviewerDecisionRecord>}
{"reviewer_decisions": [<ReviewerDecisionRecord>, ...], "next_cursor": <string | null>}
```

Error mapping is content-safe and consistent:

- `422 {"detail":"Request validation failed"}` for malformed JSON, missing or
  extra fields, coercive primitive types, conditional field presence including
  explicit null, and repeated/unknown query parameters;
- `400 {"detail":"Review request is invalid"}` for well-formed but invalid
  IDs, enum spellings, origin alphabets, offset relations, limits, or malformed/
  project/endpoint/filter-mismatched cursor encodings;
- `404 {"detail":"Review dependency was not found"}` for an absent initialized
  study, supplied actor, target, code, suggestion, coding reference, or cursor
  anchor; and
- `409 {"detail":"Review state conflicts with stored data"}` for inactive
  actors, draft codes, wrong ownership, divergent retries, stale/terminal
  decisions, schema incompatibility, corruption, or unavailable dependencies of
  already-stored state.

The route-scoped validation scrubber includes all new qualitative paths. No API
error may echo origin values, IDs copied from malformed storage, display names,
evidence text, paths, SQL, hashes, or exception text.

## Archive, Restore, And Format Compatibility

Archive format remains 2. It already captures the complete study directory,
including `qualitative.sqlite3`, and the evidence-target/text closure.

Archive creation and isolated restore preflight call
`ResearchReviewService.validate_project_state()` beside the existing case,
coding-reference, and note validators, using the captured or staged root rather
than live state. New domain errors receive the same content-safe archive
translation. A semantic failure aborts creation or restore before destination
mutation; late restore failure retains the existing rollback guarantee.

When migration 4 is present, format-1 eligibility directly runs
`SELECT 1 FROM agent_coding_suggestions LIMIT 1`. Any row, including a
semantically corrupt row, makes format 1 ineligible because its canonical target
closure cannot be represented there. Empty migration-4 tables and researcher-
only state do not. Free-form fields are never scanned for column-name literals.

Archive round-trip must preserve researcher classifications, suggestion origin
identity, exact candidates, decision history, linked coding references,
tombstones, and audit rows byte-for-byte at the database-member boundary and
semantically through service reads.

Format-2 archives and qualitative audit events remain unsigned, unencrypted,
and locally attributable rather than authenticated or tamper-evident. This
slice makes no stronger security claim.

## Required Adversarial Proof Before Merge

Focused tests must prove at least:

- cold version-4 initialization, exact version-3 upgrade preservation, injected
  migration rollback, ledger/name/version disagreement, newer-version refusal,
  and missing/extra/changed schema-object rejection;
- researcher registration/read/list, all roles, exact retry, conflicting reuse,
  legacy classification, missing/inactive actors, malformed rows, and audit
  rollback/corruption/unmatched candidates;
- passage and C-unit suggestions, exact external ownership and membership,
  offset bounds, frozen version-local codes, cross-project substitution, strict
  input types, exact origin retry, and divergent origin or ID reuse;
- immutable suggestion and decision rows plus database-trigger enforcement;
- every decision value, result-reference presence rules, accepted exact match,
  edited same-lineage/different-candidate rules, reviewer ownership, removed
  results, defer-to-terminal sequence, stale/concurrent appends, terminal
  rejection, and exact retry;
- missing, duplicate, extra-key, noncanonical, binary-marker, padded-marker, or
  content-bearing audits and atomic rollback on injected audit failure;
- bounded deterministic filtered pagination, malformed/cross-filter cursors,
  canonical limits, repeated/unknown queries, explicit-null shape failures, and
  content-safe API errors with privacy sentinels;
- whole-project validation streams rows, never overlaps workspace/study locks,
  rejects unavailable stored dependencies, and performs no provider or network
  call; suggestion and decision endpoints never invoke coding-reference create/
  remove and never change coding-reference row counts;
- format-2 create/restore round-trip, direct semantic SQLite tampering with a
  rehashed archive, version-1 rejection through direct evidence-set inspection,
  destination-conflict and late-failure rollback, captured-root validation, and
  archive-versus-write atomicity; and
- unchanged existing agent-job, segmentation, evidence, qualitative, archive,
  backend, and frontend regression gates.

## Deferred Work

- researcher rename, role transition, deactivation/reactivation, authenticated
  identity, RBAC, LAN access, and cryptographic audit integrity;
- reviewer notes, rationale, confidence, prompt/model/provider fields, digests,
  latency, costs, egress classification, secrets, and model output retention;
- provider calls, local inference, OpenRouter, queues, workers, leases, retries,
  cancellation, timeouts, and restart recovery;
- Phase 2 workbench and review controls;
- Phase 3 calibration assignments, blind dual coding, agreement denominators,
  kappa, disagreement adjudication, matrices, and publication exports; and
- automated conversion of agent jobs, segmentation outputs, or library
  approvals into qualitative suggestions.
