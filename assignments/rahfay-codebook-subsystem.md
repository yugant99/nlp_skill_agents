# Rahfay: Versioned Codebook Subsystem

## Outcome

Build the first working codebook service on top of the accepted qualitative
database contract. A researcher must be able to create a codebook, edit a draft
hierarchy, freeze a version, derive the next draft, and export or import a portable
JSON representation without changing frozen research records.

This is a backend and API assignment. It does not require an API key, model access,
real transcripts, or frontend work.

## Start Here

Read these files before changing code:

1. `goals/psychology-research-platform-roadmap.md`
2. `docs/architecture/qualitative-core-contract.md`
3. `backend/qualitative/database.py`
4. `backend/storage/sqlite_migrations.py`
5. `backend/app/main.py`
6. `tests/test_qualitative_database.py`
7. `tests/test_api.py`

Start from the merged architecture checkpoint:

```bash
git fetch origin
git switch -c codex/phase1-codebook-subsystem origin/master
```

Before coding, confirm that
`backend/qualitative/database.py` contains migration
`create-qualitative-core-contract`. If it does not, stop because the branch base is
wrong.

## Design Note First

Add `docs/architecture/codebook-service-design.md` before implementation. Keep it
short and concrete. It must define:

- public store operations and returned records;
- how a later draft preserves `stable_code_key` values;
- hierarchy cycle detection;
- JSON import and export shape;
- atomic mutation and audit behavior;
- API endpoints and error mapping;
- what remains out of scope.

Open the design note for review before making schema changes. Do not create a new
database or replace the accepted baseline schema.

## Required Implementation

Create `backend/qualitative/codebooks.py` and focused tests in
`tests/test_qualitative_codebooks.py`.

Implement operations for:

1. Creating and listing project codebooks.
2. Creating the first draft version.
3. Creating a later draft from an existing frozen version.
4. Adding and updating codes in a draft.
5. Moving a code within the hierarchy without creating a cycle.
6. Freezing a valid draft version.
7. Reading a complete version in deterministic hierarchy order.
8. Exporting one version as portable JSON.
9. Importing portable JSON into a new draft without trusting supplied database
   IDs or actor fields.

Every mutation must require `researcher_id`, verify that researcher belongs to the
project and is active, and append an audit event inside the same qualitative
transaction.

Use the existing tables. If a schema change is genuinely required, explain why in
the design note, add the next contiguous migration, and do not modify migration 1.

## Required Codebook Behavior

- Codebook titles and code labels cannot be blank after trimming.
- `stable_code_key` is unique within a version.
- Parent codes must belong to the same project and codebook version.
- A code cannot parent itself or create a longer hierarchy cycle.
- A frozen version cannot be changed through any service operation.
- Freezing an empty codebook version fails visibly.
- Deriving a draft copies the frozen hierarchy into new version-local `code_id`
  values while preserving each `stable_code_key`.
- JSON import validates the entire document before writing anything.
- An invalid import creates no partial codebook, version, codes, or audit events.
- JSON export contains research content and provenance needed for portability but
  excludes machine-specific paths and internal migration records.
- Lists and exports use deterministic ordering.

## API Slice

Add narrowly scoped FastAPI endpoints for:

- create and list codebooks;
- read one codebook version;
- create or derive a draft version;
- add or update a draft code;
- freeze a version;
- import and export version JSON.

Follow existing API conventions. Return `404` for missing records, `400` for
invalid input or hierarchy, `409` for immutable/conflicting state, and never expose
raw SQLite errors.

Do not add default researcher identities. The request must supply the actor until
the later Windows identity layer exists.

## Proof Required

Tests must cover at least:

- create/list/read happy paths;
- nested hierarchy ordering;
- duplicate stable keys;
- missing or cross-version parents;
- direct and indirect hierarchy cycles;
- blank required fields;
- exact retry behavior where supported;
- full rollback when audit insertion or import validation fails;
- freeze of an empty version;
- mutation attempts after freeze;
- draft derivation with preserved stable keys and new version-local IDs;
- JSON round trip and malformed JSON rejection;
- missing, validation, and conflict HTTP responses;
- newer unsupported database schema refusal.

Run:

```bash
.venv/bin/pytest -q tests/test_qualitative_codebooks.py
.venv/bin/pytest -q tests/test_api.py
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

## Boundaries

Do not implement:

- cases or typed attributes;
- manual transcript coding;
- memos, matrices, agreement, or adjudication;
- agent suggestions;
- a codebook editor UI;
- cloud calls or model integration;
- deletion or retention policy;
- unrelated refactors of `backend/app/main.py`.

If the architecture contract and the requested behavior conflict, stop and record
the conflict instead of inventing a compromise.

## Delivery

- Keep commits small and coherent.
- Push each commit before beginning the next logical slice.
- Add a checkpoint recording files, tests, limitations, and rollback.
- Open a pull request into `master`.
- Rebase on the latest `master` before final verification.
- Do not merge with failing or missing gates.
- After merge verification, delete the feature branch.
