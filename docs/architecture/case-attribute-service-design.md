# Cases And Typed Attributes Service Design

Status: Proposed Phase 1 implementation contract

Date: 2026-08-01

## Outcome And Boundary

This slice turns the accepted case, attribute, and source-link tables in each
study's `qualitative.sqlite3` into a working backend and FastAPI contract. A named,
active project researcher can create and update cases, define typed attributes,
set or clear validated values, and link an existing project source without
copying evidence content or weakening source identity.

The slice is backend and API only. It does not replace the existing JSON
`StudySchema`, change batch metadata behavior, add a casebook editor, or start
manual coding, memo, matrix, reliability, agent, identity, retention, or deletion
work.

## Decisions And Tradeoffs

- Migration 1 already contains every required table, constraint, index, and ID
  prefix. This slice adds no migration and never edits migration 1.
- Cases are mutable under the accepted contract. An update replaces
  `case_kind`, label, and description while preserving the stable case and project
  IDs and recording the responsible researcher.
- Attribute definitions are create/list only in this slice. Their key, type,
  category choices, and required flag are immutable, which prevents an update
  from silently invalidating stored values. A later definition-edit operation
  requires its own reviewed migration/compatibility contract.
- `required` is descriptive completeness metadata. Cases may be created before
  values exist, and clearing a required value is allowed because there is no
  complete/validated case lifecycle state yet.
- Only an exact source-link retry by the original actor is a no-op because the
  assignment explicitly requires link retry behavior. Case updates and value
  sets are accepted attributed mutations even when content repeats. A missing
  value or link cannot be presented as a successful clear/unlink.
- The existing `StudySchema` and CSV prototype continue unchanged. Automatic
  projection or bidirectional synchronization would create ambiguous ownership,
  so migration is deferred to a separately reviewed compatibility slice.

## Authoritative Storage

The service uses only the existing project database:

```text
local_data/studies/<project_id>/qualitative.sqlite3
```

It uses `QualitativeProjectDatabase.read` for reads and
`QualitativeProjectDatabase.transaction` for accepted writes. It does not open
the database directly and does not create a second case store.

The implementation uses the existing tables unchanged:

- `cases`
- `attribute_definitions`
- `case_attribute_values`
- `source_case_links`
- `researchers`
- `qualitative_audit_events`

The service lives in `backend/qualitative/cases.py`. HTTP models, endpoint routing,
serialization, and HTTP error mapping remain in `backend/app/main.py`.

## Public Records

All returned records are frozen dataclasses. Stored fields are validated as their
declared types rather than coerced with `str(...)`, `int(...)`, or truthiness.
The one explicit representation mapping is `attribute_definitions.required`:
SQLite must return the exact integer `0` or `1`; every other stored type/value is
rejected, and the public Boolean is `raw_required == 1`.

### `CaseRecord`

- `case_id`
- `project_id`
- `case_kind`
- `label`
- `description`
- `created_by`
- `updated_by`
- `created_at`
- `updated_at`

Supported kinds are exactly `participant`, `session`, `dyad`, `condition`, and
`timepoint`.

### `AttributeDefinitionRecord`

- `attribute_definition_id`
- `project_id`
- `attribute_key`
- `label`
- `value_type`
- `allowed_values` as an immutable tuple of strings
- `required` as a boolean
- `created_by`
- `updated_by`
- `created_at`
- `updated_at`

Supported value types are exactly `text`, `number`, `boolean`, `date`, and
`categorical`.

### `CaseAttributeValueRecord`

- `project_id`
- `case_id`
- `attribute_definition_id`
- `attribute_key`
- `value_type`
- `value` as one normalized JSON scalar
- `updated_by`
- `created_at`
- `updated_at`

The attribute key and type are joined from the immutable definition when reading;
they are not duplicated into `case_attribute_values`.

### `SourceCaseLinkRecord`

- `project_id`
- `project_source_id`
- `case_id`
- `linked_by`
- `created_at`

It contains no evidence text, filename, hash, revision, or imported catalog
metadata.

### `CaseSnapshot`

- `case`
- `attribute_values` as an ordered tuple of `CaseAttributeValueRecord`
- `project_source_ids` as an ordered tuple of stable external source IDs

`read_case` returns this expanded view. Other operations return the record they
create or update, or a documented empty result after deletion.

## Public Operations

`CaseService(root, project_id)` exposes:

| Operation | Behavior |
|---|---|
| `create_case(researcher_id, case_kind, label, description="")` | Create and return one stable case record. |
| `update_case(researcher_id, case_id, case_kind, label, description)` | Replace the mutable case fields and return the case record. |
| `read_case(case_id)` | Return one snapshot with typed values and source IDs. |
| `list_cases()` | Return all case records in deterministic order. |
| `create_attribute_definition(researcher_id, attribute_key, label, value_type, allowed_values=(), required=False)` | Create one immutable definition after full validation. |
| `list_attribute_definitions()` | Return all definitions in deterministic order. |
| `set_attribute_value(researcher_id, case_id, attribute_definition_id, value)` | Insert or replace one normalized value and return its joined value record. |
| `read_attribute_value(case_id, attribute_definition_id)` | Return one joined typed value record. |
| `clear_attribute_value(researcher_id, case_id, attribute_definition_id)` | Delete an existing value and return `None`. |
| `link_source(researcher_id, case_id, project_source_id)` | Validate ownership, insert, and return the link. An exact retry by the stored actor returns the original link; a different actor conflicts. |
| `unlink_source(researcher_id, case_id, project_source_id)` | Delete an existing link and return `None`. |
| `validate_project_state()` | Validate every stored case-domain row and staged source ownership for archive restore; return `None`. |

Internally owned identifiers and required labels/keys are non-empty after
trimming. Labels and keys are stored trimmed. Descriptions remain exact strings.
The externally owned `project_source_id` is never normalized: it must be a
nonblank string already equal to its trimmed form, and that exact value is used
for lookup, comparison, and storage.

## Typed Value Normalization

Values are validated against the stored definition before a transaction changes
state. They are encoded with deterministic JSON separators and `allow_nan=False`.

| Type | Accepted input and normalized value |
|---|---|
| `text` | A JSON string. Its exact content is preserved, including meaningful internal or surrounding whitespace. Clearing, rather than an empty-value heuristic, controls absence. |
| `number` | A JSON integer or float that is not a boolean and is finite. Strings, `NaN`, and positive or negative infinity fail. The numeric JSON value is preserved without string conversion. |
| `boolean` | Only the JSON booleans `true` or `false`. Integers and strings fail. |
| `date` | A string exactly equal to one real calendar date's ISO form `YYYY-MM-DD`. Datetimes, alternate formats, and impossible dates fail. |
| `categorical` | A string exactly equal, including case, to one stored allowed value. |

For a categorical definition, `allowed_values` must be a non-empty list of
non-empty strings. Choices are trimmed once at definition creation, preserve case,
and must be unique after trimming. Non-categorical definitions require an empty
allowed-value list. Duplicate attribute keys are project conflicts.

Reads parse `value_json` and validate it again against the immutable definition.
Malformed JSON, non-finite numbers, wrong stored scalar types, invalid dates,
unknown category choices, malformed definition JSON, and invalid stored text or
boolean fields fail as `CaseConflictError`; they are never coerced into research
data.

## Deterministic Reads

- Cases sort by `case_kind`, case-folded label, exact label, then `case_id`.
- Attribute definitions sort by case-folded `attribute_key`, exact key, then
  `attribute_definition_id`.
- A snapshot's assignments use the same definition order.
- `project_source_ids` sort lexicographically.

No ordering relies on SQLite's incidental row order.

## Researcher And Audit Contract

Every accepted mutation runs inside one immediate qualitative transaction and:

1. verifies the supplied `researcher_id` exists in the same project and is active;
2. verifies all case/definition state in the same transaction;
3. changes the domain row(s);
4. appends exactly one attributable `qualitative_audit_events` row;
5. returns only after the transaction commits.

The event contract is:

| Mutation | Event | Subject | Non-sensitive metadata |
|---|---|---|---|
| Create case | `case.created` | case | `case_kind` |
| Update case | `case.updated` | case | empty object |
| Create definition | `case.attribute_definition.created` | attribute definition | `value_type` |
| Set first value | `case.attribute_value.set` | case | `attribute_definition_id`, `value_type` |
| Replace value | `case.attribute_value.replaced` | case | `attribute_definition_id`, `value_type` |
| Clear value | `case.attribute_value.cleared` | case | `attribute_definition_id` |
| Link source | `case.source.linked` | case | `project_source_id` |
| Unlink source | `case.source.unlinked` | case | `project_source_id` |

Attribute values, attribute keys, descriptions, labels, category choices, source
text, hashes, filenames, and transcript content are not copied into append-only
audit metadata. If audit insertion fails, the paired mutation rolls back. Only an
exact source-link retry by the same actor appends no event; it still revalidates
the source, active actor, and case. An existing link attributed to a different
actor is a `CaseConflictError`.

## Source Ownership And Locking

Before a source link transaction:

1. validate that `project_source_id` is already in exact trimmed form;
2. acquire `workspace_mutation_lock(root)`;
3. reject an existing evidence-catalog path that is a symlink or non-regular file;
4. call `EvidenceCatalog(root).source_history(project_source_id)` and require both
   returned `project_source_id` and `workspace_id` to match the requested source
   and qualitative project exactly;
5. map a missing source to `CaseNotFoundError` and translate schema, SQLite, OS,
   symlink, and malformed-row failures into a content-safe `CaseConflictError`;
6. release the workspace lock completely;
7. only then open the qualitative transaction, revalidate the actor and case, and
   insert the stable source ID plus its audit event.

The qualitative row stores only `project_source_id`; it never copies source text,
hashes, revision lineage, filenames, or catalog timestamps.

`EvidenceCatalog` remains the authoritative source registry inside the current
local-filesystem trust boundary. The case service never falls back to ad hoc SQL.
Catalog authenticity and cryptographic integrity remain later production work.

The two databases cannot share a transaction. The catalog read finishes before
the qualitative immediate transaction begins, so cross-store locks are not held
in conflicting order. Project source IDs are currently immutable and have no
deletion workflow. A future source-deletion/lifecycle slice must coordinate
deletion against these links rather than assuming this validation is a foreign
key. Unlink does not consult the catalog, so an attributable cleanup remains
possible if external evidence is later unavailable.

Qualitative writes hold the existing study mutation guard, so project archives
cannot capture the database between the domain row and audit row.

## Archive Restore Preflight

Archive creation already holds the study snapshot guard across the complete
qualitative database file, so it cannot capture a case/value/link without its
paired audit event. Restore needs an additional staged-state preflight before any
member is published:

1. restore the archive's evidence imports into the isolated staging root;
2. if archive member `study/qualitative.sqlite3` (staged as
   `<stage_root>/studies/<project_id>/qualitative.sqlite3`) is absent, retain
   legacy archive compatibility and perform no qualitative initialization;
3. if it is present, open it only through the guarded qualitative database/service
   boundary and validate supported schema version, exact schema definition,
   integrity, foreign keys, project ownership, and every stored case, definition,
   typed value, and source-link row;
4. release the qualitative read/study guard;
5. under the staging root's workspace lock, resolve every distinct stored
   `project_source_id` through the staged `EvidenceCatalog` and require exact
   `workspace_id == project_id`;
6. reject missing/foreign links, invalid stored domain rows, and malformed or newer
   qualitative databases before destination preflight or publication.

The restore path uses a non-domain-mutating
`CaseService.validate_project_state()` operation for this purpose. The shared
database boundary may apply a supported forward schema migration to the isolated
staging copy, but the operation never repairs semantic row content, drops links,
or consults the destination machine's pre-existing evidence catalog. Unsupported
schema versions remain `ProjectArchiveConflict`; malformed/inconsistent staged
content is `ProjectArchiveError` with content-safe detail.

## API Contract

Base path: `/api/studies/{study_id}/qualitative`

Every successful endpoint returns `200` with JSON. Request objects are:

```json
{
  "CaseCreateRequest": {
    "researcher_id": "required string",
    "case_kind": "required string",
    "label": "required string",
    "description": "optional string, default empty"
  },
  "CaseUpdateRequest": {
    "researcher_id": "required string",
    "case_kind": "required string",
    "label": "required string",
    "description": "optional string, default empty"
  },
  "AttributeDefinitionCreateRequest": {
    "researcher_id": "required string",
    "attribute_key": "required string",
    "label": "required string",
    "value_type": "required string",
    "allowed_values": "optional array of strings, default empty",
    "required": "optional strict boolean, default false"
  },
  "AttributeValueSetRequest": {
    "researcher_id": "required string",
    "value": "required uncoerced JSON value"
  },
  "ResearcherActionRequest": {
    "researcher_id": "required string"
  },
  "SourceLinkActionRequest": {
    "researcher_id": "required string",
    "project_source_id": "required exact string"
  }
}
```

`ResearcherActionRequest` is the JSON body for attribute-value DELETE.
`SourceLinkActionRequest` carries the authoritative source identity in the body;
it is never placed in a path segment where `/`, percent encoding, or other
path-significant content could change addressability. The endpoint contract is:

| Method and path | Request model | `200` response envelope |
|---|---|---|
| `POST /cases` | `CaseCreateRequest` | `{"case": CaseRecord}` |
| `GET /cases` | none | `{"cases": [CaseRecord, ...]}` |
| `GET /cases/{case_id}` | none | `CaseSnapshot` |
| `PUT /cases/{case_id}` | `CaseUpdateRequest` | `{"case": CaseRecord}` |
| `POST /attribute-definitions` | `AttributeDefinitionCreateRequest` | `{"attribute_definition": AttributeDefinitionRecord}` |
| `GET /attribute-definitions` | none | `{"attribute_definitions": [AttributeDefinitionRecord, ...]}` |
| `PUT /cases/{case_id}/attributes/{attribute_definition_id}` | `AttributeValueSetRequest` | `{"attribute_value": CaseAttributeValueRecord}` |
| `DELETE /cases/{case_id}/attributes/{attribute_definition_id}` | `ResearcherActionRequest` | `{"cleared": {"case_id": string, "attribute_definition_id": string}}` |
| `PUT /cases/{case_id}/sources` | `SourceLinkActionRequest` | `{"source_link": SourceCaseLinkRecord}` |
| `DELETE /cases/{case_id}/sources` | `SourceLinkActionRequest` | `{"unlinked": {"case_id": string, "project_source_id": string}}` |

Request field names mirror the service names. `case_kind` and `value_type` are
strings validated by the domain so unknown enum values return `400`. The
attribute `value` is structurally required but accepts any JSON value at the HTTP
boundary; definition-aware scalar/type validation returns `400`. The definition
`required` field uses a strict boolean request type, preventing `0`, `1`, and
strings from being coerced.

The exact record shapes are:

```json
{
  "CaseRecord": {
    "case_id": "cas_...",
    "project_id": "study-id",
    "case_kind": "participant",
    "label": "P1",
    "description": "",
    "created_by": "res_...",
    "updated_by": "res_...",
    "created_at": "...",
    "updated_at": "..."
  },
  "AttributeDefinitionRecord": {
    "attribute_definition_id": "atr_...",
    "project_id": "study-id",
    "attribute_key": "consent_date",
    "label": "Consent date",
    "value_type": "date",
    "allowed_values": [],
    "required": true,
    "created_by": "res_...",
    "updated_by": "res_...",
    "created_at": "...",
    "updated_at": "..."
  },
  "CaseAttributeValueRecord": {
    "project_id": "study-id",
    "case_id": "cas_...",
    "attribute_definition_id": "atr_...",
    "attribute_key": "consent_date",
    "value_type": "date",
    "value": "2026-08-01",
    "updated_by": "res_...",
    "created_at": "...",
    "updated_at": "..."
  },
  "SourceCaseLinkRecord": {
    "project_id": "study-id",
    "project_source_id": "psrc_...",
    "case_id": "cas_...",
    "linked_by": "res_...",
    "created_at": "..."
  }
}
```

The exact expanded `GET /cases/{case_id}` shape is:

```json
{
  "case": {
    "case_id": "cas_...",
    "project_id": "study-id",
    "case_kind": "participant",
    "label": "P1",
    "description": "",
    "created_by": "res_...",
    "updated_by": "res_...",
    "created_at": "...",
    "updated_at": "..."
  },
  "attribute_values": [
    {
      "project_id": "study-id",
      "case_id": "cas_...",
      "attribute_definition_id": "atr_...",
      "attribute_key": "consent_date",
      "value_type": "date",
      "value": "2026-08-01",
      "updated_by": "res_...",
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "project_source_ids": ["psrc_..."]
}
```

## Error Mapping

The service defines `CaseNotFoundError`, `CaseValidationError`, and
`CaseConflictError` and contains raw SQLite/catalog failures.

- `422`: malformed JSON, missing request fields, or structurally wrong request
  containers/types rejected by FastAPI/Pydantic.
- `400`: domain enum, typed-value, date, category, blank label/key, or allowed-list
  validation failures.
- `404`: missing study/project/researcher/case/definition/value/link/source.
- `409`: inactive researcher, duplicate attribute key, wrong source workspace,
  unsupported/newer schema, journal/storage conflict, corrupt stored domain data,
  or other ownership/integrity conflict.

Concrete HTTP regressions bind that boundary:

- `required: 1`, `required: "true"`, an object instead of `allowed_values`, a
  missing `value`, and malformed JSON return `422` without calling the service.
- Unknown `case_kind`/`value_type` strings and definition-dependent wrong values
  such as `true` for a number, `null`, arrays/objects, an impossible date, or an
  unknown category return `400`.
- Missing researcher, case, definition, assigned value, link, or evidence source
  returns `404`.
- Inactive researcher, duplicate attribute key, same link with a different actor,
  wrong-workspace source, and a newer qualitative schema return `409`.

Raw SQL, file paths, copied evidence content, and internal catalog details never
appear in HTTP errors.

## Existing `StudySchema` And CSV Compatibility

This slice chooses the assignment's explicit coexistence path:

- `/api/studies/{study_id}/schema`, `study_schema.json`, batch metadata,
  `frontend/src/casebookDesign.ts`, and `frontend/src/casebookCsv.ts` remain
  unchanged and keep their existing behavior.
- The new service is the canonical relational case/attribute model only for new
  case API consumers. It does not infer that `P1`, `week_1`, or condition strings
  are stable qualitative case identities.
- No write in either system updates the other.
- A later reviewed migration must define identity mapping, conflict detection,
  actor attribution, retry/rollback, and whether projection is one-time or
  read-only before either model can replace the other.

Regression gates retain all existing `StudySchema`, casebook CSV, and study-batch
tests to prove this coexistence rather than merely document it.

## Required Proof

Focused service and API tests cover:

- all five case kinds and deterministic create/read/update/list behavior;
- all five value types and exact normalized round trips;
- wrong JSON types, boolean-as-number, non-finite numbers, impossible dates,
  invalid category values, empty choices, and duplicate choices;
- duplicate attribute keys and immutable-definition behavior;
- set, focused read, replace, clear, and missing-value behavior;
- missing/inactive researchers and audit actor/order/metadata;
- rollback of every paired write when audit insertion fails;
- source link success, same-actor exact retry, different-actor conflict, unlink,
  missing source, wrong workspace, and absence of copied evidence content;
- malformed stored definition/value/link/case state failing visibly;
- missing, validation, conflict, and structural HTTP responses;
- newer or structurally tampered qualitative database refusal;
- unchanged `StudySchema`, casebook CSV, and batch behavior;
- archive round trip with a real case, value, and source link, plus restore
  rejection for a missing/foreign linked source and invalid/newer qualitative
  database.

The final branch runs the focused service suite, study/API regressions, complete
backend suite, frontend production build, all eight frontend helper suites,
Python compilation, Ruff, and `git diff --check`.

## Out Of Scope And Rollback

This slice does not implement definition edits/deletion, case deletion,
relationships between cases, case completeness validation, bulk import/export,
automatic `StudySchema` migration, manual transcript coding, memos, annotations,
queries, matrices, reliability/adjudication, agent suggestions, UI controls,
roles/authorization, retention, or source deletion.

Because no migration is added, rollback is code-only. Reverting the service/API
leaves any accepted rows in the already-supported migration-1 tables. Preserve a
verified project archive before running older code that does not expose those
rows; do not delete the database or silently project them back into
`study_schema.json`.
