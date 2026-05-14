# 2026-05-14 V5 Agent Job Evidence

## Goal

Attach structured verification evidence to local agent jobs so review gates can persist test/build/UI smoke outcomes as auditable artifacts.

## Files Changed

- `backend/extensions/agent_jobs.py`
- `backend/app/main.py`
- `tests/test_agent_jobs.py`
- `frontend/src/types.ts`
- `frontend/src/api.ts`
- `frontend/src/App.tsx`
- `checkpoints/README.md`

## CLI Verification

- `.venv/bin/pytest tests/test_agent_jobs.py -q`
- `.venv/bin/pytest -q`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`
- `rg -n "Recorded from the Agent jobs panel" local_data/agent_jobs -g 'ui_review.json'`

## UI Verification

- Started local backend and frontend.
- Used browser automation against `http://127.0.0.1:5173/`.
- Clicked the `Evidence` button in the Agent jobs panel.
- Confirmed the UI shows `Evidence recorded:` with an `/evidence/ui_review.json` path.
- Confirmed `local_data/agent_jobs/build_skill_link_ui_smoke/evidence/ui_review.json` contains the expected summary.
- Captured `/tmp/nlp_skill_agents_v5_evidence.png`.

## Git Commit

Pending.

## Follow-Up Risks

- Evidence is manually recorded from the UI for now. Later V5 orchestration should capture command output automatically after verification commands run.
