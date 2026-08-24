# NLP Skill Agents

Local-first transcript and C-unit analysis workbench for psychology and other
research studies.

This repository is currently a research prototype, not a production qualitative
analysis platform. The active product direction, scope boundary, gap register, and
phase gates live in
[`goals/psychology-research-platform-roadmap.md`](goals/psychology-research-platform-roadmap.md).
The product supports research workflows; it is not clinical decision support and
must not be used to diagnose or recommend treatment.

For the isolated synthetic classroom proof—exactly four Luna calls, strict JSON,
a deterministic local merge, a cost receipt, and reversible human acceptance—see
[`docs/professor-demo.md`](docs/professor-demo.md). That lane does not mutate study
data and does not represent completion of the broader roadmap.

## What Works Today

The primary workflow is source-preserving C-unit segmentation:

- import or paste Descript-style transcript text;
- run deterministic C-unit segmentation and review configured rule checks;
- inspect specialist packets and proposed patch operations;
- apply patches, verify the result, and export evidence artifacts;
- run the tracked synthetic regression corpus.

Analysis and segmentation evidence carries content-addressed source and transcript
revision IDs backed by the SHA-256 of the exact transcript text supplied to the
pipeline. Parsed turns and segmentation events have stable passage IDs, and
counted C-unit candidates have stable C-unit IDs within that immutable revision.
Each ingestion also receives a distinct import ID and records the SHA-256 of the
actual uploaded blob, or the exact UTF-8 bytes for pasted text, in
`evidence.sqlite3`. Re-recording an import or revision may be idempotent but cannot
change its identity fields.
Project source records group those immutable revisions inside either a study or
the local default workspace. A revised import must name its existing project
source and parent transcript revision; invalid cross-source or cross-workspace
lineage is rejected before run artifacts are created.
Original source blobs are retained locally at content-addressed SHA-256 paths.
New writes verify their expected digest, existing blobs are reverified before
deduplication, and every read used by the verification API rehashes the bytes.
Persisted analysis runs and current-lineage, pipeline-verified segmentation runs
using the C-unit text contract also register immutable evidence target sets. Each
set binds its producer, source and transcript revision, canonical passage/C-unit
identities, exact UTF-8 text digest, and content-addressed target-set ID without
copying transcript text into qualitative coding rows. Coding therefore depends on
a persisted, revalidated evidence set rather than accepting a merely derivable
identifier as proof that evidence exists.

Study backups are portable ZIP archives. Current format-2 manifests cover every
study file, workspace-scoped evidence record and target set, referenced source and
evidence-text blob, and study-scoped audit event. Restore verifies declared paths,
sizes, hashes, canonical target identities, and qualitative references before
atomically exposing the staged study directory. Archive handling rejects encrypted
or unsupported ZIP members, non-portable Windows paths, path-prefix collisions,
and malformed typed records before extraction. Backup capture holds the per-study
mutation boundary, refuses a running batch, and validates both journal-backed and
pre-journal completed batches, including manifests, run snapshots, aggregate JSON,
CSV exports, audit events, skill packs, evidence rows, and source blobs. Restore
preflights shared evidence state under a workspace mutation lock and rolls back
catalog, audit, newly introduced source/evidence-text blobs, and filesystem state if
final study publication fails. Format-1 archives remain readable under their exact
historical contract and cannot smuggle format-2 evidence references.
Legacy validation follows the persisted generation instead of imposing the newest
contract retroactively. Current lineage-aware snapshots are bound to the exact
catalog record. Journal-backed snapshots always require their verified blob;
pre-journal lineage snapshots verify it when retained because an intermediate
writer generation predated blob storage. The first import-catalog generation
validates its deterministic transcript identity, and backup promotes its migrated
legacy catalog row into the study archive while explicitly recording the original
blob as unretained. Earlier hash-only snapshots validate their deterministic
source/revision IDs. Metadata-only and original pre-audit snapshots remain
readable as explicitly reduced-trust history rather than receiving invented
provenance or audit events.
The analysis-run, evidence-catalog, segmentation-operation, per-study batch, and
per-study qualitative databases use ordered, forward-only SQLite migration
ledgers. Each migration is transactional, older supported database shapes are
upgraded in place, and the application refuses a database created by a newer
unsupported schema instead of guessing. `GET /api/storage/schema-status` reports
the three root database contracts; study-scoped contracts have adjacent
`schema-status` endpoints.
Standalone analysis persistence uses a durable operation journal across source
blob retention, evidence cataloging, result/CSV writes, and final run indexing.
Failures retain the last completed stage and exception class without storing raw
transcript content or error messages. Exact retries replay integrity checks and
idempotent writes; the final run row and completed marker commit together.
`GET /api/storage/analysis-operations` exposes completed, failed, or interrupted
operations for local recovery inspection.

Segmentation create, patch, verify, and explicit rewrite persistence uses a
separate operation journal. Each attempt binds its run/import identity to the
previous and target canonical payload hashes, then records progress after source
blob retention, evidence cataloging, specialist-packet writes, and the run
snapshot. Overlapping live operations, stale snapshot writers, and unsupported
newer journal schemas fail visibly. `GET /api/storage/segmentation-operations`
exposes identifiers, hashes, stages, attempt counts, timestamps, and exception
class without transcript content, filenames, specialist packets, or exception
messages.

Study batch persistence has its own per-study operation journal. A caller can keep
and resubmit an explicit batch ID to retry the exact ordered inputs and skill-pack
artifact. Reserved run, import, and project-source identities survive caught
failures; completed rows bind the canonical aggregate hash, and replay verifies
existing blobs, evidence rows, run snapshots, aggregate JSON, CSV exports, the
batch manifest, and the stable completion audit event rather than duplicating
them. Supported older journal shapes are upgraded transactionally, including
repair of pre-hash completed rows from their persisted aggregate snapshot.
`GET /api/studies/{study_id}/batch-operations` exposes content-safe operation
status, and the adjacent `schema-status` endpoint reports the journal migration
contract. Pre-journal batch history remains readable through list, detail, and run
drilldown routes, while journal-known running or failed batches stay hidden from
completed history and return a conflict on direct reads. A malformed, directory,
or symbolic-link journal also returns a controlled conflict across status and
history endpoints instead of escaping as a storage error.

The segmentation conflict guards cover the current same-root,
shared-filesystem, single-host design only. Its root-global journal and list
endpoint are not study-scoped or access-controlled.

The segmentation journal provides recovery diagnostics, not automatic recovery.
There is no replay endpoint or startup reconciler, and the journal does not retain
the run payload. A hard stop leaves a `running` row that currently blocks exact
replay. The multi-store write sequence does not roll back earlier side effects.
Root-level segmentation runs, specialist artifacts, and `segmentation.sqlite3`
are also not included in per-study project archives.

Each study can now initialize a versioned `qualitative.sqlite3` contract inside
its study directory. The contract reserves one transactional boundary for named
researchers, versioned hierarchical codebooks, cases, typed attributes,
source-to-case links, coding references, and append-only qualitative audit events.
Frozen codebook versions are immutable at the database boundary.

The backend now exposes the first working codebook workflow: bootstrap the named
project researcher, create and list codebooks, edit deterministic draft
hierarchies, freeze a version, derive a later draft with preserved stable keys,
and import or export portable JSON without trusting database or actor IDs. The
shared database boundary rejects symlinks, schema/trigger drift, integrity or
foreign-key failures, and foreign project ownership before reads or writes.
`GET /api/studies/{study_id}/qualitative/schema-status` reports compatibility;
the codebook endpoints live under
`/api/studies/{study_id}/qualitative/codebooks`.

The same backend now exposes attributable case and typed-attribute workflows for
participant, session, dyad, condition, and timepoint records. Researchers can
define immutable typed attributes, set or clear validated scalar values, and link
an existing project-owned evidence source without copying evidence content or
catalog metadata beyond the stable source ID. Project restore validates this
qualitative state and every source link against the staged evidence catalog before
publication.

The coding-reference API can apply, read, list, and remove attributable codings
against exact passages or C-units and exact codes from frozen codebook versions.
Every accepted mutation and its privacy-minimized audit event commit atomically.
Reads strictly revalidate stored rows against the qualitative database, evidence
catalog, target registry, evidence-text blob, and canonical identifier functions.
The adjacent study-scoped segmentation routes create the persisted evidence sets
required for coding without exposing one study's runs through another study.

The memo and annotation APIs now attach attributable, revisioned notes to an
exact study, project source, case, frozen code, or evidence excerpt. Immutable
revision history uses compare-and-append retries, one-way removal tombstones, and
privacy-minimized atomic audit events. Strict reads revalidate local relations,
canonical evidence targets, bounded UTF-8 content, pagination cursors, and audit
pairing; format-2 backup and staged restore preserve every revision and reject
tampered or incomplete note state before publication.

The research-review API registers attributable local researchers, imports exact
agent coding suggestions from synthetic fixtures or external agent-output
artifacts, and appends human reviewer decisions without mutating accepted coding
references. Suggestions and decisions use strict origin identities, frozen-code
and evidence-span validation, conditional result-reference rules, bounded cursors,
and atomic privacy-minimized audits. This Phase 1 boundary makes no model or
provider calls and stores no prompt, rationale, confidence, or model response.

The saved-query API now persists attributable, immutable coding-reference filter
definitions with caller-stable identities, exact-retry semantics, bounded
project/filter-bound cursors, atomic privacy-minimized audits, and complete
archive/restore preflight. It stores definitions only: it does not execute a
search, materialize query results, or add a researcher-facing saved-query UI.
Those interactions remain part of the Phase 2 manual workbench, while matrices
and publication/reproducibility exports remain Phase 3 work.

These are backend/API Phase 1 services, not yet a researcher-facing codebook, case,
manual-coding, review, or note-authoring workbench. Source selection, coding
stripes, retrieval, undo, suggestion review, memo/annotation editing UI, search,
and identity/role administration remain later slices. The existing JSON
`StudySchema` and casebook CSV helpers continue unchanged until a separately
reviewed migration is defined.

Segmentation outputs are rule-checked candidates, not validated gold transcripts.
Rule and fixture counts show deterministic implementation coverage only; they are
not estimates of accuracy, inter-rater reliability, or psychology-domain validity.
C-unit boundary decisions remain explicitly uncalibrated and require researcher
review until representative authorized data and human-coded comparisons exist.

The older study-metrics workspace remains available underneath that workflow:

- import TXT or DOCX transcripts, or paste transcript batches;
- configure JSON/YAML study skill packs;
- run deterministic base, lexical, disfluency, and plugin metrics;
- manage study workspaces, casebook metadata, batch history, and exports;
- draft or refine skill packs locally, with optional OpenRouter authoring.

Agent jobs in the current codebase are prompts, evidence-gated status transitions,
evidence records, and implementation packets. They are not yet a durable
autonomous worker system and cannot be treated as production agent execution.

## Data And Network Boundary

Generated runs, uploads, SQLite metadata, and exports default to `local_data/` or
the path set by `NLP_SKILL_AGENTS_DATA_DIR`. These paths are intentionally ignored
by Git. Backend JSON, CSV, TXT, Markdown, HTML, and audit artifacts are written to
a same-directory temporary file, synced, and atomically replaced so an interrupted
single-file write does not truncate the last complete artifact.

Deterministic analysis and segmentation run locally. OpenRouter is optional and is
called only when a user explicitly selects OpenRouter for skill-pack authoring or
refinement and a key is configured. That action sends the entered authoring
content to an external provider. There is no automatic OpenRouter fallback.

Use the `secure-offline` deployment-profile check when network LLM access must be
disabled. A configured `OPENROUTER_API_KEY` causes that profile check to fail.

## Project Layout

```text
backend/app/           FastAPI entry point and HTTP API
backend/analysis/      Transcript parsing, deterministic metrics, and skill packs
backend/evidence/      Shared source, revision, passage, and C-unit identifiers
backend/qualitative/   Per-study qualitative database and domain services
backend/segmentation/  C-unit parsing, adjudication, patching, evaluation, and runs
backend/extensions/    Agent-job and plugin-request artifacts
backend/storage/       Local JSON, CSV, SQLite, study, audit, and library stores
frontend/              React and Vite research workbench
study_skill_packs/     Product-facing study and metric definitions
demo_assets/           Tracked synthetic demonstration data
tests/                 Backend unit and API regression tests
checkpoints/           Historical feature and verification records
goals/                 Current roadmap plus historical planning documents
assignments/           Reviewed implementation packets for project contributors
local_data/            Ignored local runs, uploads, databases, and exports
```

## Development

Python 3.11 or newer and Node.js are required.

Backend:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

The frontend defaults to `http://127.0.0.1:8000` for the API. Set
`VITE_API_BASE` to use a different backend URL.

## Verification

Run the established backend, build, and frontend helper gates before merging a
feature:

```bash
.venv/bin/pytest -q
cd frontend
npm run build
npm run test:batch
npm run test:casebook
npm run test:matrix
npm run test:casebook-csv
npm run test:agent-jobs
npm run test:privacy
npm run test:provenance
npm run test:validation
```

Every feature follows the branch, commit, test, pull-request, merge, and cleanup
rules in the active roadmap. Checkpoints record feature-specific proof and known
limitations.
