# 2026-05-13 V3 Plugin Request Artifacts

## Goal

Add the first request-to-extension artifact flow: researchers can save a local request for a new metric plugin with a research question, output columns, and synthetic examples.

## Completed

- Added V3-V6 modular plan files under `plans/`.
- Extended the high-level roadmap through V6.
- Added `backend/extensions/plugin_requests.py`.
- Added local plugin request persistence under ignored `local_data/plugin_requests`.
- Added `POST /api/plugin-requests`.
- Added `GET /api/plugin-requests`.
- Added frontend plugin request form inside the plugin registry panel.
- Added recent plugin request history in the UI.

## Files Changed

- `plans/README.md`
- `plans/v3-agentic-extension-system.md`
- `plans/v4-study-workspace-dashboard-composer.md`
- `plans/v5-local-orchestration-review-gates.md`
- `plans/v6-institutional-research-platform.md`
- `goals/v1-v2-v3-roadmap.md`
- `backend/extensions/__init__.py`
- `backend/extensions/plugin_requests.py`
- `backend/app/main.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/styles.css`
- `frontend/src/types.ts`
- `tests/test_plugin_requests.py`

## CLI Verification

- `.venv/bin/pytest tests/test_plugin_requests.py -q`
- `.venv/bin/pytest -q`
- `npm run lint` from `frontend/`
- `npm run build` from `frontend/`

## UI Verification

- Start backend on `127.0.0.1:8000`.
- Start frontend on `127.0.0.1:5173`.
- Open the workbench.
- Save an `Empathy Response Metric` plugin request from the plugin registry panel.
- Confirm the success message and recent request row render.
- Confirm local artifact is written under `local_data/plugin_requests`.

## Git

Commit and push after verification.

## Follow-Up Risks

- Requests are draft artifacts only; they do not yet create branches or generate plugin code.
- V5 should add job/review gates around converting these artifacts into branch work.
- Examples are intended to be synthetic; avoid pasting sensitive transcript excerpts.
