# 2026-07-10 Phase 0 Repository Truth

## Goal

Make the repository entry points agree with the active psychology-platform
roadmap and the code that is actually on the default branch.

## Files Changed

- `README.md`
- `checkpoints/README.md`
- `checkpoints/2026-07-10-phase0-repository-truth.md`
- `goals/psychology-research-platform-roadmap.md`

## Implemented

- Replaced the stale V1-only README description with the current C-unit-first
  prototype, retained study-metrics workflow, and explicit research-only boundary.
- Documented the real local-data and optional OpenRouter network boundary.
- Documented current entry points, directories, startup commands, and regression
  gates.
- Linked the active roadmap from the checkpoint catalog, classified older plans
  and checkpoints as historical evidence, and indexed the May 24 C-unit artifacts.

## CLI Verification

- Documentation contract checks for linked paths, API defaults, deployment-profile
  behavior, and `git diff --check` passed.
- `.venv/bin/pytest -q` passed.
- `cd frontend && npm run build` passed.
- `cd frontend && npm run test:batch` passed: 7/7.
- `cd frontend && npm run test:casebook` passed: 6/6.
- `cd frontend && npm run test:matrix` passed: 2/2.
- `cd frontend && npm run test:casebook-csv` passed: 4/4.

## UI Verification

Not applicable. This feature changes repository documentation and tracking only;
it does not change rendered application behavior.

## Git Commits

- `a5b7be4 docs: align README with current platform`
- `a145f52 docs: reconcile checkpoint catalog`

## Known Limitations And Rollback

- The UI privacy tile, uploaded-transcript provenance, and four-participant schema
  cap remain separate Phase 0 features and are not claimed as complete here.
- Revert this feature's commits to restore the previous documentation. No runtime
  data or schema is changed.
