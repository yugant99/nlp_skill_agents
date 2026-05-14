# 2026-05-13 V3 Question Type Metrics

## Goal

Add a linguistics-oriented metric plugin that separates open questions from yes/no questions by speaker, giving psychology and healthcare researchers a clearer view of prompting style.

## Files Changed

- `backend/analysis/metrics.py`
- `backend/analysis/pipeline.py`
- `tests/test_metric_plugins.py`
- `tests/test_skill_packs.py`
- `study_skill_packs/interaction_dynamics_healthcare.json`
- `frontend/src/App.tsx`
- `checkpoints/README.md`

## CLI Verification

- `.venv/bin/pytest tests/test_metric_plugins.py -q`
- `.venv/bin/pytest -q`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`

## UI Verification

- Used browser automation against `http://127.0.0.1:5173/`.
- Pasted a four-turn transcript with open and yes/no questions.
- Activated a `Question Type UI Demo` skill pack with `base_metrics` and `question_type_metrics`.
- Ran local analysis through the UI.
- Confirmed the API returned `question_type_metrics`.
- Confirmed the UI rendered `Question Type Metrics` with open/yes-no question columns and CSV export.

## Git Commit

Pending.

## Follow-Up Risks

- The classifier is rule-based. It should be expanded with examples for indirect prompts and multi-question turns.
