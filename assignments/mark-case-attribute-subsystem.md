# Mark: Cases And Typed Attributes Subsystem

## Outcome

Build the first working case and attribute service on top of the accepted
qualitative database contract. A researcher must be able to create participant or
study-context cases, define typed attributes, assign validated values, and link an
existing project source to a case without copying or weakening evidence identity.

This is a backend and API assignment. It does not require an API key, model access,
real transcripts, or frontend work.

## Start Here

Read these files before changing code:

1. `goals/psychology-research-platform-roadmap.md`
2. `docs/architecture/qualitative-core-contract.md`
3. `backend/qualitative/database.py`
4. `backend/storage/evidence_catalog.py`
5. `backend/storage/study_store.py`
6. `frontend/src/casebookDesign.ts`
7. `frontend/src/casebookCsv.ts`
8. `tests/test_qualitative_database.py`
9. `tests/test_study_workspaces.py`
10. `tests/test_api.py`

Start from the merged architecture checkpoint:

```bash
git fetch origin
git switch -c codex/phase1-case-attribute-subsystem origin/master
```

Before coding, confirm that
`backend/qualitative/database.py` contains migration
`create-qualitative-core-contract`. If it does not, stop because the branch base is
wrong.

## Design Note First

Add `docs/architecture/case-attribute-service-design.md` before implementation.
It must define:

- public store operations and returned records;
- value normalization for every supported attribute type;
- categorical allowed-value rules;
- source ownership validation against `EvidenceCatalog`;
- compatibility with the existing JSON `StudySchema` and casebook CSV helpers;
- atomic mutation and audit behavior;
- API endpoints and error mapping;
- what remains out of scope.

Open the design note for review before changing schema or replacing any existing
study behavior.

## Required Implementation

Create `backend/qualitative/cases.py` and focused tests in
`tests/test_qualitative_cases.py`.

Implement operations for:

1. Creating, updating, reading, and listing cases.
2. Supporting the baseline kinds `participant`, `session`, `dyad`, `condition`,
   and `timepoint` without adding method-specific assumptions.
3. Creating and listing attribute definitions.
4. Setting, replacing, reading, and clearing a case attribute value.
5. Linking and unlinking an existing project source and case.
6. Returning cases with their typed attribute values in deterministic order.

Every mutation must require `researcher_id`, verify that researcher belongs to the
project and is active, and append an audit event inside the same qualitative
transaction.

Use the existing tables. If a schema change is genuinely required, explain why in
the design note, add the next contiguous migration after rebasing, and do not
modify migration 1.

## Typed Value Rules

- `text`: accept a string and preserve meaningful internal whitespace.
- `number`: accept finite JSON numbers; reject strings, NaN, and infinity.
- `boolean`: accept only JSON `true` or `false`.
- `date`: accept one calendar date formatted `YYYY-MM-DD` and reject impossible
  dates.
- `categorical`: accept one exact value from the definition's non-empty,
  duplicate-free allowed-value list.
- Attribute keys and case labels cannot be blank after trimming.
- Attribute keys are stable identifiers and unique within the project.
- Changing an attribute definition must not silently invalidate stored values.
  Document and test the chosen safe behavior before implementing updates.
- Clearing a value removes the value row and appends an attributable audit event.

## Evidence-Link Rule

Before linking `project_source_id`:

1. Load it through `EvidenceCatalog.source_history`.
2. Confirm `source["workspace_id"]` equals the current project ID.
3. Only then insert the link and audit event in one qualitative transaction.

Reject missing sources and sources owned by another workspace. Do not copy source
text, hashes, revisions, or filenames into `source_case_links`.

## Existing Casebook Compatibility

The current `StudySchema` and browser CSV helpers are prototype behavior. Do not
delete or silently replace them in this slice.

The design note must choose and test one explicit compatibility path:

- read-only projection from the new case service into the existing shape; or
- a later, separately reviewed migration plan while both systems coexist.

Whichever path is chosen, existing study-batch behavior and its tests must remain
unchanged.

## API Slice

Add narrowly scoped FastAPI endpoints for:

- create/list/read/update cases;
- create/list attribute definitions;
- set/clear case attribute values;
- link/unlink project sources;
- read one case with typed attributes and source IDs.

Follow existing API conventions. Return `404` for missing records, `400` for type
or validation failures, `409` for ownership/conflicting state, and never expose raw
SQLite errors.

Do not add default researcher identities. The request must supply the actor until
the later Windows identity layer exists.

## Proof Required

Tests must cover at least:

- every supported case kind;
- create/read/update/list behavior;
- each valid attribute type;
- wrong JSON types, NaN/infinity, impossible dates, and invalid categories;
- duplicate attribute keys and duplicate category choices;
- setting, replacing, and clearing values;
- missing and inactive researchers;
- transaction rollback when audit insertion fails;
- source link success, exact retry, unlink, missing source, and wrong workspace;
- preservation of existing `StudySchema`, casebook CSV, and batch behavior;
- missing, validation, and conflict HTTP responses;
- newer unsupported database schema refusal.

Run:

```bash
.venv/bin/pytest -q tests/test_qualitative_cases.py
.venv/bin/pytest -q tests/test_study_workspaces.py tests/test_api.py
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

- codebooks or code hierarchies;
- manual transcript coding;
- case relationships beyond the accepted baseline kinds;
- memos, matrices, agreement, or adjudication;
- agent suggestions;
- a casebook editor UI;
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
