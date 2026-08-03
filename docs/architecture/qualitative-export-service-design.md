# Qualitative Export Service Design Contract

Status: Proposed Phase 1 implementation contract

Date: 2026-08-02

## Purpose And Phase Boundary

This slice adds the durable `export` entity, attributable audit, recoverable
filesystem publication, and backup/restore closure required by the Phase 1 exit
gate.

Phase 1 supports exactly one export definition: a portable JSON document for one
frozen codebook version. The document uses the existing
`nlp-skill-agents.codebook-version` format. Reusing that format gives the first
durable export a real import consumer without inventing another research result.

This slice does not export coding-reference results, saved-query results, source
or transcript text, cases, attributes, notes, suggestions, decisions, matrices,
agreement statistics, reports, audit trails, or reproducibility bundles. It does
not execute a query or add an export UI. Query execution belongs to Phase 2;
matrices and publication/reproducibility exports belong to Phase 3.

The existing inline codebook-export endpoint remains unchanged. It may export a
draft and does not create a durable entity. The legacy study bundle and the
verified `.nlpstudy.zip` project archive are also unchanged: the first is a
manifest helper and the second is recovery infrastructure, not a qualitative
research export.

## Authoritative Identity And Storage

- The existing study ID remains `project_id`.
- Each export has a caller-supplied stable ID matching
  `qex_[0-9a-f]{32}`. Caller-supplied identity makes response-loss retries exact
  and non-duplicating.
- The definition references one exact project-owned `codebook_id` and one exact
  `codebook_version_id` belonging to that codebook.
- The database record lives in the existing
  `studies/<project_id>/qualitative.sqlite3` database.
- Migration 6 is the only schema change. Migrations 1 through 5 remain byte-for-
  byte unchanged.
- The artifact path is derived by the server as
  `studies/<project_id>/qualitative_exports/<qualitative_export_id>.json`.
  It is not caller input and is not stored as identity.
- Paths, filenames, hashes, timestamps, titles, version numbers, and artifact
  contents are not export identities.

The `qualitative_export` prefix is added to `new_qualitative_id` for clients and
tests, but the service never silently replaces a supplied ID.

## Definition Version 1

Create accepts this exact nested definition:

```json
{
  "kind": "codebook_version",
  "version": 1,
  "codebook_id": "cbk_<32 lowercase hex>",
  "codebook_version_id": "cbv_<32 lowercase hex>"
}
```

Every key is required. Extra keys, coercive primitive values, paths, filenames,
media types, arbitrary bytes, selection filters, query definitions, format
options, and publication settings are rejected.

The referenced version must exist in this project, belong to the supplied
codebook, be frozen, and contain at least one code. Missing and foreign
dependencies share one not-found response. A draft is a request conflict: the
caller may freeze it through the existing codebook workflow and retry.

## Canonical Artifact

The artifact is the exact object returned by the existing portable codebook
serializer:

```json
{
  "format": "nlp-skill-agents.codebook-version",
  "format_version": 1,
  "codebook": {
    "title": "...",
    "description": "..."
  },
  "version": {
    "source_version_number": 1,
    "source_status": "frozen"
  },
  "codes": []
}
```

Codes retain the established portable fields and deterministic hierarchy order:
`stable_code_key`, `parent_stable_code_key`, `label`, `definition`,
`inclusion_criteria`, `exclusion_criteria`, `examples`, `notes`, `color`, and
`sort_order`. Local row IDs, project IDs, researcher IDs, audit IDs, paths, and
timestamps are intentionally absent from the portable document.

Artifact bytes are:

```text
json.dumps(
    document,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
).encode("utf-8") + b"\n"
```

The final newline is required. Duplicate JSON keys, invalid UTF-8, non-finite
numbers, noncanonical bytes, NUL-bearing strings, invalid hierarchy, unexpected
keys, and a non-frozen source status are invalid. One artifact is limited to
8 MiB. One project is limited to 1,000 completed qualitative exports and 256 MiB
of completed qualitative-export artifacts. A single prepared operation may exist
in addition to those completed records.

The artifact is a local portable file and may contain researcher-authored
codebook text. It is never sent to a model or provider by this subsystem.

## Canonical Request Digest

`request_sha256` is the lowercase SHA-256 of these exact bytes:

```text
b"nlp-skill-agents.qualitative-export-create.v1\0"
+ json.dumps(
    {
      "definition": {
        "codebook_id": <codebook_id>,
        "codebook_version_id": <codebook_version_id>,
        "kind": "codebook_version",
        "version": 1
      },
      "project_id": <project_id>,
      "qualitative_export_id": <qualitative_export_id>,
      "researcher_id": <researcher_id>
    },
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
  ).encode("utf-8")
```

There is no trailing newline or other byte between the domain tag and canonical
request JSON. The digest is retry identity and corruption evidence, not a
credential.

## Migration 6

Migration 6 is named `add-qualitative-export-contract` and adds two tables.

```text
qualitative_export_operations
  qualitative_export_id  primary key
  project_id              owning project
  export_kind             codebook_version
  definition_version      1
  codebook_id              exact source codebook
  codebook_version_id      exact frozen source version
  request_sha256           canonical request digest
  artifact_sha256          digest reserved before filesystem publication
  artifact_size_bytes      positive bounded byte count
  requested_by             project researcher
  requested_at             canonical UTC reservation timestamp
  status                   prepared | completed
  completed_at             null until completion

qualitative_exports
  qualitative_export_id  primary key
  project_id              owning project
  export_kind             codebook_version
  definition_version      1
  codebook_id              exact source codebook
  codebook_version_id      exact frozen source version
  request_sha256           canonical request digest
  artifact_sha256          exact published digest
  artifact_size_bytes      exact published byte count
  created_by               project researcher
  created_at               canonical completion timestamp
```

Both tables have exact SQLite type/value checks, project-scoped foreign keys, and
indexes for deterministic project listing. `qualitative_exports` also references
its matching operation. A partial unique index permits at most one `prepared`
operation per project.

Operation inserts must start as `prepared` with `completed_at = null`. A trigger
permits exactly one update: the immutable `prepared` row may become `completed`
with a canonical completion timestamp after the matching export row exists.
Every other update and every physical delete is rejected. Export rows are
immutable and cannot be deleted.

A later reviewed migration may add an explicit abandoned/reconciled terminal
state. Migration 6 does not guess a lease, timeout, owner process, retry count,
or failure policy for the separate reconciliation/lifecycle slice.

## Mutation, Retry, And Audit Protocol

`create_qualitative_export` uses this order:

1. Strictly validate and canonicalize the request and request digest.
2. In an initial qualitative read, scan the operation/export family. If the
   supplied ID already exists, validate its complete stored identity before
   deciding whether it is an exact retry or divergent reuse.
3. For a new ID, begin one immediate qualitative transaction. Re-probe the ID,
   require the initialized project and active actor, require the exact frozen
   codebook version, build and bound the canonical artifact, enforce count and
   byte capacity, and insert one `prepared` operation containing the reserved
   artifact digest and size.
4. Release the qualitative transaction and its study-operation guard.
5. Under `workspace_mutation_lock` only, validate the study/export directory
   ancestors. If the final artifact exists, require exact safe bytes. Otherwise
   publish the already-reserved bytes with a same-directory fsynced temporary
   file and an exclusive no-overwrite publication step. Never overwrite
   divergent bytes.
6. Release the workspace lock.
7. Begin a second immediate qualitative transaction. Revalidate the exact
   operation, source dependency, and capacities. Inside that transaction,
   acquire `workspace_mutation_lock`, read and verify the final artifact through
   a no-follow regular-file handle, then insert the immutable export row, append
   exactly one audit event, and advance the operation to `completed`. Release
   the workspace lock before committing the qualitative transaction.
8. Re-read and strictly validate the completed entity before returning it.

The final transaction follows the established lock order: the qualitative
transaction owns the study-operation guard before it acquires the workspace
lock. Code must never hold the workspace lock and then open a qualitative
read/transaction.

The service does not store exception messages or mark a speculative failure
state. A caught or hard failure leaves a visible `prepared` operation. An exact
retry resumes it from its reserved source and artifact identity. A different ID
cannot bypass it. The later reconciliation slice may add explicit takeover or
abandonment after reviewing ownership and age semantics.

The final audit event is:

```text
event_type   qualitative_export.created
subject_type qualitative_export
subject_id   <qualitative_export_id>
actor_id     <requested_by>
created_at   <export created_at / operation completed_at>
metadata     {"export_kind":"codebook_version","format_version":1}
```

Audit metadata contains no path, filename, digest, size, title, description,
codebook ID, version ID, code labels, notes, request hash, or error text. The
export row, audit event, and operation completion commit atomically. Exact retry
creates no second artifact, row, or event.

## Crash And Concurrency Semantics

| Interruption | Durable state | Exact retry |
|---|---|---|
| Before reservation commit | Nothing | Starts normally. |
| After reservation, before final file | Prepared operation only | Publishes reserved bytes. |
| During temporary write | Prepared operation; final absent; possible scoped temp | Refuses unexpected temp until reconciliation removes it safely. |
| After final publication | Prepared operation plus exact final artifact | Adopts exact bytes and finalizes. |
| During final DB transaction | Artifact remains; row/audit/completion roll back together | Finalizes once. |
| After final commit/response loss | Completed operation, row, audit, artifact | Verifies and returns existing entity. |
| Completed artifact missing or changed | Completed database closure but invalid filesystem state | Conflicts; never silently regenerates. |

Concurrent exact creates may both encounter the same prepared reservation and
artifact, but converge on one completed row and one audit. Concurrent divergent
reuse conflicts on the canonical request digest. A concurrent different-ID
create conflicts while the project has a prepared operation.

Project archive lock order is `archive_snapshot_guard` then
`workspace_mutation_lock`. Therefore an archive sees either completed closure or
a prepared operation. Staged validation rejects the latter; no successful
archive can represent half an export. A transient concurrent archive is allowed
to fail visibly and be retried.

## Strict Stored-State Validation

Every public create/read/list/download and archive preflight validates stored
state rather than trusting SQLite affinity or a selected row:

- scan the complete operation, export, and relevant export-audit families so a
  foreign-project or malformed row cannot hide behind a filter;
- enforce exact column types, canonical IDs, hashes, byte counts, timestamps,
  statuses, request digests, project/actor ownership, and source relationships;
- recompute the exact portable artifact from the current frozen source version
  and require its digest/size to match the reservation and completed entity;
- require every completed operation to have exactly one matching immutable
  export row and exactly one byte-for-byte canonical audit event;
- require a prepared operation to have no export row or creation audit and to
  have either no final artifact or the exact reserved final artifact;
- require the managed directory to contain exactly the expected final files;
  reject symlinks, non-regular nodes, orphans, temporary files, case-colliding
  names, subdirectories, and unexpected members;
- open artifacts without following symlinks, compare descriptor and path
  identity, enforce the byte limit before reading, then require exact size,
  SHA-256, UTF-8, JSON shape, semantics, and canonical bytes;
- reject unmatched, duplicate, padded, case-folded, binary-marker, extra-key, or
  content-bearing export audit records; and
- enforce the per-project count and total-byte capacities.

List filtering never hides a stored defect. Download verifies already-persisted
bytes and never creates, repairs, or regenerates an artifact.

## Read, List, And Download Contract

The service exposes:

| Operation | Behavior |
|---|---|
| `create_qualitative_export(...)` | Attributable exact-retry creation. |
| `read_qualitative_export(id)` | Strict completed project-owned read. |
| `list_qualitative_exports(created_by=None, limit=20, cursor=None)` | Bounded deterministic completed list. |
| `read_artifact(id)` | Strict verified persisted bytes for download. |
| `validate_project_state(require_closed=True)` | Complete archive/recovery preflight. |

Read/list/download fail visibly when any prepared operation exists. The create
path alone may resume the exact prepared ID. A future reconciliation API may
expose content-safe operation status; Phase 1 does not expose internal stages.

List order is `(created_at, qualitative_export_id)` ascending. `limit` is a
canonical ASCII decimal from 1 through 50 at the API boundary. `created_by` is an
exact researcher ID. Cursors follow the existing strict compact-JSON,
unpadded-base64url, project/filter-bound pattern and are bounded before decoding
and parsing.

## HTTP Contract

Create:

```http
POST /api/studies/{study_id}/qualitative/exports
```

```json
{
  "qualitative_export_id": "qex_<32 lowercase hex>",
  "researcher_id": "res_<32 lowercase hex>",
  "definition": {
    "kind": "codebook_version",
    "version": 1,
    "codebook_id": "cbk_<32 lowercase hex>",
    "codebook_version_id": "cbv_<32 lowercase hex>"
  }
}
```

Pydantic request/query models use `ConfigDict(extra="forbid", strict=True)`.
Every nested key is required. The API also exposes:

```http
GET /api/studies/{study_id}/qualitative/exports
GET /api/studies/{study_id}/qualitative/exports/{qualitative_export_id}
GET /api/studies/{study_id}/qualitative/exports/{qualitative_export_id}/download
```

Create and read return:

```json
{
  "qualitative_export": {
    "qualitative_export_id": "qex_...",
    "project_id": "study-id",
    "definition": {
      "kind": "codebook_version",
      "version": 1,
      "codebook_id": "cbk_...",
      "codebook_version_id": "cbv_..."
    },
    "artifact": {
      "media_type": "application/json",
      "size_bytes": 1234,
      "sha256": "<64 lowercase hex>",
      "download_url": "/api/studies/study-id/qualitative/exports/qex_.../download"
    },
    "created_by": "res_...",
    "created_at": "<canonical UTC timestamp>"
  }
}
```

No response exposes an absolute/relative artifact path, journal status, request
digest, audit ID, temporary filename, SQL, or raw exception.

Download returns the verified bytes already read in memory as
`application/json` with
`Content-Disposition: attachment; filename="<qualitative_export_id>.json"`.
It does not use a path-reopening response after validation.

Exception mapping is content-safe:

- `422`: malformed/unknown/missing/coercive request fields, invalid query syntax,
  and repeated query parameters — `Request validation failed`;
- `400`: invalid IDs, unsupported definition, capacity, or artifact-size request
  — `Qualitative export request is invalid`;
- `404`: unknown study, export, or supplied dependency, without ownership
  disclosure;
- `409`: inactive actor, draft dependency, divergent retry, unresolved prepared
  operation, newer schema, stored corruption, missing/altered artifact, unsafe
  path, or storage failure — `Qualitative export state conflicts with stored data`.

No update, delete, publish, share, upload, arbitrary filename, raw-directory,
query execution, CSV, PDF, matrix, report, or reproducibility endpoint is added.

## Archive And Restore Contract

Archive format 2 remains unchanged. Its existing recursive study capture includes
the managed artifact and qualitative database. Staged validation adds
`QualitativeExportService(stage_root, study_id).validate_project_state()` after
evidence/catalog reconstruction and before destination mutation.

Archive creation/restore rejects prepared operations, missing or extra artifacts,
unsafe filesystem nodes, malformed operation/export/audit closure, dependency
drift, and artifact mismatch. Schema/newer-version failures translate to
`ProjectArchiveConflict`; malformed semantic or artifact state translates to a
content-safe project-archive error. Restore validates the isolated staged root
before destination preflight and publication.

Format 1 directly rejects any populated `qualitative_export_operations` or
`qualitative_exports` table and any `study/qualitative_exports/` member. It does
not search arbitrary JSON for export IDs. Empty/absent tables and an absent
managed directory remain compatible.

## Focused Adversarial Verification

The implementation must cover:

1. exact response-loss retry, divergent ID reuse, inactive actor, draft/missing/
   foreign dependency, artifact/count/byte capacity, and concurrent exact/
   divergent creates;
2. interruption after reservation, during temporary write, after final
   publication, during export-row insert, during audit insert, during operation
   completion, and after commit, with exactly one final entity/artifact/audit;
3. missing, altered, truncated, oversized, noncanonical, duplicate-key, invalid
   UTF-8, deeply nested, or semantically forged artifacts;
4. symlinked ancestors/artifact, FIFO or other non-regular nodes, orphan/extra/
   temporary/case-colliding files, and no external-path writes;
5. forged SQLite primitive types, project/actor/source ownership, request or
   artifact hashes, sizes, timestamps, statuses, source mappings, illegal
   closure, and unmatched/duplicate/content-bearing audits;
6. hidden corruption that list filters and pagination cannot suppress;
7. traversal/confusable IDs, slash/backslash/percent forms, response-header
   injection attempts, and privacy sentinels in API errors;
8. format-2 round trip, rehashed semantic tampering before restore publication,
   concurrent archive/create closure, unresolved-operation rejection, direct
   format-1 table/member rejection, and empty format-1 compatibility; and
9. all existing qualitative, archive, API, backend, frontend, and repository
   helper regression gates.

## Explicitly Deferred

- saved-query execution and result snapshots;
- coded-excerpt, case, note, suggestion, decision, or source-text exports;
- matrices, denominators, agreement, adjudication, reports, audit-trail exports,
  publication bundles, and reproducibility bundles;
- export deletion, retention expiry, abandonment, takeover, and stale-temporary
  cleanup, which require the following reconciliation/lifecycle contract;
- researcher-facing export UI; and
- provider calls, model data, credentials, egress, retries, jobs, or agent output.
