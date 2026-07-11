# NLP Skill Agents

Local-first transcript and C-unit analysis workbench for psychology and other
research studies.

This repository is currently a research prototype, not a production qualitative
analysis platform. The active product direction, scope boundary, gap register, and
phase gates live in
[`goals/psychology-research-platform-roadmap.md`](goals/psychology-research-platform-roadmap.md).
The product supports research workflows; it is not clinical decision support and
must not be used to diagnose or recommend treatment.

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
backend/segmentation/  C-unit parsing, adjudication, patching, evaluation, and runs
backend/extensions/    Agent-job and plugin-request artifacts
backend/storage/       Local JSON, CSV, SQLite, study, audit, and library stores
frontend/              React and Vite research workbench
study_skill_packs/     Product-facing study and metric definitions
demo_assets/           Tracked synthetic demonstration data
tests/                 Backend unit and API regression tests
checkpoints/           Historical feature and verification records
goals/                 Current roadmap plus historical planning documents
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
