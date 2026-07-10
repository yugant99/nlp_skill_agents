# 2026-07-10 Phase 0 Agent-Job Gates

## Goal

Replace manually editable agent-job statuses with a server-enforced lifecycle and
evidence gates while keeping the existing local implementation-packet workflow.

## Files Changed

- `backend/extensions/agent_jobs.py`
- `backend/app/main.py`
- `tests/test_agent_jobs.py`
- `frontend/src/agentJobLifecycle.ts`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/types.ts`
- `frontend/tests/agentJobLifecycle.test.mjs`
- `frontend/package.json`
- `README.md`
- `checkpoints/README.md`
- `goals/psychology-research-platform-roadmap.md`

## Implemented

- Enforced `queued -> in_progress <-> blocked -> verified -> merged` movement,
  with `queued -> blocked` also available before work starts.
- Rejected unsupported states and invalid lifecycle jumps at both the store and API
  boundaries.
- Required one passed evidence record with the exact command text for every job
  verification command before `verified` becomes available.
- Required passed, non-empty `merge` evidence before `merged` becomes available.
- Limited evidence status values to `passed` and `failed`.
- Added computed `available_transitions` to every agent-job API payload.
- Made the workbench render only server-unlocked lifecycle actions and display
  explicit waiting/completion states.
- Renamed the previous generic `Evidence` action to `Review note` so a manual UI
  note is not presented as command-execution proof.

## CLI Verification

- `.venv/bin/pytest tests/test_agent_jobs.py -q` passed: 7/7.
- `.venv/bin/pytest -q` passed: 124/124.
- `cd frontend && npm run test:agent-jobs` passed: 2/2.
- `cd frontend && npm run build` passed.
- All frontend helper suites passed: 27/27.
- Direct API attempts to move `queued -> verified` and `verified -> merged`
  without their required evidence returned HTTP 400.
- `git diff --check` passed before both implementation commits.

## UI Verification

- Launched the backend with isolated data under
  `local_data/tmp/agent-job-gate-qa` and the frontend on loopback.
- A queued job showed only `Start`, `Block`, and `Review note`; `Verify` and
  `Merge` were absent.
- After `Start`, `Verify` remained absent until passed evidence existed for all
  three exact verification commands.
- After verification, the UI hid lifecycle actions and showed
  `Waiting for required evidence` until merge evidence was recorded.
- Merge evidence unlocked only `Merge`; completing it changed the status to
  `merged` and showed `Lifecycle complete`.
- Browser console reported zero errors and zero warnings.

## Git Commits

- `76be43b Enforce agent job lifecycle gates`
- `8623f05 Show only unlocked agent job actions`

## Known Limitations And Rollback

- Evidence is still recorded by a trusted local caller. A durable worker must
  execute commands, capture outputs, hash artifacts, and attest results before
  these records can be treated as production execution proof.
- Existing verified or merged artifacts are accepted as their stored current
  state; this feature does not reconstruct missing historical transitions.
- Job and evidence JSON still use direct file writes. Recoverable atomic writes
  are the next separate Phase 0 gap.
- Revert the two implementation commits to restore unrestricted status updates
  and the previous four-button UI. Existing job JSON remains structurally
  compatible because `available_transitions` is computed at API time.
