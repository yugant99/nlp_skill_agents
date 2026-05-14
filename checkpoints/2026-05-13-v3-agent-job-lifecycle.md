# 2026-05-13 V3 Agent Job Lifecycle

## Goal

Let queued plugin implementation jobs move through explicit local states so autonomous work can be tracked without relying on memory or chat context.

## Files Changed

- `backend/extensions/agent_jobs.py`
- `backend/app/main.py`
- `tests/test_agent_jobs.py`
- `frontend/src/api.ts`
- `frontend/src/App.tsx`
- `checkpoints/README.md`

## CLI Verification

- `.venv/bin/pytest tests/test_agent_jobs.py -q`
- `.venv/bin/pytest -q`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`
- `curl -sS http://127.0.0.1:8000/api/agent-jobs | .venv/bin/python -m json.tool`

## UI Verification

- Reloaded the local workbench.
- Used the new `Verify` control on `build_runbook_metric_ui_smoke`.
- Confirmed the workbench status message says `Updated build_runbook_metric_ui_smoke -> verified`.
- Confirmed the Agent jobs list shows the `verified` status.
- Confirmed `/api/agent-jobs` returns `"status": "verified"` for the same job.

## Git Commit

Pending.

## Follow-Up Risks

- Status changes are manual controls. Later orchestration should move jobs automatically after worktree creation, verification, PR creation, and merge.
