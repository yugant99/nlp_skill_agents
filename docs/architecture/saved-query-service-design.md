# Saved Query Service Design Contract

Status: Reviewed Phase 1 implementation contract

Date: 2026-08-02

## Purpose And Phase Boundary

This slice adds the durable `saved query` entity required by the Phase 1 exit
gate. It persists an attributable, immutable definition of the exact coding-
reference filters the backend already supports.

It does not execute a saved query, add search UI, add text or label search, join
cases or attributes, calculate a matrix, freeze a result set, or create an export
artifact. Search and manual retrieval belong to Phase 2. Matrices and publication
or reproducibility exports belong to Phase 3. The qualitative-export entity will
follow in a separate contiguous migration because a filesystem artifact needs a
recoverable database/filesystem operation contract that this SQLite-only slice
does not need.

## Authoritative Identity And Storage

- The existing study ID remains `project_id`.
- Each saved query has a caller-supplied stable ID matching
  `qry_[0-9a-f]{32}`. Caller-supplied identity makes a response-loss retry exact
  and non-duplicating.
- Saved queries live in the owning study's existing
  `studies/<project_id>/qualitative.sqlite3` database.
- Migration 5 is the only schema change. Migrations 1 through 4 remain byte-for-
  byte unchanged.
- The `saved_query` prefix is added to `new_qualitative_id` for clients and tests,
  but the service does not silently replace a supplied ID.
- Titles, timestamps, cursor positions, labels, filesystem paths, and filter
  values are not identities.

## Definition Version 1

Phase 1 accepts one non-executable definition shape:

```json
{
  "kind": "coding_reference_filter",
  "version": 1,
  "filters": {
    "project_source_id": null,
    "codebook_version_id": null,
    "code_id": null,
    "created_by": null,
    "include_removed": false
  }
}
```

Every key is required, including null-valued optional filters. Extra keys,
coercive primitive values, arrays, SQL, expressions, paths, URLs, arbitrary JSON,
and evidence or transcript text are rejected.

The five filters have exactly the semantics of
`CodingReferenceService.list_references`:

- project scope is implicit;
- non-null filters are exact, conjunctive matches;
- `include_removed = false` means active references only;
- `include_removed = true` means active and removed references;
- the existing deterministic result order is `created_at`, then
  `coding_reference_id`.

An all-null definition with `include_removed = false` is valid. Persisting the
definition does not execute it and does not store a count or result snapshot.
Changing these semantics requires a later definition version; version 1 is never
silently reinterpreted.

## Input And Dependency Validation

The service validates before accepting a create:

- `saved_query_id` is an exact lowercase `qry_` ID;
- `researcher_id`, `codebook_version_id`, `code_id`, and filter `created_by`
  values are bounded stable lowercase entity IDs when present;
- `project_source_id` is a bounded, non-empty, unpadded, NUL-free source ID when
  present;
- `include_removed` is a JSON Boolean, not `0`, `1`, or a string;
- the title is non-empty, unpadded, NUL-free UTF-8 with at most 256 Unicode code
  points and 1,024 UTF-8 bytes;
- the acting researcher exists in the project and is active;
- a source filter resolves through `EvidenceCatalog.source_history` and belongs
  to this project;
- local codebook-version, code, and researcher filters exist in this project;
- when both codebook version and code are supplied, the code belongs to that
  version.

Dependency checks do not expose whether a valid identifier belongs to another
project. Missing and foreign supplied dependencies share the same not-found API
response.

## Canonical Persistence

Migration 5 adds one table:

```text
saved_queries
  saved_query_id          primary key
  project_id              owning project
  title                   bounded display title
  query_kind              coding_reference_filter
  definition_version      1
  filters_json             canonical full-shape filter object
  request_sha256          canonical create-request digest
  created_by              project researcher
  created_at              canonical UTC timestamp
```

`filters_json` is the exact UTF-8 encoding of
`json.dumps(filters, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
allow_nan=False)`, with no trailing newline. It contains exactly the five filter
keys.

`request_sha256` is the lowercase SHA-256 of these exact bytes:

```text
b"nlp-skill-agents.saved-query-create.v1\0"
+ json.dumps(
    {
      "definition": {
        "filters": <the full-shape filters object>,
        "kind": "coding_reference_filter",
        "version": 1
      },
      "project_id": <project_id>,
      "researcher_id": <researcher_id>,
      "saved_query_id": <saved_query_id>,
      "title": <title>
    },
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False
  ).encode("utf-8")
```

There is no trailing newline or other byte between the domain tag and canonical
JSON. The digest is retry identity and corruption evidence, not a credential.

The table has project-scoped foreign keys, exact type/value checks, an index for
`(project_id, created_at, saved_query_id)`, and a creator-list index. Triggers
reject every update and physical delete. Phase 1 does not rename, revise,
tombstone, or remove a saved query. A changed definition or title receives a new
saved-query ID. Later revision or retention behavior requires a new reviewed
migration.

The service enforces a maximum of 10,000 saved queries per project. Reads fail
visibly if stored rows exceed this bound.

## Mutation, Retry, And Audit Contract

`create_saved_query` uses a three-stage order so retry identity is decided before
new-mutation gates and the workspace lock never overlaps a qualitative
transaction:

1. Strictly validate and canonicalize the request.
2. In an initial qualitative read, require the initialized project and probe the
   caller ID. If present, strictly validate its row, local relations, and audit,
   then compare every canonical field and the request digest. Divergent reuse is
   immediately a conflict. Retain the exact validated candidate for the next
   stages.
3. With no qualitative connection open, validate a supplied source filter under
   the workspace mutation lock. A missing/foreign source is not found for a
   genuinely new create, but is a stored-state conflict for an existing exact
   candidate.
4. Begin one immediate qualitative transaction and probe the caller ID again.
   If it is now present, strictly validate and compare it. An exact candidate is
   returned without requiring an active actor or unused capacity; a divergent or
   damaged candidate conflicts.
5. Only when the ID is still absent, require the active actor, local filter
   dependencies, and project capacity, then insert the row and exactly one audit
   event in the same transaction.
6. Re-read and strictly validate the accepted row and audit before commit.

This ordering lets an exact response-loss retry succeed after its actor is later
deactivated or the project reaches capacity. It never lets those states authorize
a new insert. A concurrent same-ID exact create converges on one row/event; a
concurrent divergent create conflicts.

The audit event is:

```text
event_type   saved_query.created
subject_type saved_query
subject_id   <saved_query_id>
actor_id     <created_by>
created_at   <saved-query created_at>
metadata     {"definition_version":1,"query_kind":"coding_reference_filter"}
```

The event contains no title, filter values, source ID, code ID, path, request
hash, evidence text, SQL, or raw error. Exact retry creates no second event. A
row cannot commit without its event, and an event cannot commit without its row.

## Strict Stored-State Validation

Every public read validates storage rather than trusting SQLite affinity:

- exact column types and canonical ID/timestamp/hash forms;
- canonical title and `filters_json` bytes;
- the exact definition shape and allowed values;
- recomputed request digest equality;
- project ownership and all local relations;
- exactly one byte-for-byte canonical audit event per saved query;
- no unmatched, duplicate, padded, case-folded, binary-marker, extra-key, or
  content-bearing saved-query audit;
- the 10,000-row project capacity; and
- source-filter ownership through the external evidence catalog.

Malformed hidden rows do not disappear behind an API filter. Public read/list and
archive validation stream and validate the complete saved-query family, collect
unique source dependencies with bounded memory, release the qualitative read,
and then validate those dependencies under the workspace lock. Any stored defect
is a conflict.

## Read And Pagination Contract

The service exposes:

| Operation | Behavior |
|---|---|
| `create_saved_query(...)` | Attributable exact-retry create. |
| `read_saved_query(saved_query_id)` | Strict project-owned read. |
| `list_saved_queries(created_by=None, limit=20, cursor=None)` | Bounded deterministic list. |
| `validate_project_state()` | Complete archive/recovery preflight. |

List order is `(created_at, saved_query_id)` ascending. `limit` is a canonical
ASCII decimal from 1 through 50 at the API boundary. The optional `created_by`
filter is an exact researcher ID.

Cursors use only the ASCII base64url alphabet `[A-Za-z0-9_-]`, never `=` padding.
The encoded input is rejected before decoding when it exceeds 4,096 characters;
decoded JSON is rejected before parsing when it exceeds 3,072 bytes. Cursors are
canonical JSON encoded with `sort_keys=True`, compact separators,
`ensure_ascii=False`, and `allow_nan=False`, then unpadded base64url encoded. They
have this semantic shape:

```json
{
  "version": 1,
  "project_id": "study-id",
  "endpoint": "saved_queries",
  "filters": {"created_by": null},
  "anchor": {
    "created_at": "canonical timestamp",
    "saved_query_id": "qry_..."
  }
}
```

The cursor must round-trip byte-for-byte to its canonical encoding. Non-ASCII or
non-base64url characters, padding, unknown keys, duplicate JSON keys,
noncanonical JSON/timestamps, either oversize bound, wrong
project/endpoint/filter bindings, and malformed anchors are invalid. A missing
anchor is not found. An anchor that no longer matches its bound filter or whose
complete local, audit, and external source closure is invalid is a conflict. The
anchor closure is validated even though the anchor is not returned on the next
page.

## API Contract

Routes are project-owned JSON endpoints:

| Method and path | Success envelope |
|---|---|
| `POST /api/studies/{study_id}/qualitative/saved-queries` | `200 {"saved_query": {...}}` for create or exact retry |
| `GET /api/studies/{study_id}/qualitative/saved-queries` | `200 {"saved_queries": [...], "next_cursor": null|string}` |
| `GET /api/studies/{study_id}/qualitative/saved-queries/{saved_query_id}` | `200 {"saved_query": {...}}` |

The exact create body is nested as follows; no key has a default:

```json
{
  "saved_query_id": "qry_...",
  "researcher_id": "res_...",
  "title": "Active coding by owner",
  "definition": {
    "kind": "coding_reference_filter",
    "version": 1,
    "filters": {
      "project_source_id": null,
      "codebook_version_id": null,
      "code_id": null,
      "created_by": "res_...",
      "include_removed": false
    }
  }
}
```

Every `saved_query` response has exactly these public fields:

```json
{
  "saved_query_id": "qry_...",
  "project_id": "study-id",
  "title": "Active coding by owner",
  "definition": {
    "kind": "coding_reference_filter",
    "version": 1,
    "filters": {
      "project_source_id": null,
      "codebook_version_id": null,
      "code_id": null,
      "created_by": "res_...",
      "include_removed": false
    }
  },
  "created_by": "res_...",
  "created_at": "canonical timestamp"
}
```

`request_sha256` and raw `filters_json` are never returned. Request and query
models use strict primitive types and forbid extra fields. Unknown or repeated
scalar query parameters are a scrubbed 422. A syntactically valid `created_by`
list filter that has no matching saved query returns an empty page; it is not a
researcher-existence probe. The route-scoped validation handler returns only
`{"detail":"Request validation failed"}`.

Domain errors are content-safe and fixed:

- 400: request semantics are invalid;
- 404: study, saved query, cursor anchor, or supplied dependency is unavailable;
- 409: inactive actor, divergent retry, corrupt/newer storage, archive race, or
  other stored-state conflict.

No error echoes a title, filter value, identifier from stored content, SQL,
filesystem path, hash, or raw exception.

## Archive And Restore Contract

Project archive format remains 2. The qualitative database is already a regular
study member, so no new filesystem member or archive-format bump is needed.

Archive creation and staged restore call `SavedQueryService.validate_project_state`
against the isolated captured/staged root. A format-2 archive is accepted only if
every saved query, audit, local dependency, and source-filter ownership check is
complete. Concurrent archive versus saved-query creation contains both the row
and audit or neither because both paths share the study mutation guard.

Format 1 directly rejects any populated `saved_queries` table. It does not scan
free-form JSON looking for identifiers. A format-1 archive with an empty or absent
table may still restore and migrate normally.

Restore validation completes before destination mutation. Schema compatibility
errors remain conflicts; malformed domain state is a generic archive error. The
archive-create API maps conflicts to the fixed detail
`Project archive state conflicts with stored data` and invalid requests to
`Project archive request is invalid`. The restore API maps conflicts to
`Project restore conflicts with stored data` and invalid requests to
`Project restore request is invalid`. Neither endpoint returns `str(exc)`, raw
schema text, exception text, identifiers from stored filters, or other stored
content.

## Focused Adversarial Proof

Before landing, tests cover at least:

- migration 4 to 5 upgrade, exact schema, rollback, newer-schema refusal, and ID
  prefix behavior without modifying migrations 1 through 4;
- create, exact retry, divergent ID retry, inactive/missing actor, capacity, and
  transaction rollback when audit insertion fails;
- concurrent same-ID exact/divergent creates, exact retry after actor
  deactivation, exact retry at capacity, and stored-source disappearance;
- strict definition shape/types, canonical JSON/digest/title/timestamp storage,
  foreign/missing source and local dependency rejection;
- missing/duplicate/unmatched/padded/binary/extra-key/content-bearing audits;
- hidden-row corruption, malformed SQLite types, relation corruption, and
  over-capacity reads;
- bounded pagination, both cursor size bounds, padding/nonalphabet/duplicate-key/
  unknown-key/alternate-encoding rejection, cross-project/cross-filter cursor
  rejection, missing anchor, and complete cursor-anchor source preflight;
- strict API bodies, repeated query parameters, content-safe 400/404/409/422
  responses, and no content/path/hash leakage;
- format-2 round-trip, rehashed semantic tampering, isolated validator use,
  concurrent archive/create atomicity, direct format-1 populated-table rejection,
  and archive-create/restore privacy sentinels that prove raw exception and saved-
  query content are not returned.

The focused migration, service, API, and archive suites plus the complete backend,
frontend build, and all established frontend helper gates must pass on the final
branch state.

## Explicit Deferrals

This slice does not add saved-query execution, arbitrary expressions, text search,
case/attribute joins, OR/grouping, target-kind filters, query revisions, query
deletion, result snapshots, counts, matrices, CSV, publication exports,
reproducibility bundles, UI, authentication, cryptographic audit, encryption,
retention policy, provider calls, or model output.
