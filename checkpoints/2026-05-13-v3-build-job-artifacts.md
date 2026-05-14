# 2026-05-13 V3 Build Job Artifacts

## Goal

Bridge plugin requests to agent execution by creating a local build-job artifact that points to the implementation packet and records branch, files, commands, and queued status.

## Completed

- Added `backend/extensions/agent_jobs.py`.
- Added `AgentJob` and `AgentJobStore`.
- Added metric-plugin build job generation from plugin requests.
- Added `POST /api/plugin-requests/{request_id}/build-job`.
- Added `GET /api/agent-jobs`.
- Added frontend `Queue job` action for recent plugin requests.
- Added frontend agent job list inside the plugin registry panel.

## Files Changed

- `backend/extensions/agent_jobs.py`
- `backend/app/main.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/types.ts`
- `tests/test_agent_jobs.py`

## CLI Verification

- `.venv/bin/pytest tests/test_agent_jobs.py tests/test_plugin_requests.py -q`
- `.venv/bin/pytest -q`
- `npm run lint` from `frontend/`
- `npm run build` from `frontend/`

## UI Verification

- Save a plugin request.
- Queue a build job from the recent request row.
- Confirm the queued job appears under Agent jobs with branch name.
- Confirm local job artifact is written under `local_data/agent_jobs`.

## Git

Commit and push after verification.

## Follow-Up Risks

- Jobs are local artifacts only; they do not execute Codex or create git worktrees yet.
- Next slice should add a command/README handoff for running a queued job in a `codex/plugin-*` branch.
