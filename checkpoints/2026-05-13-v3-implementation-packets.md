# 2026-05-13 V3 Implementation Packets

## Goal

Turn each saved plugin request into a local implementation packet that a Codex branch worker can use to build the requested metric plugin.

## Completed

- Each plugin request now writes:
  - `local_data/plugin_requests/<request_id>.json`
  - `local_data/plugin_requests/<request_id>/implementation_prompt.md`
- The implementation prompt includes:
  - branch name
  - metric id
  - research question
  - output columns
  - synthetic examples
  - implementation contract
  - verification commands
- `POST /api/plugin-requests` returns `implementation_prompt_path`.
- Frontend success state shows the generated prompt path.

## Files Changed

- `backend/extensions/plugin_requests.py`
- `backend/app/main.py`
- `frontend/src/App.tsx`
- `frontend/src/types.ts`
- `tests/test_plugin_requests.py`

## CLI Verification

- `.venv/bin/pytest tests/test_plugin_requests.py -q`
- `.venv/bin/pytest -q`
- `npm run lint` from `frontend/`
- `npm run build` from `frontend/`

## UI Verification

- Save a new plugin request from the Plugin Registry panel.
- Confirm the success text includes `implementation_prompt.md`.
- Confirm local packet file exists under ignored `local_data/plugin_requests/<request_id>/implementation_prompt.md`.

## Git

Commit and push after verification.

## Follow-Up Risks

- The packet is a handoff artifact only; it does not yet automatically create the branch.
- Next slice should add an explicit build-job artifact that references this prompt and tracks branch/status/provenance.
