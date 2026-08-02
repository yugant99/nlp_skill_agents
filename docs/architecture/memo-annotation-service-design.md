# Memo And Annotation Service Design

Status: Reviewed Phase 1 implementation contract

Date: 2026-08-01

## Outcome And Phase Boundary

This slice makes researcher-authored memos and annotations durable,
revisioned, attributable, auditable, and portable. An active project researcher
can create either kind of note against exactly one study, source, case, frozen
code, or exact evidence excerpt; read deterministic current and historical
state; append a compare-and-append revision; and apply a one-way attributable
tombstone.

This is the Phase 1 storage, service, API, and recovery foundation. Phase 2 owns
the researcher-facing authoring workbench, transcript selection interactions,
coding stripes, rich-text presentation, keyboard undo, and search UI. This
slice does not add model/provider calls, agent-authored notes, tags, folders,
cross-note links, bulk mutation, authentication, authorization, retention
policy, or export formats.

## Discovery Decisions And Tradeoffs

- The existing study ID is the qualitative `project_id`. No second study or
  project identity is introduced.
- One shared revisioned-note model represents both kinds. `note_kind` is exactly
  `memo` or `annotation`; separate parallel tables and services would duplicate
  the same provenance and lifecycle rules.
- A memo is a titled analytic or reflexive note. Its title is normalized by one
  Python `str.strip()` call and must then be non-empty. No Unicode, case, or
  newline normalization is applied. An annotation is an untitled contextual
  note. Its stored title is exactly the empty string, and annotation API
  requests forbid a title field.
  Both kinds require a non-blank body and support the same targets and revision
  lifecycle. These are technical semantics, not a claim of methodological
  validation.
- Each note has exactly one immutable primary target. Relinking creates a new
  note and tombstones the old one; target arrays and mutable relinking are out
  of scope.
- The five target kinds are exactly `study`, `source`, `case`, `code`, and
  `excerpt`. There is no separate transcript-revision target: a source note
  follows its project source across revisions, while revision-specific meaning
  uses an excerpt target.
- A code target freezes the exact `(codebook_version_id, code_id)` pair and
  requires that version to be frozen. A `stable_code_key`, code label, or latest
  version is not a target identity.
- An excerpt points directly to canonical evidence, not to a coding reference.
  It can therefore exist before coding and survive uncoding or reapplication.
  The qualitative database never stores copied excerpt text or an excerpt hash.
- Create is intentionally non-idempotent because two identical notes can be
  intentional. Revision and removal have explicit retry contracts.
- Accepted writes carry an explicit active project `researcher_id`. Existing
  role values remain descriptive, not an authorization boundary. Historical
  attribution remains readable after a researcher becomes inactive.
- The existing audit boundary proves local attribution and atomicity, not an
  authenticated user or tamper-evident log.

## Canonical Target Identities

The project is implicit in every route and stored explicitly on every row.
Targets have these exact shapes:

| Target kind | Canonical stored identity |
|---|---|
| `study` | No subordinate identifier. |
| `source` | Exact opaque project-owned `project_source_id`. |
| `case` | Exact `(project_id, case_id)`. |
| `code` | Exact `(project_id, codebook_version_id, code_id)` in a frozen version. |
| `excerpt` | Exact `(project_source_id, transcript_revision_id, evidence_set_id, excerpt_target_kind, passage_id, cunit_id, start_offset, end_offset)` within the project. |

`project_source_id`, not the content-derived source ID, proves project
ownership. For an excerpt, `excerpt_target_kind` is exactly `passage` or
`cunit`. A passage target has no C-unit ID; a C-unit target requires its exact
parent-derived C-unit ID. `evidence_set_id` is mandatory because passage and
C-unit IDs alone do not identify the producer interpretation.

Offsets are zero-based Python Unicode code-point offsets into the canonical
target text. `start_offset` is inclusive, `end_offset` is exclusive, and the
required relation is `0 <= start_offset < end_offset <= len(target_text)`.
The caller supplies IDs and offsets but never authoritative target text or a
hash.

## Authoritative Storage And Migration Rule

The existing per-study database remains authoritative:

```text
local_data/studies/<project_id>/qualitative.sqlite3
```

The qualitative migration ledger currently ends at migration 2. This slice
adds only:

```python
Migration(3, "add-memo-annotation-contract", _add_qualitative_notes)
```

Migrations 1 and 2 remain byte-for-byte unchanged. Migration 3 is forward-only,
transactional, and part of the exact `sqlite_master` signature replayed by
`QualitativeProjectDatabase`. A failed migration must leave the database at
version 2 with no migration-3 table, index, trigger, ledger row, or `user_version`
change.

The shared ID registry adds:

| Entity | Prefix and shape |
|---|---|
| memo | `mem_` plus 32 lowercase hexadecimal characters |
| annotation | `ann_` plus 32 lowercase hexadecimal characters |
| note revision | `nrv_` plus 32 lowercase hexadecimal characters |

The note kind and ID prefix must agree on every read. Existing `qae_` audit
event IDs remain unchanged.

## Migration 3 Schema

### `qualitative_notes`

| Column | Contract |
|---|---|
| `note_id` | Primary key; exact memo or annotation ID. |
| `project_id` | Owning qualitative project. |
| `note_kind` | Exactly `memo` or `annotation`. |
| `target_kind` | Exactly `study`, `source`, `case`, `code`, or `excerpt`. |
| `project_source_id` | Nullable exact source component. |
| `case_id` | Nullable exact case component. |
| `codebook_version_id` | Nullable exact code-version component. |
| `code_id` | Nullable exact code component. |
| `transcript_revision_id` | Nullable exact excerpt component. |
| `evidence_set_id` | Nullable exact excerpt component. |
| `excerpt_target_kind` | Nullable; exactly `passage` or `cunit` for excerpts. |
| `passage_id` | Nullable exact excerpt passage. |
| `cunit_id` | Nullable exact excerpt C-unit. |
| `start_offset` | Nullable exact integer excerpt start. |
| `end_offset` | Nullable exact integer excerpt end. |
| `created_by`, `created_at` | Immutable creator and aware UTC timestamp. |
| `removed_by`, `removed_at` | Initially null; first complete tombstone only. |

The table has `unique(project_id, note_id)` plus composite foreign keys to the
owning project, local case, exact code/version pair, creator, and optional
remover. Foreign keys are restrictive; note lifecycle never cascades.

One exhaustive check constraint enforces the exact nullable target shape:

- `study`: every subordinate target column is null;
- `source`: only `project_source_id` is non-null;
- `case`: only `case_id` is non-null;
- `code`: only `codebook_version_id` and `code_id` are non-null; and
- `excerpt`: source, revision, evidence set, excerpt target kind, passage, and
  exact integer offsets are non-null. `cunit_id` is null for a passage and
  non-null for a C-unit.

The table also requires a complete-null or complete-non-null tombstone pair and
requires integer SQLite storage classes for populated offsets. External source
and excerpt relations are service-validated because SQLite cannot enforce
foreign keys across the evidence and qualitative databases.

### `qualitative_note_revisions`

| Column | Contract |
|---|---|
| `note_revision_id` | Primary key; exact `nrv_` ID. |
| `project_id`, `note_id` | Exact owning note. |
| `revision_number` | Exact SQLite integer greater than zero. |
| `title` | Memo title or exact empty annotation title. |
| `body` | Exact researcher-authored Unicode body. |
| `created_by`, `created_at` | Immutable revision author and aware UTC timestamp. |

The table has `unique(project_id, note_revision_id)` and
`unique(project_id, note_id, revision_number)`, a restrictive composite foreign
key to the note, and a restrictive project-researcher foreign key. The unique
revision constraint also supplies the ordered note-history index; no redundant
mutable current-revision pointer or duplicate index is added.

The technical content ceilings are 512 Unicode code points for a stripped memo
title and 262,144 Unicode code points for either note body. A body is never
transformed: `body.strip()` is used only to test whether it is blank, and the
original code points, whitespace, and newlines are stored. NUL and lone UTF-16
surrogate code points are rejected in both fields so every accepted value is
valid UTF-8 and SQLite text. Annotation titles are exactly empty. No NFC/NFKC,
case, or newline normalization is applied. These ceilings limit accidental
resource exhaustion; they do not prescribe research method.

Across all revisions in one project, exact UTF-8 title and body bytes are capped
at 256 MiB. Create and revise enforce this budget inside `BEGIN IMMEDIATE`, and
strict project validation recomputes it from accepted text. Direct-SQL excess is
stored corruption, not content to return. The budget reserves substantial room
beneath the archive's 512 MiB per-member ceiling, but portability is still
conditional on the existing global archive limits because other qualitative
tables also occupy the same database. Archive creation fails visibly if the
complete database exceeds those limits.

### Indexes

Migration 3 adds only the indexes consumed by the Phase 1 list contract:

- notes by `(project_id, note_kind, created_at, note_id)`;
- notes by `(project_id, note_kind, created_by, created_at, note_id)`;
- notes by `(project_id, target_kind, created_at, note_id)`.

The unique revision constraint supplies ordered history lookup. Target-specific
attachment indexes are deferred until Phase 2 freezes the exact target-filter
query contract; migration 3 does not add speculative write cost.

### Triggers

Migration 3 adds database enforcement for the invariants that direct SQL can
express:

- a code-target note requires an existing frozen codebook version;
- a note cannot be inserted already removed or physically deleted;
- the only note update is the first complete
  `(null, null) -> (removed_by, removed_at)` tombstone transition;
- a removal time must be valid, must not predate note creation, and must not
  predate the latest revision, and removal fails if revision history is absent;
- a revision can never be updated or deleted;
- a removed note accepts no new revision;
- revision 1 must be first, and every later insert must be exactly
  `max(revision_number) + 1`;
- revision 1 actor and time equal note creation actor and time;
- later revision time cannot predate the preceding revision; and
- memo/annotation title shape, a minimally non-empty body, exact integer
  revision storage, and the content ceilings are enforced on insert.

Unicode blank detection, ID shape, timestamp awareness, complete audit pairing,
and cross-store ownership still require strict service validation. The database
constraints are defense in depth, not a replacement for that validation.
Nullable trigger comparisons use SQLite `IS`/`IS NOT`, populated offsets and
revision numbers require `typeof(...) = 'integer'`, and NUL/surrogate rejection
does not rely on SQLite `length()`.

## Service Contract

Shared behavior belongs in `backend/qualitative/notes.py`. The service exposes
kind-specific entry points backed by one implementation:

- create a memo or annotation;
- read one current snapshot;
- deterministically list snapshots;
- append one revision with an expected current revision number;
- list the immutable revision history;
- tombstone a note; and
- validate the complete project note state for archive preflight.

Notes are ordered by `(created_at, note_id)`. Revisions are ordered by
`(revision_number, note_revision_id)`. Reads return exact stored identifiers and
content but do not silently join labels, source text, excerpt text, or codebook
display fields.

### Create

Create validates input and any external target first. It then opens one
`QualitativeProjectDatabase.transaction`, requires an active project actor,
revalidates local case or frozen code dependencies, and inserts:

1. the immutable note header;
2. revision 1 with the same actor and timestamp; and
3. exactly one canonical creation audit event.

All three writes commit or roll back together. Identical creates produce
different notes by design.

Before revising or removing, the service strictly validates the complete stored
header, revision chain, local relations, and canonical audit chain. It repeats
that validation after the accepted mutation and before returning. Removal may
skip external resolution, but it never skips local or audit validation.

### Compare-And-Append Revision

Revision input requires `expected_revision_number` greater than zero. The
immutable target never changes. Because the revision request does not repeat the
target, revision first reads and strictly validates the complete local snapshot,
then releases the study guard. It next validates that stored external source or
excerpt target under the workspace lock and releases that lock. Finally it opens
`BEGIN IMMEDIATE`, re-reads and strictly validates the complete local state and
target identity, and performs the compare-and-append decision. A new mutation
requires an active actor and valid local dependencies. An exact accepted retry is
non-mutating and returns the persisted row even if its historical actor has since
been deactivated. A concurrent local revision is thus observed in the final
transaction rather than overwritten.

- If the current number equals the expectation and content differs, append
  revision `N + 1` and one matching audit event atomically.
- If the current number equals the expectation and content is unchanged, reject
  the no-op rather than manufacture history.
- If the current number is `expected + 1` and that newest revision has the exact
  same actor, normalized title, exact body, and canonical audit event, return it
  as an idempotent retry without another row or event.
- Every other stale or divergent attempt conflicts.

`BEGIN IMMEDIATE` serializes concurrent append attempts. No revision is accepted
after removal.

### Removal

A new removal requires an active actor but deliberately does not resolve external
evidence. This preserves attributable cleanup after external target damage. The
first removal writes the complete tombstone and exactly one audit event in one
transaction. An exact retry by the stored remover is non-mutating and returns the
tombstone without another event even if that historical remover has since been
deactivated. A different remover, resurrection, second transition, target
mutation, or physical deletion conflicts.

## Lock Order And Cross-Store Validation

The mandatory invariant is that a note-service workspace lock and study guard
never overlap. No code may enter a qualitative read or transaction while
holding the root workspace lock, and no code may acquire the workspace lock
while holding a qualitative read or transaction. Archive capture can hold the
study snapshot guard before its workspace work, so nesting the reverse order in
a service would create a deadlock.

The operation sequences are exact:

```text
create with external target:
  workspace lock -> validate supplied target -> release
  study guard + BEGIN IMMEDIATE -> revalidate local state + write -> release

read/list/history/project validation:
  study guard + qualitative read -> collect and validate local state -> release
  workspace lock -> validate stored external targets -> release

revise:
  study guard + qualitative read -> validate local snapshot/audit -> release
  workspace lock -> validate stored external target -> release
  study guard + BEGIN IMMEDIATE -> re-read/validate/compare/write -> release

remove:
  study guard + BEGIN IMMEDIATE -> validate local snapshot/audit/write -> release
```

Target validation is exact:

- `study`: require the initialized qualitative project;
- `source`: under the workspace lock, load `EvidenceCatalog.source_history` and
  require exact source existence and `workspace_id == project_id`;
- `case`: inside the qualitative transaction, require the exact same-project
  case;
- `code`: inside the qualitative transaction, require the exact same-project
  code in the exact frozen version; and
- `excerpt`: under the workspace lock, resolve the full tuple through
  `EvidenceTargetRegistry`, compare every returned identity, load the exact
  canonical target text, and enforce the Unicode offset bounds.

Read/list/history operations resolve only the bounded page's external targets;
whole-project validation resolves all deduplicated targets after releasing its
local read. Missing external state on an already-stored note makes project state
conflict visibly; it is never silently skipped.

## Strict Stored-State Validation

Every service read and `validate_project_state()` rejects rather than coerces:

- wrong SQLite storage classes, including text/Boolean-like values for integers;
- padded, malformed, overlong, wrong-prefix, or kind/prefix-disagreeing IDs;
- unknown note or target kinds and partial, hybrid, or empty-sentinel targets;
- invalid, naive, overlong, non-UTC, or non-monotonic timestamps;
- missing, duplicate, gapped, or out-of-order revision chains;
- revision 1 creator/time mismatch or any revision after removal;
- annotation title content, blank or oversized memo titles, blank or oversized
  bodies, NUL/lone-surrogate content, normalization disagreement, or a project
  note-content total above 256 MiB of exact UTF-8 bytes;
- partial tombstones, removal before creation/latest revision, or resurrection;
- missing or foreign actors, cases, codes, versions, sources, or evidence
  targets;
- draft or wrong-version code targets;
- stale/mismatched excerpt sets, passage/C-unit identity, text blobs, or offsets;
  and
- missing, duplicate, unmatched, malformed, noncanonical, extra-key, or
  content-bearing note audit events.

Inactive historical actors are valid attribution on reads, but inactive actors
cannot mutate. Corruption raises a content-safe conflict and never returns a
partial note or partial project validation result.

## Atomic Audit Contract

Canonical event and subject pairs are:

| Mutation | Event type | Subject type | Exact metadata keys |
|---|---|---|---|
| memo create | `memo.created` | `memo` | `note_revision_id`, `revision_number`, `target_kind` |
| memo revise | `memo.revised` | `memo` | `note_revision_id`, `revision_number` |
| memo remove | `memo.removed` | `memo` | none |
| annotation create | `annotation.created` | `annotation` | `note_revision_id`, `revision_number`, `target_kind` |
| annotation revise | `annotation.revised` | `annotation` | `note_revision_id`, `revision_number` |
| annotation remove | `annotation.removed` | `annotation` | none |

`metadata_json` is canonical compact JSON with sorted keys. Create/revision
events match the domain row's actor and timestamp; removal matches the tombstone
actor and timestamp. The subject ID is the note ID. Revision number 1 pairs only
with the creation event; every later revision has exactly one revision event;
and a removal event exists exactly when the note is tombstoned.

Audit metadata never contains a title, body, source or excerpt text, label,
filename, path, hash, SQL detail, or raw exception. Project validation also
rejects an unmatched note-domain audit event, not only a missing event.

## API Contract

The public resources are two parallel six-route collections. Every successful
route returns status 200 and exactly this top-level body:

| Method | Route suffix after `/api/studies/{study_id}` | Exact success body |
|---|---|---|
| `POST` | `/qualitative/memos` | `{"memo": NoteSnapshot}` |
| `GET` | `/qualitative/memos` | `{"memos": [NoteSnapshot, ...], "next_cursor": string-or-null}` |
| `GET` | `/qualitative/memos/{memo_id}` | `{"memo": NoteSnapshot}` |
| `POST` | `/qualitative/memos/{memo_id}/revisions` | `{"memo": NoteSnapshot}` |
| `GET` | `/qualitative/memos/{memo_id}/revisions` | `{"revisions": [NoteRevisionRecord, ...], "next_cursor": string-or-null}` |
| `DELETE` | `/qualitative/memos/{memo_id}` | `{"memo": NoteSnapshot}` |
| `POST` | `/qualitative/annotations` | `{"annotation": NoteSnapshot}` |
| `GET` | `/qualitative/annotations` | `{"annotations": [NoteSnapshot, ...], "next_cursor": string-or-null}` |
| `GET` | `/qualitative/annotations/{annotation_id}` | `{"annotation": NoteSnapshot}` |
| `POST` | `/qualitative/annotations/{annotation_id}/revisions` | `{"annotation": NoteSnapshot}` |
| `GET` | `/qualitative/annotations/{annotation_id}/revisions` | `{"revisions": [NoteRevisionRecord, ...], "next_cursor": string-or-null}` |
| `DELETE` | `/qualitative/annotations/{annotation_id}` | `{"annotation": NoteSnapshot}` |

Every request model uses strict types and `extra="forbid"`. Create requests carry
`researcher_id`, body, one discriminated target object, and memo title only.
Revision requests carry `researcher_id`, strict `expected_revision_number`,
body, and memo title only. Delete carries only `researcher_id`. The target object
uses `kind` plus only the canonical fields for that kind; hybrid or extra fields
fail request validation.

Collection GET accepts only `target_kind`, `created_by`,
`include_removed=true|false`, `limit`, and `cursor`. `include_removed` defaults
to false. `limit` defaults to 20 and accepts only a canonical ASCII decimal from
1 through 50. A collection cursor is the exact last memo/annotation ID from a
prior page. It must belong to the same project and route kind; the service loads
its immutable `(created_at, note_id)` tuple and returns rows strictly after that
tuple in `(created_at, note_id)` order. A changed filter does not reinterpret the
cursor. The page query materializes at most `limit + 1` matching note headers,
returns at most `limit`, and sets `next_cursor` to the last returned note ID only
when another matching row exists. The strict-read invariant still requires
visiting the complete revision and audit chain for each materialized snapshot and
recomputing the project content budget. Those validation scans use cursor
iteration and retain only current/endpoint records plus compact actor identities,
rather than materializing complete or off-page full-content chains in memory.

History GET accepts only `limit` and `cursor`, with the same default and maximum.
Its cursor is the exact last `note_revision_id` from a prior page and must belong
to the requested note. Results are strictly after its immutable
`(revision_number, note_revision_id)` tuple. Its page query likewise materializes
at most `limit + 1` full-content revision rows, returns at most `limit`, and emits
the last returned revision ID as `next_cursor` only when another revision exists.
Before that page query, strict validation streams the complete revision and audit
chain with bounded memory. Repeated scalar parameters, unknown parameters,
noncanonical limits, and wrong query types return 422.

Singular and history reads include a tombstoned note. Collection reads exclude
tombstoned notes by default and include them only when `include_removed=true`.
The API may add exact target filters with the Phase 2 retrieval contract, but
this Phase 1 route does not guess that query shape.

`NoteSnapshot` is exactly:

```json
{
  "note": {
    "note_id": "mem_* or ann_*",
    "note_kind": "memo or annotation",
    "project_id": "study-id",
    "target": {"kind": "study | source | case | code | excerpt"},
    "created_by": "res_*",
    "created_at": "aware timestamp",
    "removed_by": null,
    "removed_at": null
  },
  "current_revision": {
    "note_revision_id": "nrv_*",
    "note_id": "mem_* or ann_*",
    "revision_number": 1,
    "title": "memo title or empty string",
    "body": "exact stored body",
    "created_by": "res_*",
    "created_at": "aware timestamp"
  }
}
```

`NoteRevisionRecord` is exactly the displayed `current_revision` object. A note
header's `target` is one of these exact objects; inapplicable keys are omitted,
not returned as null or empty sentinels:

```json
{"kind":"study"}
{"kind":"source","project_source_id":"opaque project source id"}
{"kind":"case","case_id":"cas_*"}
{"kind":"code","codebook_version_id":"cbv_*","code_id":"cod_*"}
{"kind":"excerpt","project_source_id":"opaque id","transcript_revision_id":"trv_*","evidence_set_id":"evs_*","excerpt_target_kind":"passage","passage_id":"psg_*","start_offset":0,"end_offset":4}
{"kind":"excerpt","project_source_id":"opaque id","transcript_revision_id":"trv_*","evidence_set_id":"evs_*","excerpt_target_kind":"cunit","passage_id":"psg_*","cunit_id":"cun_*","start_offset":0,"end_offset":4}
```

Collection elements are complete `NoteSnapshot` values, not abbreviated
headers. Pagination bounds the full-content response. Responses expose exact
stored content but never add source text, excerpt text, code labels, or
filenames.

Every route calls the content-safe study preflight before constructing the note
service. Route-scoped request validation for only `/qualitative/memos` and
`/qualitative/annotations` suppresses FastAPI's input-echoing validation detail.
Domain errors map without `str(exc)` leakage:

- 422: malformed JSON; missing, extra, hybrid, or structurally wrong fields;
  wrong JSON types; unknown target discriminators; repeated/unknown query
  parameters; or noncanonical pagination syntax;
- 400: a structurally valid request with malformed ID values, semantically
  invalid offsets, blank/oversized/invalid text, or an invalid expected revision
  number;
- 404: missing study, requested note, supplied actor, supplied collection/history
  cursor, or a dependency supplied for a new create; and
- 409: inactive or foreign dependency, draft code, stale revision, removed
  state, conflicting retry, corrupted/newer storage, or a source/case/code/actor/
  evidence dependency missing from an already-stored note during
  read/list/history/revise/project validation.

No error response contains note content, excerpt content, paths, hashes, SQL, or
raw stored values.

## Archive, Restore, And Version-1 Compatibility

No project-archive format bump is required. Format 2 already captures the whole
study directory, including `qualitative.sqlite3`, and the exact project evidence
target/text closure. Memo and annotation revisions therefore travel inside the
qualitative database; excerpt text remains in its existing content-addressed
evidence blob.

Archive capture and staged restore validation add
`NoteService(...).validate_project_state()` beside case and coding-reference
validation, translate only the three note-domain error classes, and validate
against the isolated captured/staged root. They do not reacquire live study
state beneath the live workspace lock.

Format 1 cannot carry evidence target or text members. Its compatibility scan
must directly inspect migration-3 `qualitative_notes`: any non-null, non-empty
`evidence_set_id` makes the archive ineligible, regardless of the accompanying
stored target kind. Valid study, source, case, and code notes have null evidence
set columns and remain format-1 eligible. This broader direct check also rejects
a tampered hybrid row instead of trusting its discriminator. The scanner must
not search revision body text for the literal string `evidence_set_id`;
researcher content is not a reference declaration.

Restore keeps its existing verify-before-mutation, isolated staging,
destination preflight, locking, and rollback contracts. Rehashed tampering of a
note, revision, target, actor, tombstone, or audit row must fail staged
validation before publication and leave an existing destination unchanged.

Archives remain local, unsigned, and unencrypted. Portability of memo content is
not authorization to use real or sensitive research data.

## Required Adversarial Proof

Focused tests must prove:

1. cold version-3 creation and version-2-to-3 upgrade preserve every earlier
   row and audit; induced failure remains exactly version 2;
2. missing, changed, extra, or newer migration-3 schema objects fail exact
   compatibility checks;
3. all five target kinds round-trip for both note kinds, while every partial,
   hybrid, foreign, draft-code, or wrong-version target fails;
4. excerpt sets remain distinct, passage/C-unit identity and Unicode offsets are
   exact, and no excerpt text or hash enters the qualitative database;
5. exact title stripping, absent Unicode/case/newline normalization, exact body
   preservation, NUL/lone-surrogate rejection, per-field ceilings, and the
   cumulative UTF-8 content budget hold;
6. revisions are immutable, contiguous, attributable, monotonic, and serialized;
   exact retries return one row/event while stale, divergent, and no-op writes
   conflict;
7. note identity and targets cannot update or delete; only the first complete
   tombstone is valid, with same-remover retry and different-remover conflict;
8. missing, foreign, or inactive mutation actors fail while inactive historical
   attribution remains readable;
9. audit insertion failure rolls back create, revise, and remove; malformed,
   missing, duplicate, unmatched, extra-key, or content-bearing audit fails
   strict reads and project validation;
10. wrong SQLite storage classes, padded IDs, invalid timestamps, revision gaps,
    impossible tombstones, and kind/prefix disagreement fail visibly;
11. exact API envelopes, tombstone visibility, bounded collection/history
    pagination, cursor ordering, defaults, and 422/400/404/409 boundaries hold,
    including instrumentation that distinguishes streamed strict validation from
    the `limit + 1` page-materialization queries, malformed JSON, extra fields,
    repeated/unknown queries, Boolean/numeric coercion, and privacy sentinels;
12. format-2 archive/restore preserves every target, revision, ID, actor,
    timestamp, body, tombstone, and audit event exactly;
13. rehashed archive tampering fails isolated preflight before destination
    mutation, and format 1 accepts a non-excerpt note (including a body that
    literally says `evidence_set_id`) but rejects any populated note evidence-set
    column through direct row inspection;
14. archive validation reads only the isolated captured root; and
15. concurrent archive versus create/revise/remove yields the complete note,
    revision chain, and audit mutation or none, without lock inversion or
    deadlock.

The focused service, migration, API, and archive suites run before the full
backend and frontend regression gates. Phase 1 does not claim memo/annotation
completion until all gates pass and the merged master commit is verified.

## Independent Review Record

The first independent review round approved the migration/concurrency design and
identified implementation cautions now incorporated above: nullable trigger
identity comparisons, exact integer storage classes, explicit NUL rejection,
missing-history removal rejection, and full local/audit validation before and
after mutation.

The domain/API and API/archive reviews found three blocking ambiguities. This
revision resolves them by defining every route envelope and target object,
freezing normalization and tombstone visibility, replacing the contradictory
lock-order statement with non-overlapping operation sequences, separating
structural/new-dependency/stored-dependency HTTP outcomes, and bounding complete
collection/history responses with deterministic cursors. It also broadens the
format-1 evidence-set row check and records the cumulative content/archive
capacity boundary.

Final independent re-review approved the complete revised contract in all three
areas. The domain reviewer verified canonical identities, exact envelopes,
normalization, tombstone visibility, bounded pagination, and the Phase 1/2
boundary. The migration/concurrency reviewer verified migration-3 feasibility,
trigger semantics, atomic capacity enforcement, keyset pagination, and lock
non-overlap. The API/archive/security reviewer verified the 422/400/404/409
boundaries, bounded full-content responses, isolated restore preflight,
format-1/format-2 behavior, capacity disclosure, privacy, and adversarial proof.
Schema and runtime implementation may proceed under this contract.
