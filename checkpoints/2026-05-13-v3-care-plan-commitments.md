# 2026-05-13 V3 Care Plan Commitments Metric

## Goal

Add a healthcare-focused metric plugin that detects caregiver future-action commitments such as calling, scheduling, arranging, or following up on care tasks.

## Files Changed

- `backend/analysis/metrics.py`
- `backend/analysis/pipeline.py`
- `tests/test_metric_plugins.py`
- `tests/test_skill_packs.py`
- `study_skill_packs/interaction_dynamics_healthcare.json`
- `frontend/src/App.tsx`
- `checkpoints/README.md`

## CLI Verification

- `.venv/bin/pytest tests/test_metric_plugins.py tests/test_skill_packs.py -q`
- `.venv/bin/pytest -q`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`

## UI Verification

- Used browser automation against `http://127.0.0.1:5173/`.
- Pasted a four-turn caregiver/participant transcript.
- Activated a `Care Plan UI Demo` skill pack with `base_metrics` and `care_plan_commitment_metrics`.
- Ran local analysis through the UI.
- Confirmed the UI rendered `Care Plan Commitment Metrics` with caregiver, participant, and total rows.
- Confirmed the `care_plan_commitment_metrics.csv` export link is visible.

## Git Commit

Pending.

## Follow-Up Risks

- The first detector is deterministic and conservative. It should evolve through researcher examples before being treated as a validated coding scheme.
