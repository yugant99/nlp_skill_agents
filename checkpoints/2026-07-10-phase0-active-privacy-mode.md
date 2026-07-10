# 2026-07-10 Phase 0 Active Privacy Mode

## Goal

Replace the unconditional `No cloud I/O` header claim with a status derived from
the backend's actual OpenRouter configuration.

## Files Changed

- `backend/storage/deployment_profiles.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/privacyMode.ts`
- `frontend/src/types.ts`
- `frontend/tests/privacyMode.test.mjs`
- `frontend/package.json`
- `tests/test_api.py`
- `tests/test_deployment_profiles.py`
- `README.md`
- `checkpoints/README.md`
- `goals/psychology-research-platform-roadmap.md`

## Implemented

- The secure-offline profile now detects OpenRouter configuration from both the
  process environment and the same local `.env` loading path used by OpenRouter.
- The frontend loads the secure-offline profile at startup and reports `Offline
  ready`, `External authoring configured`, or `Status unavailable`.
- The header labels this state as `Privacy` and no longer claims all workflows have
  no cloud I/O.
- Added focused frontend and backend regression coverage for both configuration
  states and missing profile evidence.

## CLI Verification

- `.venv/bin/pytest tests/test_deployment_profiles.py tests/test_api.py -q` passed.
- `.venv/bin/pytest -q` passed.
- `cd frontend && npm run build` passed.
- Existing frontend helper suites passed: 19/19.
- `cd frontend && npm run test:privacy` passed: 3/3.
- `git diff --check origin/master...HEAD` passed.

## UI Verification

- Launched the backend and frontend on `127.0.0.1:8000` and `127.0.0.1:5173`.
- Playwright showed `Privacy: External authoring configured` with the local
  OpenRouter configuration present.
- Restarted the backend with an explicitly empty key and Playwright showed
  `Privacy: Offline ready` after reload.
- Browser console reported zero errors and zero warnings.
- No OpenRouter request was made during either check.

## Git Commits

- `2029d19 fix: detect configured external authoring`
- `cb9d349 feat: model active privacy mode in frontend`
- `914b245 fix: show active privacy state in workspace`
- `dad07fb docs: add privacy regression command`

## Known Limitations And Rollback

- The status reports whether external authoring is configured, not a live ledger
  of individual network requests. Request-level egress policy remains Phase 4 work.
- Revert the feature commits to restore the previous profile and header behavior.
  No stored research data or schema is changed.
