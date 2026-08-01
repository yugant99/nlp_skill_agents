# Versioned Codebook Service Design

Status: Proposed Phase 1 implementation contract

## Scope And Storage

The service extends the accepted per-study qualitative database at
`local_data/studies/<project_id>/qualitative.sqlite3`. It uses the existing
`codebooks`, `codebook_versions`, `codes`, `researchers`, and
`qualitative_audit_events` tables from migration 1. No schema change or parallel
database is required.

The backend and API in this slice support codebook creation, draft editing,
one-way freezing, draft derivation, and portable JSON import/export. Cases,
coding references, memos, search, reliability, agents, and UI controls remain out
of scope.

## Public Domain Operations

`CodebookService(root, project_id)` exposes:

- `create_codebook(*, researcher_id, title, description="") -> CodebookRecord`
  and `list_codebooks() -> tuple[CodebookRecord, ...]`;
- `create_draft(*, researcher_id, codebook_id) -> CodebookVersionSnapshot` for
  the first version;
- `derive_draft(*, researcher_id, codebook_id, based_on_version_id) ->
  CodebookVersionSnapshot` for a later version copied from one frozen version;
- `add_code(*, researcher_id, codebook_id, codebook_version_id,
  stable_code_key, label, parent_code_id=None, definition="",
  inclusion_criteria="", exclusion_criteria="", examples=(), notes="",
  color="", sort_order=0) -> CodeRecord`;
- `update_code(*, researcher_id, codebook_id, codebook_version_id, code_id,
  label, parent_code_id, definition, inclusion_criteria, exclusion_criteria,
  examples, notes, color, sort_order) -> CodeRecord` for full replacement of
  mutable code content and placement. `stable_code_key` is immutable after
  creation; replacing the represented concept requires a new code;
- `freeze_version(*, researcher_id, codebook_id, codebook_version_id) ->
  CodebookVersionSnapshot`;
- `read_version(*, codebook_id, codebook_version_id) ->
  CodebookVersionSnapshot`;
- `export_version(*, codebook_id, codebook_version_id) -> dict[str, object]`;
  and
- `import_version(*, researcher_id, document) -> CodebookVersionSnapshot` to
  create a new codebook and first draft from a fully validated portable document.

`freeze_version` and `read_version` return `CodebookVersionSnapshot`;
`export_version` returns the documented plain JSON-compatible dictionary.
Returned records are immutable dataclasses:

- `CodebookRecord`: `codebook_id`, `project_id`, `title`, `description`,
  `created_by`, `updated_by`, `created_at`, and `updated_at`;
- `CodebookVersionRecord`: `codebook_version_id`, `project_id`, `codebook_id`,
  `version_number`, `status`, `based_on_version_id`, `created_by`, `created_at`,
  and `frozen_at`;
- `CodeRecord`: every column in `codes`, with `examples_json` decoded to an
  immutable `examples` tuple; and
- `CodebookVersionSnapshot`: one `CodebookRecord`, one
  `CodebookVersionRecord`, and an ordered tuple of `CodeRecord` values.

`list_codebooks` sorts by case-folded title then `codebook_id`. Version reads,
export, derivation, and import use deterministic depth-first hierarchy order.
Siblings sort by `sort_order`, case-folded label, `stable_code_key`, then
`code_id`.

The service exposes four domain error classes. `CodebookNotFoundError` represents
missing project-owned records or researchers. `CodebookValidationError`
represents invalid content or hierarchy. `CodebookConflictError` represents
identity, uniqueness, actor-state, or creation-state conflicts.
`CodebookImmutableError` is the conflict subtype for frozen-state mutations.
SQLite exceptions are translated inside the service and never used as an API
contract.

## Researcher And Transaction Boundary

Every mutation requires an explicit `researcher_id`. Inside the same
`QualitativeProjectDatabase.transaction()` block, the service:

1. verifies the researcher belongs to the project and is active;
2. validates the complete requested domain change;
3. writes the domain rows; and
4. appends one attributable `qualitative_audit_events` row.

The transaction commits both domain state and audit state or rolls both back.
Audit metadata contains stable identifiers, counts, and version numbers, never
transcript content or codebook research text. Reads use the qualitative database
compatibility boundary and never bypass its migration checks.

`QualitativeProjectDatabase.read()` is the shared read boundary for qualitative
services. It holds the existing study mutation guard, verifies/applies compatible
migrations, opens the database with foreign keys enabled, sets `sqlite3.Row`, and
enables `query_only` before yielding the connection. Service modules never call
`sqlite3.connect` directly. This avoids taking `BEGIN IMMEDIATE` for reads while
preserving the study/archive lock order and schema-compatibility boundary.

Both `read()` and `transaction()` reject an existing database path unless it is a
non-symlink regular file. Before yielding, their shared connection preparation:

1. compares the complete live `sqlite_master` signature for the current migration
   version with an in-memory database built from the same ordered migrations;
2. runs SQLite integrity and foreign-key checks;
3. applies any supported forward migrations;
4. repeats the exact-schema and integrity checks at the supported version; and
5. rejects any `qualitative_projects` row owned by a project other than the
   requested study.

Zero project rows remain valid only for schema inspection and first-researcher
bootstrap. Codebook operations require the project row through their active-actor
check. A newer schema still raises `SchemaCompatibilityError`; malformed paths,
schema definitions, triggers, indexes, database pages, foreign keys, or project
ownership raise a content-safe `QualitativeDatabaseConflict`. The database
boundary also translates SQLite/storage failures from project initialization so
`PUT /project` cannot expose raw driver or filesystem errors.

## Version And Hierarchy Rules

- Codebook titles, code labels, and stable code keys are trimmed and non-empty.
- A first draft is version 1 and can be created only when the codebook has no
  versions.
- A derived draft uses the next version number and requires a frozen source
  version from the same codebook.
- Derivation generates new version-local `code_id` values, preserves every
  `stable_code_key`, and reconnects parents through the old-to-new ID map.
- Parent codes must belong to the same project and version.
- Updates reject self-parenting and walk the proposed parent chain to reject
  indirect cycles before writing.
- One shared snapshot validator is used by version read, export, freeze, and
  derivation. It requires every stored code to appear exactly once in one rooted,
  acyclic forest and every parent to resolve inside the same project/version.
  Corrupt stored hierarchy raises `CodebookConflictError`; it is never partially
  returned, frozen, exported, or copied.
- Draft codes may be added or updated. Frozen versions are immutable.
- Freezing is one-way and rejects an empty version. Repeating freeze against the
  same already-frozen version returns the unchanged snapshot, creates no second
  audit event, and returns HTTP `200` through the API.
- Database uniqueness and immutability errors are translated to stable domain
  conflicts; raw SQLite messages never cross the service boundary.

## Portable JSON Contract

Export shape:

```json
{
  "format": "nlp-skill-agents.codebook-version",
  "format_version": 1,
  "codebook": {
    "title": "Interview themes",
    "description": "Researcher-authored thematic codebook"
  },
  "version": {
    "source_version_number": 2,
    "source_status": "frozen"
  },
  "codes": [
    {
      "stable_code_key": "support",
      "parent_stable_code_key": null,
      "label": "Support",
      "definition": "",
      "inclusion_criteria": "",
      "exclusion_criteria": "",
      "examples": [],
      "notes": "",
      "color": "",
      "sort_order": 0
    }
  ]
}
```

The export excludes database IDs, actor IDs, filesystem paths, migration records,
and audit internals. Import accepts only the documented keys and validates the
entire structure, scalar types, the exact
`format = "nlp-skill-agents.codebook-version"` literal, supported
`format_version = 1`, a positive JSON-integer `source_version_number`,
`source_status` in `draft|frozen`, non-negative JSON-integer ordering values
(booleans are not integers), unique stable keys, parent references, and acyclic
hierarchy before opening a write transaction. It then generates new codebook,
version, code, and audit-event IDs; `created_by`, `updated_by`, and audit
`actor_id` use the validated request `researcher_id`. The imported version is
always a new draft regardless of exported source status.

Export/import round-trip equality covers normalized codebook research content,
stable keys, hierarchy, and sibling order. It intentionally excludes source
version number/status, database IDs, actor attribution, and timestamps because
an import creates a separately attributable version-1 draft.

## HTTP Surface And Errors

The FastAPI layer exposes study-scoped endpoints under
`/api/studies/{study_id}/qualitative`:

- `PUT /project` initializes the qualitative project and its first named
  researcher through the existing idempotent initialization contract;
- `POST /codebooks` and `GET /codebooks`;
- `POST /codebooks/import`;
- `POST /codebooks/{codebook_id}/versions`;
- `GET /codebooks/{codebook_id}/versions/{codebook_version_id}`;
- `GET /codebooks/{codebook_id}/versions/{codebook_version_id}/export`;
- `POST /codebooks/{codebook_id}/versions/{codebook_version_id}/codes`;
- `PUT /codebooks/{codebook_id}/versions/{codebook_version_id}/codes/{code_id}`;
  and
- `POST /codebooks/{codebook_id}/versions/{codebook_version_id}/freeze`.

The exact request/response envelopes are:

- project initialization: `{researcher_id, researcher_name}` ->
  `{project: {project_id, researcher: {researcher_id, display_name, role,
  active}}}`;
- codebook create: `{researcher_id, title, description}` -> `{codebook: ...}`;
  codebook list -> `{codebooks: [...]}`;
- version create: `{researcher_id, based_on_version_id: null|string}` -> the
  snapshot envelope `{codebook: ..., version: ..., codes: [...]}`. `null`
  requests the first draft; an ID requests derivation from that frozen version;
- code add: `{researcher_id, stable_code_key, label, parent_code_id, definition,
  inclusion_criteria, exclusion_criteria, examples, notes, color, sort_order}` ->
  `{code: ...}`;
- code replacement: the same mutable content fields without
  `stable_code_key` -> `{code: ...}`;
- freeze: `{researcher_id}` -> the snapshot envelope;
- version read -> the snapshot envelope;
- export -> the portable document itself, without an additional envelope; and
- import: `{researcher_id, document}` -> the snapshot envelope.

Optional text fields default to empty strings, `examples` defaults to an empty
list, `parent_code_id` defaults to `null`, and `sort_order` defaults to zero.
Requests carry `researcher_id` for every mutation after bootstrap. The API maps
missing studies, codebooks, versions, codes, or researchers to `404`; domain
input and hierarchy validation to `400`; inactive researchers, uniqueness
conflicts, immutable versions, incompatible creation state, newer schemas, and
study-mutation or qualitative-database conflicts to `409`. Structural request-body
failures retain the repository's FastAPI/Pydantic `422` convention; a structurally
valid request rejected by `CodebookValidationError` returns `400`. Responses never
expose raw SQLite errors.

## Explicit Non-Goals

This slice does not implement researcher account management, Windows identity,
role authorization, codebook deletion, UI editing, cases or typed attributes,
manual coding, memos or annotations, saved queries, matrices, agreement,
adjudication, agent proposals, cloud/model calls, retention policy, or
cryptographic audit integrity.

The `/project` endpoint exposes only the already accepted first-researcher
bootstrap so this API is usable for a newly created study. Adding, listing,
editing, deactivating, or authorizing researchers remains out of scope.
