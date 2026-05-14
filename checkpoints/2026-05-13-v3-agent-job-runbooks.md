# 2026-05-13 V3 Agent Job Runbooks

## Goal

Make queued plugin build jobs directly actionable by writing a local runbook that an implementation agent can follow without rediscovering branch names, prompt paths, edit scope, or verification commands.

## Files Changed

- `backend/extensions/agent_jobs.py`
- `tests/test_agent_jobs.py`
- `frontend/src/App.tsx`
- `frontend/src/types.ts`
- `checkpoints/README.md`

## CLI Verification

- `.venv/bin/pytest tests/test_agent_jobs.py -q`
- `.venv/bin/pytest -q`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`

## UI Verification

- Created a synthetic `Runbook Metric UI Smoke` plugin request against the local API.
- Queued the build job through the workbench UI.
- Confirmed the workbench shows `local_data/agent_jobs/build_runbook_metric_ui_smoke/runbook.md` in the queue status and Agent jobs list.
- Read `local_data/agent_jobs/build_runbook_metric_ui_smoke/runbook.md` to confirm branch/worktree, prompt, allowed files, and verification command sections.

## Git Commit

Pending.

## Follow-Up Risks

- Runbooks are instructions only. A later V4/V5 orchestrator should track started, verified, blocked, and merged job states.
