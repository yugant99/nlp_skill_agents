# 2026-05-13 V3 Metric Plugin Builder Skill

## Goal

Package the project-specific metric plugin implementation workflow as a loadable internal Codex skill and point generated agent job runbooks at it.

## Files Changed

- `codex_internal_skills/metric-plugin-builder/SKILL.md`
- `codex_internal_skills/README.md`
- `backend/extensions/agent_jobs.py`
- `tests/test_agent_jobs.py`
- `checkpoints/README.md`

## CLI Verification

- `.venv/bin/pytest tests/test_agent_jobs.py -q`
- `.venv/bin/pytest -q`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`
- `rg -n "metric-plugin-builder|Internal Skill" local_data/agent_jobs/build_skill_link_ui_smoke/runbook.md`

## UI Verification

- Created a synthetic `Skill Link UI Smoke` plugin request.
- Queued the build job through the workbench UI.
- Confirmed the UI shows `local_data/agent_jobs/build_skill_link_ui_smoke/runbook.md`.
- Confirmed the generated runbook includes `cat codex_internal_skills/metric-plugin-builder/SKILL.md`.

## Git Commit

Pending.

## Follow-Up Risks

- The skill is repo-local guidance. If future agents need automatic discovery from the global Codex skill list, install or symlink it into the active Codex skills directory intentionally.
