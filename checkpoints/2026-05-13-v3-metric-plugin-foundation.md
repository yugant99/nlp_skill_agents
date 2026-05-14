# 2026-05-13 V3 Metric Plugin Foundation

## Goal

Start V3 by adding a reusable metric plugin interface and proving that a new analysis capability can appear in the workbench through plugin registration.

## Completed

- Added `MetricPlugin` registry metadata:
  - metric id
  - label
  - description
  - category
  - output schema
  - deterministic calculate function
- Registered existing metrics through the plugin interface.
- Added `interaction_dynamics_metrics` as the first V3 metric plugin.
- Added `GET /api/metric-plugins` for plugin catalog discovery.
- Added frontend plugin registry panel showing registered/active plugin metrics.
- Added built-in `interaction_dynamics_healthcare` study skill pack template.
- Updated local and OpenRouter-assisted skill authoring to allow interaction dynamics metrics.
- Added internal Codex metric plugin authoring guidance.

## Files Changed

- `backend/analysis/metric_plugins.py`
- `backend/analysis/metrics.py`
- `backend/analysis/pipeline.py`
- `backend/analysis/skill_builder.py`
- `backend/app/main.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/styles.css`
- `frontend/src/types.ts`
- `study_skill_packs/interaction_dynamics_healthcare.json`
- `codex_internal_skills/metric_plugin_authoring.md`
- `tests/test_metric_plugins.py`
- `tests/test_skill_builder.py`
- `tests/test_skill_packs.py`

## CLI Verification

- `.venv/bin/pytest tests/test_metric_plugins.py tests/test_skill_packs.py::test_builtin_interaction_dynamics_plugin_template_loads -q`
- `.venv/bin/pytest tests/test_skill_builder.py::test_draft_skill_pack_adds_interaction_plugin_when_requested tests/test_metric_plugins.py -q`
- `.venv/bin/pytest -q`
- `npm run lint` from `frontend/`
- `npm run build` from `frontend/`

## UI Verification

- Started backend on `127.0.0.1:8000`.
- Started frontend on `127.0.0.1:5173`.
- Used browser automation to draft `V3 Interaction Demo` from a turn-taking/question-balance study brief.
- Confirmed CG/P prefixes were preserved.
- Ran a pasted CG/P transcript.
- Confirmed plugin registry rendered.
- Confirmed `Interaction Dynamics Metrics` table rendered with word share, question turns, average words per turn, and longest turn words.
- Screenshot: `/tmp/nlp_skill_agents_v3_interaction_demo.png`.

## Git

Commit and push after verification.

## Follow-Up Risks

- V3 does not yet create branches or pull requests from the app itself.
- V3 does not yet sandbox arbitrary researcher-provided code; plugin metrics are still repository-authored deterministic Python.
- Next V3 slice should add a plugin request artifact: a local JSON spec that captures researcher examples and can drive a Codex branch/worktree implementation.
