# Canonical Evidence Targets And Coding Reference Service Design

Status: Reviewed Phase 1 implementation contract

Date: 2026-08-01

## Outcome And Required Prerequisite

This slice makes a human coding relationship durable and portable. An active
project researcher can apply one exact code from one frozen codebook version to
an exact span of one project-owned passage or C-unit, read and list that
relationship, and remove it without losing who created or removed it.

Read-only discovery found that the current stable-ID layer is necessary but not
sufficient for this contract:

- passage and C-unit IDs are hashes of parent IDs plus ordinals, not hashes of
  the represented text;
- analysis turns and Descript events can assign the same passage ordinal under
  different parsing contracts;
- a coordination decision can issue two C-unit IDs while persisting only one
  passage-level `cleaned_text`, so the individual unit texts are not recoverable;
- the evidence catalog stores sources and revisions but no passage or C-unit
  membership; and
- root-level segmentation snapshots are not project archive members.

Accepting a well-formed `psg_*` or `cun_*` value would therefore turn a
derivable identifier into false existence proof. Before coding references are
written, this slice adds the smallest durable prerequisite: an immutable central
evidence-target registry and content-addressed UTF-8 evidence text blobs. The
registry is populated by the existing analysis and segmentation persistence
paths and exported only for the owning project.

This prerequisite is Phase 1 evidence infrastructure. It does not add a manual
workbench, editor, coding stripes, undo UI, or model/provider behavior.

## Decisions And Tradeoffs

- `project_source_id`, not content-derived `source_id`, is the project-owned
  source identity. A coding reference always carries the exact source ID and
  transcript revision that were resolved.
- Existing `passage_id` and `cunit_id` formats remain unchanged. Because those
  IDs omit parser/adjudicator identity, their effective target identity is the
  tuple `(evidence_set_id, passage_id, cunit_id)`.
- An evidence set is one immutable, complete interpretation produced from one
  import by one versioned producer contract. Multiple interpretations may
  coexist; the service never picks one implicitly.
- Exact normalized transcript content and exact target text are retained in a
  separate content-addressed UTF-8 blob store. The evidence database stores
  hashes and lengths, not transcript text.
- A coding span uses zero-based Python Unicode code-point offsets into the exact
  canonical target text. `start_offset` is inclusive and `end_offset` is
  exclusive. This creates one explicit coordinate contract instead of guessing
  offsets in raw TXT/DOCX bytes.
- The caller supplies IDs and offsets but never supplies an authoritative
  excerpt or excerpt hash. The immutable target identity plus offsets is the
  selection identity; no short-text hash is persisted or exposed.
- Only codes in a frozen codebook version can be applied. The service checks
  this inside its write transaction and migration 2 adds a database trigger so
  direct SQL cannot create a draft-target coding.
- Coding removal is a one-way attributable tombstone. The original coding row,
  creator, evidence tuple, code target, and creation time remain intact; only
  `removed_by` and `removed_at` are added. Reapplying later creates a new coding
  reference ID.
- An exact active create retry by the same coder is idempotent and appends no
  second audit event. A second coder may independently apply the same code to
  the same span. An exact remove retry by the stored remover is also idempotent;
  a different remover conflicts.
- Legacy segmentation snapshots stay readable. A snapshot without explicitly
  persisted per-C-unit canonical text is not silently upgraded into accepted
  evidence. It must be reverified by the current producer before its C-units can
  be registered and coded.
- Only a pipeline-`verified` segmentation run with the current explicit C-unit
  text contract can register a coding-eligible C-unit evidence set. Scientific
  review status remains separately `not_domain_validated`.
- Phase 1 adds a study-scoped segmentation create/read/list/reverify API so
  project-owned C-unit evidence has a real public path into the coding service.
  It adds no segmentation or coding UI.
- New project archives use format version 2. The new reader remains compatible
  with eligible version-1 archives; an old reader rejects version 2 rather than
  silently discarding target members it does not understand.
- The existing local audit boundary provides attribution, not authentication or
  tamper evidence. No request-supplied researcher ID is represented as a
  cryptographically proven user.

## Authoritative Storage

Three existing root/project boundaries remain authoritative:

```text
local_data/evidence.sqlite3
local_data/evidence_text_blobs/sha256/<prefix>/<sha256>.utf8
local_data/studies/<project_id>/qualitative.sqlite3
```

`evidence.sqlite3` owns source/revision lineage and the new immutable target
registry. `evidence_text_blobs` owns verified canonical UTF-8 text. The
per-study qualitative database owns accepted coding relationships and their
audit events. No transcript or excerpt text is copied into the qualitative
database or append-only audit metadata.

Cross-database foreign keys are impossible. The coding service therefore uses a
two-stage validation/write flow and strict revalidation during archive preflight.

All evidence-catalog and target-registry operations use one shared prepared
connection boundary. An existing `evidence.sqlite3` must be a non-symlink
regular file. The boundary enables foreign keys, disables trusted schema, checks
the exact `sqlite_master` signature expected for the recorded migration version,
runs integrity and foreign-key checks, applies only supported forward migrations,
and repeats those checks afterward. Reads keep that validated connection open in
query-only mode; writes keep it open through `BEGIN IMMEDIATE` and commit or
rollback. No method validates one connection and then reopens an unchecked one.
Newer versions, migration-ledger disagreement, unexpected/missing tables,
indexes, or triggers, malformed paths, integrity failures, and invalid foreign
keys are content-safe conflicts.

## Evidence Catalog Migration 4

The evidence catalog's next contiguous migration is migration 4. Migrations 1,
2, and 3 remain byte-for-byte unchanged. Migration 4 adds:

### `evidence_sets`

| Column | Contract |
|---|---|
| `evidence_set_id` | Primary key, `evs_` plus 32 lowercase hexadecimal characters. |
| `import_id` | Existing immutable source import. |
| `project_source_id` | Exact project-owned source ID. |
| `transcript_revision_id` | Exact imported revision. |
| `producer_kind` | Exact supported producer enum. |
| `producer_version` | Positive integer contract version. |
| `producer_status` | Exact immutable eligibility status; initially only `verified`. |
| `review_status` | Exact bounded status; it must not imply scientific validation. |
| `transcript_text_sha256` | Full SHA-256 of exact canonical transcript UTF-8 bytes. |
| `snapshot_sha256` | Full SHA-256 of the canonical ordered passage/C-unit manifest. |
| `passage_count` | Non-negative exact SQLite integer. |
| `cunit_count` | Non-negative exact SQLite integer. |
| `created_at` | Timezone-aware immutable creation timestamp. |

Migration 4 adds unique parent keys on
`source_imports(import_id, project_source_id, transcript_revision_id)` and
`transcript_revisions(transcript_revision_id, transcript_sha256)`. The set row
uses composite foreign keys to both of those exact tuples and to
`source_revisions(project_source_id, transcript_revision_id)`. Its
`transcript_text_sha256` is therefore structurally bound to the catalog's
revision digest rather than merely checked beside it. `created_at` is the
referenced import's durable `imported_at`, not a new retry-time clock value.

Supported initial producer contracts are:

- `analysis_turns`, version `1`, producer status `verified`, review status
  `not_applicable`; and
- `cunit_segmentation`, version `1`, producer status `verified`, review status
  `not_domain_validated`.

The review status preserves the current methodological limit: deterministic
segmentation remains researcher-reviewable evidence and has not been validated
against expert agreement.

### `evidence_passages`

| Column | Contract |
|---|---|
| `evidence_set_id` | Parent immutable evidence set. |
| `passage_id` | Existing deterministic passage ID. |
| `passage_ordinal` | Contiguous zero-based ordinal within this set. |
| `role` | Exact producer role/speaker string; may be empty but is never coerced. |
| `text_sha256` | Full SHA-256 of exact canonical passage UTF-8 bytes. |
| `text_length` | Python Unicode code-point length of that text. |

The primary key is `(evidence_set_id, passage_id)` and the set/ordinal pair is
unique. Registration recomputes
`passage_evidence_id(transcript_revision_id, passage_ordinal)` and requires an
exact match.

### `evidence_cunits`

| Column | Contract |
|---|---|
| `evidence_set_id` | Parent immutable evidence set. |
| `cunit_id` | Existing deterministic C-unit ID. |
| `passage_id` | Exact parent passage in the same set. |
| `cunit_ordinal` | Contiguous zero-based ordinal within that passage. |
| `text_sha256` | Full SHA-256 of exact canonical C-unit UTF-8 bytes. |
| `text_length` | Python Unicode code-point length of that text. |

The primary key is `(evidence_set_id, cunit_id)`, the
set/passage/ordinal tuple is unique, and a composite foreign key requires the
parent passage in the same set. Registration recomputes
`cunit_evidence_id(passage_id, cunit_ordinal)` and requires an exact match.

Migration 4 makes set completion append-proof, not merely update-proof. Passage
and C-unit foreign keys to the set header are `DEFERRABLE INITIALLY DEFERRED`.
Registration inserts all passages, then all C-units, and inserts the set header
last in the same transaction. Header insertion verifies the declared child
counts. Child `BEFORE INSERT` triggers reject insertion when that set header
already exists. Separate triggers reject every update/delete of a set, passage,
or C-unit. Once the header is visible, no target can be appended, rewritten, or
removed. Source lifecycle work must later coordinate retention rather than
cascade through accepted evidence.

## Evidence Set Identity And Registration

The version-1 canonical snapshot is this exact JSON shape:

```json
{
  "format": "nlp-skill-agents.evidence-target-set",
  "format_version": 1,
  "import_id": "imp_...",
  "workspace_id": "study-id",
  "project_source_id": "exact opaque id",
  "transcript_revision_id": "trv_...",
  "transcript_text_sha256": "64 lowercase hex",
  "producer_kind": "analysis_turns | cunit_segmentation",
  "producer_version": 1,
  "producer_status": "verified",
  "review_status": "not_applicable | not_domain_validated",
  "passage_count": 1,
  "cunit_count": 1,
  "passages": [
    {
      "passage_id": "psg_...",
      "passage_ordinal": 0,
      "role": "exact producer role",
      "text_sha256": "64 lowercase hex",
      "text_length": 12,
      "cunits": [
        {
          "cunit_id": "cun_...",
          "cunit_ordinal": 0,
          "text_sha256": "64 lowercase hex",
          "text_length": 12
        }
      ]
    }
  ]
}
```

Passage arrays sort by ordinal and C-unit arrays sort by ordinal. JSON keys are
serialized with `sort_keys=True`, `ensure_ascii=False`, `allow_nan=False`, and
separators `(",", ":")`, then encoded as UTF-8. Integers use Python JSON's
canonical base-10 integer representation; booleans are never accepted as
integers. Strings retain their exact Unicode code points with no implicit
normalization. Filenames, labels, run paths, timestamps, and raw text are absent.

`snapshot_sha256` is SHA-256 of those exact bytes and `evidence_set_id` is
`"evs_" + snapshot_sha256[:32]`. The complete digest remains stored. An exact
replay produces the same ID. Any import, ownership, producer eligibility/review
state, parser interpretation, target text, C-unit split, or producer version
change produces another set instead of rewriting the old one.

`EvidenceTargetRegistry.register_complete_set(...)` runs under
`workspace_mutation_lock(root)` and:

1. validates exact bounded field types, IDs, enums, timestamps, counts, and
   contiguous ordinals;
2. verifies the referenced import, project source, workspace, and revision;
3. recomputes the transcript revision and transcript SHA-256 from the supplied
   canonical transcript text;
4. recomputes every passage/C-unit ID, target SHA-256, text length, snapshot
   SHA-256, and evidence-set ID;
5. stores all referenced UTF-8 blobs content-addressably and reads them back with
   hash/UTF-8 validation;
6. inserts passages, C-units, and finally the complete set header in one
   evidence-database transaction; and
7. on retry, strictly rereads every stored row and blob and returns only if the
   complete stored set is exactly identical.

Blobs may be written before the database transaction; an interrupted write can
leave an unreferenced content-addressed blob, never a visible partial evidence
set. New analysis, study-batch, and segmentation payloads persist and expose the
exact `evidence_set_id`; their operation journals advance the existing
`evidence_cataloged` stage only after both the import and complete target set are
durable. The stage alone is not reinterpreted as target proof. Legacy completed
payloads without `evidence_set_id` remain readable but uncodable. No journal
schema is changed for this discriminator; any later journal-column change must
use that store's next contiguous migration. Replays are idempotent and conflicts
remain visible.

`EvidenceTargetRegistry.resolve(...)` requires the full project source, revision,
set, passage, and optional C-unit tuple. It strictly validates stored SQLite
types and values, recomputes all relationships, verifies the complete set counts
and snapshot, reads the selected text blob by hash, and returns exactly one
immutable resolved target. Zero matches is not found; disagreement, ambiguity,
partial state, malformed storage, or a blob failure is a conflict.

## Producer Changes

### Analysis and study batches

Every current analysis run registers an `analysis_turns` set from the exact
`AnalysisRun.source_content` and parsed `Turn` records. Passage ordinals must be
contiguous `turn_index` values and passage text is the exact stored `Turn.text`.
The run and study-batch response/payload expose the resulting
`evidence_set_id` so later clients can address the exact interpretation.

Standalone `results.json` need not duplicate all turn text because the registry
and text blobs become the durable target source. Study-batch snapshots continue
to retain their existing turns and are cross-checked against the registered set.

### Segmentation and exact C-unit text

Current `CUnitBoundaryDecision` gains an additive `cunit_texts` list aligned by
ordinal with `cunit_ids`. `CUnitAdjudication` gains a persisted
`cunit_text_contract_version`. New and explicitly reverified output writes `1`;
an absent legacy field loads as `0`. Compatibility loading never promotes 0 to
1. Only the explicit verify operation reruns the current producer and can emit
version 1.

- A one-C-unit decision persists its exact `cleaned_text` as the sole canonical
  unit text.
- A coordination split uses the first case-insensitive match of
  `\b(?:and|but|so)\s+(?:i|we|he|she|they|it)\s+\w+` in `cleaned_text`. Unit zero
  is `cleaned_text[:match.start()].rstrip()` and unit one is
  `cleaned_text[match.start():].lstrip()`. The conjunction stays with unit one;
  all other punctuation and exact code points are preserved. Both outputs must
  be non-empty or the turn becomes a zero-count human-review decision.
- A zero-count decision persists no C-unit IDs or texts.
- Any count/ID/text mismatch fails persistence; it is never truncated or padded.

One pure version-1 C-unit canonicalizer owns cleaning, classification, count, and
exact unit-string production from the ordered passage role/text. The live
adjudicator, registry insertion, archive replay, and strict resolver all rerun or
compare against that same canonicalizer; merely hashing caller-supplied unit text
is insufficient. Zero-count dependent attachments are not materialized as
C-units and cannot be C-unit-coded, while their source passage remains codable.

The segmentation set uses each event's exact text as passage text and the new
per-unit strings as C-unit text. It can be registered only when the run's
pipeline status is exactly `verified`, its text contract version is 1, its
source/revision lineage is catalog-proven, and the canonicalizer reproduces all
decisions. A `failed` or `needs_rewrite` run exposes an empty unavailable
`evidence_set_id`; pipeline verification and scientific review status remain
separate. Fallback IDs and legacy version-0 decisions are not eligible.
Reverification through the current producer can write a new complete set;
compatibility loading alone cannot.

The generic local segmentation API retains its existing `local-default`
behavior. Phase 1 also adds project-owned JSON endpoints under
`/api/studies/{study_id}/segmentation/runs` for create, list, read, specialist
patch, and explicit reverify. They require an existing study, pass the path
`study_id` as the immutable storage `workspace_id`, reject a loaded run owned by
another workspace before read or mutation, preserve source lineage validation,
and expose `evidence_set_id` in create/get/list/patch/verify payloads. An
additive study-scoped file-upload route is not required for this backend
identity slice. Phase 2 may add the UI without changing the contract.

## Qualitative Migration 2

The qualitative database's next contiguous migration is migration 2. Migration
1 is never modified. Migration 2 adds `coding_references`:

| Column | Contract |
|---|---|
| `coding_reference_id` | Primary key generated with qualitative `cdr_` prefix. |
| `project_id` | Owning qualitative project. |
| `project_source_id` | Exact opaque source ID; never normalized. |
| `transcript_revision_id` | Exact immutable revision. |
| `evidence_set_id` | Exact immutable producer interpretation. |
| `target_kind` | Exactly `passage` or `cunit`. |
| `passage_id` | Required exact passage. |
| `cunit_id` | Non-null empty string for passage; required ID for C-unit. |
| `start_offset` | Inclusive Unicode code-point offset. |
| `end_offset` | Exclusive Unicode code-point offset, greater than start. |
| `codebook_version_id` | Exact frozen version. |
| `code_id` | Exact version-local code. |
| `created_by` | Original active project researcher/coder. |
| `created_at` | Timezone-aware creation timestamp. |
| `removed_by` | Null while active; remover researcher after uncoding. |
| `removed_at` | Null while active; timezone-aware removal time afterward. |

Checks enforce target-kind/C-unit shape, positive span width, and the paired
nullability of removal fields. Composite foreign keys bind the code
to its exact version and bind creator/remover IDs to the project. A partial
unique index on the complete target/span/code/version/creator tuple where
`removed_at is null` allows independent coders while preventing duplicate active
application by one coder.

Triggers:

- reject insert unless the referenced codebook version is frozen;
- reject every insert whose removal fields are not both null;
- reject physical deletion;
- use one null-safe update trigger that requires every original identity,
  evidence, span, code, creator, and creation column to compare with `IS`, and
  permits only the transition from `(removed_by, removed_at) = (null, null)` to
  two non-null values;
- reject no-op updates, resurrection, second removal, or replacement of removal
  attribution; and
- retain the baseline frozen-code and append-only-audit triggers unchanged.

## Public Coding Service

`CodingReferenceService(root, project_id)` exposes:

| Operation | Behavior |
|---|---|
| `create_reference(...)` | Resolve exact evidence, validate a non-empty span, require an active coder and frozen exact code, insert row plus audit atomically, and return the strict stored record. |
| `read_reference(coding_reference_id)` | Strictly return one active or removed record. |
| `list_references(include_removed=False, ...)` | Deterministically list project records, with optional exact source/code/coder filters. |
| `remove_reference(researcher_id, coding_reference_id)` | Tombstone one active reference plus audit atomically; exact same-remover retry is a no-op. |
| `validate_project_state()` | Strictly validate all rows, audit pairs, frozen code targets, and externally resolved evidence for archive preflight. |

Creation input is:

```text
researcher_id
project_source_id
transcript_revision_id
evidence_set_id
target_kind
passage_id
cunit_id                 empty for passage
start_offset
end_offset
codebook_version_id
code_id
```

The returned immutable record contains only the stored fields. Evidence or code
labels and excerpt text are not silently joined into it. A later retrieval API
can deliberately resolve display content from these exact IDs.

Deterministic list order is `created_at`, then `coding_reference_id`. Filters are
exact stable IDs, never labels or substring guesses.

The service defines `CodingReferenceNotFoundError`,
`CodingReferenceValidationError`, and `CodingReferenceConflictError`. Stored
corruption is always a conflict, not a validation error and never a partially
returned record.

## Validation And Transaction Boundary

Before opening a qualitative transaction, create does the following under the
root workspace lock:

1. validates non-trimmed bounded external IDs, exact enums, and strict integer
   offsets (booleans are not integers);
2. resolves the complete target tuple through `EvidenceTargetRegistry`;
3. requires exact `workspace_id == project_id` and exact source/revision/set
   agreement;
4. reads and verifies the canonical target text blob;
5. validates `0 <= start_offset < end_offset <= len(text)` and materializes that
   exact non-empty selection only for validation, without persisting its text or
   hash.

It then releases the workspace lock completely. In one
`QualitativeProjectDatabase.transaction()` it:

1. revalidates the supplied project researcher and active state;
2. strictly resolves the exact version-local code and requires the version to be
   frozen;
3. returns an exact active same-coder retry only after all validation;
4. inserts one new coding row;
5. appends exactly one attributable audit event; and
6. strictly rereads the stored row before commit.

Evidence sets are immutable, so the validated target cannot be retargeted
between the external read and qualitative write. Future evidence/source deletion
must coordinate with coding rows. Removal deliberately does not require external
evidence to remain available, so attributable cleanup is possible after damage
or later lifecycle restriction.

Strict reads first materialize all local rows/audits under the study guard and
then release it before resolving all distinct evidence tuples under one workspace
lock. They reject wrong SQLite storage classes, padded/blank IDs, invalid ID
shapes, impossible or timezone-naive timestamps, malformed target-kind shape,
invalid offsets, project disagreement, draft/missing/wrong-version codes,
invalid researcher history, malformed audit JSON, noncanonical audit metadata,
impossible removal state, or unavailable/conflicting external evidence.

## Attributable Audit Contract

The coding row and audit event share the qualitative transaction.

| Mutation | Event | Actor | Subject | Exact metadata keys |
|---|---|---|---|---|
| Apply | `coding_reference.created` | `created_by` | `coding_reference` / reference ID | `target_kind`, `evidence_set_id`, `codebook_version_id`, `code_id` |
| Remove | `coding_reference.removed` | `removed_by` | `coding_reference` / reference ID | empty object |

Metadata JSON uses sorted keys and compact separators. It never contains source
or excerpt text, hashes, filenames, code labels/definitions, researcher names,
case attributes, request bodies, filesystem paths, or raw SQLite errors.

Strict project validation requires exactly one canonical creation event for
every row with actor/time matching `created_by`/`created_at`. An active row has no
removal event. A removed row has exactly one canonical removal event with
actor/time matching `removed_by`/`removed_at`. Extra, missing, duplicated,
malformed, or content-bearing coding-reference events are conflicts.

If audit insertion fails, the domain insert or tombstone rolls back. Idempotent
retries append no event.

## Lock Order

The repository's archive order remains:

```text
study batch archive guard -> workspace mutation lock
```

Runtime target validation follows the existing case/source-link shape:

```text
workspace mutation lock -> evidence reads -> release
study mutation guard -> qualitative BEGIN IMMEDIATE -> mutation and audit
```

Runtime code must never enter the study guard while still holding the workspace
lock. Restore/project validation first collects and validates local qualitative
rows under the study guard, releases it, and only then resolves the distinct
external evidence tuples under the workspace lock.

Archive creation already holds the archive guard and workspace lock. It must not
call a public service operation that reacquires the non-reentrant study guard.
Instead it builds an isolated snapshot root from the exact captured member bytes
and runs the ordinary staged validators against that independent root before
writing the archive.

## Archive And Restore Contract

New archives emit manifest format version 2. The reader accepts versions 1 and 2
explicitly; all other versions conflict. Version 2 requires:

- `evidence/targets.json`: strict nested records for every complete evidence set
  reachable from the project's exported import IDs; and
- `evidence_text_blobs/<sha256>.utf8`: exactly the verified canonical text-blob
  closure referenced by those target records.

The target document exists even when its set list is empty and contains only
typed registry fields, never inline text. Its expected distinct digest set is
the union of every set's `transcript_text_sha256`, every passage `text_sha256`,
and every C-unit `text_sha256`. Expected member names derive exactly from that
union. Extra, missing, duplicate, case-colliding, invalid UTF-8, wrong-hash,
oversized, or unreferenced members fail.

Version 1 remains legacy-readable only when neither its staged study artifacts
nor its qualitative rows reference an `evidence_set_id`; it cannot contain
version-2 target/text members. This check is not based only on coding rows.
Emitting version 2 prevents an older version-1 reader from accepting an
analysis-only project and silently ignoring new evidence members.

Creation, while holding the existing snapshot guards, must:

1. validate completed study batches and skill-pack versions;
2. collect the exact study directory, project imports, audit events, source blobs,
   complete evidence sets, and evidence text blobs;
3. build an isolated validation root from those captured bytes;
4. replay imports and targets into that root and validate all study and
   qualitative state there; and
5. write only the already validated member set and manifest.

Restore must:

1. verify the ZIP/member budget, paths, manifest hashes, and typed JSON before
   creating destination state;
2. build an isolated staging root;
3. store and verify source and the complete transcript/passage/C-unit text-blob
   closure in the isolated staging root;
4. replay evidence imports in lineage order, then complete target sets against
   only that staged blob store;
5. validate study batch artifacts and all qualitative state only against the
   staged root;
6. preflight the destination by copying its evidence database/audit state,
   validating any existing text blobs, staging every missing archive text blob
   into the isolated preflight root, and replaying the same imports and targets
   there;
7. reject same-ID/different-record or blob conflicts before publication; and
8. commit evidence DB, audit, newly created source/text blobs, and study directory
   with exact rollback to the pre-restore snapshot on any late failure.

Every newly created destination text blob is tracked with newly created source
blobs and removed on late-failure rollback. Newer evidence or qualitative
schemas are conflicts. Malformed supported-schema content is a content-safe
archive error. Destination evidence is never used to fill missing staged archive
evidence.

The archive remains local, unsigned, and unencrypted. This slice does not claim
malicious-writer tamper evidence or authorize sensitive/real research data.

## HTTP Contract

Base path: `/api/studies/{study_id}/qualitative/coding-references`

Request objects:

```json
{
  "CodingReferenceCreateRequest": {
    "researcher_id": "required string",
    "project_source_id": "required exact string",
    "transcript_revision_id": "required string",
    "evidence_set_id": "required string",
    "target_kind": "passage | cunit",
    "passage_id": "required string",
    "cunit_id": "optional string, default empty",
    "start_offset": "required strict non-negative integer",
    "end_offset": "required strict positive integer",
    "codebook_version_id": "required string",
    "code_id": "required string"
  },
  "CodingReferenceRemoveRequest": {
    "researcher_id": "required string"
  }
}
```

`project_source_id` stays in JSON because it is an opaque external ID and may
contain path-significant characters. Endpoints:

| Method and path | Success response |
|---|---|
| `POST /coding-references` | `200 {"coding_reference": CodingReferenceRecord}` |
| `GET /coding-references` | `200 {"coding_references": [...]}`; optional exact query filters and `include_removed=false` |
| `GET /coding-references/{coding_reference_id}` | `200 {"coding_reference": CodingReferenceRecord}` |
| `DELETE /coding-references/{coding_reference_id}` | `200 {"coding_reference": CodingReferenceRecord}` |

Error mapping:

- `422`: malformed JSON, missing fields, wrong structural types, Boolean offsets,
  or invalid query parameter containers rejected by FastAPI/Pydantic;
- `400`: blank/padded/internal ID input, unknown target kind, target-kind/C-unit
  mismatch, or invalid selection offsets;
- `404`: missing study, researcher, coding reference, source, revision, evidence
  set/target, codebook version, or code; and
- `409`: inactive actor, wrong project/source/revision ownership, incomplete or
  ambiguous evidence, text/hash corruption, draft/wrong-version code, duplicate
  identity conflict, different-actor retry, newer/tampered schema, or storage and
  journal conflict.

HTTP details are content-safe and never expose transcript/code content, hashes,
paths, SQL, or raw driver errors.

The study-scoped segmentation prerequisite uses
`/api/studies/{study_id}/segmentation/runs` with `POST` create, `GET` list,
`GET /{run_id}`, `POST /{run_id}/verify`, and
`POST /{run_id}/specialists/{specialist_id}/patches`. Request bodies reuse the
existing segmentation models; `workspace_id` comes only from the path and is not
client-overridable. These responses retain the existing run envelope plus the
additive `evidence_set_id` and `cunit_text_contract_version` fields.

## Required Adversarial Proof

Focused tests must prove at least:

1. Evidence migrations 1-3 and qualitative migration 1 remain unchanged; new
   migrations are exactly evidence 4 and qualitative 2 and roll back partial
   failures.
2. Symlink/non-file evidence DBs, dropped/new schema objects, newer versions,
   failed integrity/FK checks, and missing append/immutability/frozen-target
   triggers are refused on the same prepared connection.
3. Analysis and segmentation writers register exact complete sets idempotently;
   interruption before journal advance is recoverable, while a legacy
   `evidence_cataloged` stage without an evidence-set ID remains uncodable.
4. Same evidence-set ID with changed source/revision/producer/target/hash/length
   conflicts; composite import/source/revision/digest foreign keys reject crossed
   ownership; fabricated hash-shaped passage/C-unit IDs fail.
5. Independent producer interpretations with the same passage ID remain distinct
   and require the requested evidence-set ID.
6. The exact version-1 coordination split persists two reproducible C-unit
   strings; status other than `verified`, text contract 0, and missing or
   mismatched legacy per-unit text prevent accepted C-unit coding.
7. Text blobs reject symlinks, non-files, invalid UTF-8, wrong hashes, and missing
   content without leaking the text in errors.
8. Cross-project source, revision from another source, wrong set/passage/C-unit
   pairing, fallback-only identity, and incomplete set all fail.
9. Empty, reversed, negative, Boolean, and out-of-range offsets fail; valid
   Unicode code-point spans round-trip without persisting or exposing excerpt
   text or a short-text hash.
10. Direct SQL insertion against a draft version is rejected by the trigger;
    pre-tombstoned insert and physical delete are rejected; service creation
    rejects missing/draft/wrong-version codes.
11. Missing, inactive, and foreign researchers fail; creator/remover and audit
    actor/timestamps match exactly.
12. Exact same-coder create retry and same-remover delete retry are idempotent; a
    second coder is distinct; different-remover retry conflicts; reapply after
    removal creates a new ID.
13. Audit insertion failure rolls back create/remove. Malformed, duplicate,
    extra-key, noncanonical, or content-bearing audit metadata fails strict read
    and archive validation.
14. Padded IDs, wrong SQLite storage classes, naive/impossible timestamps,
    impossible tombstones, later child append, and direct identity mutation fail
    visibly.
15. API `422/400/404/409` boundaries are exact, and privacy sentinel transcript,
    excerpt, label, filename, SQL, and path values never appear in errors/audit.
16. A real passage and exact multi-unit C-unit coding round-trip through a
    version-2 archive with identical IDs, spans, target hashes, frozen
    code/version, attribution, tombstone state, and audit events.
17. Version 1 compatibility rejects any artifact/row evidence-set reference;
    removing each version-2 target JSON/text member, forging
    target/code/audit ownership, or recomputing ZIP manifest hashes around
    semantic corruption still fails before destination publication.
18. Exact destination records/blobs replay idempotently; same-ID/different-state
    conflicts without partial writes; injected late publication failure restores
    evidence DB, audit, both blob stores, and study directory exactly.
19. Archive capture concurrent with coding contains either both domain/audit rows
    or neither, and lock instrumentation proves no workspace-to-study inversion.
20. All pre-existing evidence, analysis, segmentation, study, qualitative,
    archive, API, and frontend gates remain green.

## Independent Review Record

Three read-only reviews completed before any schema or runtime edit: a holistic
identity/API review, a SQLite/locking review, and an archive/security/scope
review. The contract was revised to resolve every blocking finding:

- evidence sets now use header-last deferred registration and prohibit later
  child append as well as update/delete;
- the evidence database now has an exact prepared-connection schema/integrity
  boundary and structurally bound import/source/revision/digest keys;
- canonical manifest bytes, hashing, evidence-set ID derivation, producer
  eligibility, C-unit text contract version, and coordination splitting are
  exact rather than implementation-defined;
- study-owned C-unit evidence now has a Phase 1 public create/read/reverify path;
- coding rows cannot be inserted pre-removed, updated outside the one removal
  transition, or physically deleted;
- the redundant/dictionary-guessable excerpt hash was removed;
- archive format 2 prevents older readers from silently dropping target data,
  while strict version-1 compatibility remains; and
- transcript, passage, and C-unit blob closure plus destination staging/rollback
  are explicit.

The reviewers agreed that the central target registry is a necessary Phase 1
portability prerequisite, not Phase 2 work. No UI or retrieval presentation was
added to this contract.

## Explicit Non-Goals And Rollback

This slice does not implement Phase 2 transcript selection UI, coding stripes,
keyboard undo, source retrieval presentation, codebook editing UI, memo or
annotation behavior, case assignment, bulk coding, search, matrices, reliability,
reviewer adjudication, agent proposals, provider calls, roles/authorization,
source deletion, retention policy, cryptographic audit, encryption, or cloud
egress.

Rollback is forward-data preserving. Reverting application code does not delete
evidence migration-4 or qualitative migration-2 data. Older binaries correctly
refuse the newer databases. Preserve a verified project archive and roll forward
with compatible code; never edit migration 1, drop target/coding tables, erase
text blobs, or silently downgrade `user_version`.
