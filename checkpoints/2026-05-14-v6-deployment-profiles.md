# 2026-05-14 V6 Deployment Profiles

## Goal

Add local deployment profile checks so the app can report whether it is running in `dev`, `lab-local`, or `secure-offline` mode.

## Files Changed

- `backend/storage/deployment_profiles.py`
- `backend/app/main.py`
- `tests/test_deployment_profiles.py`
- `tests/test_api.py`
- `checkpoints/README.md`

## CLI Verification

Pending final verification before commit:

- `.venv/bin/pytest tests/test_deployment_profiles.py tests/test_api.py::test_deployment_profile_endpoint_reports_secure_offline_status -q`
- `.venv/bin/pytest -q`
- `cd frontend && npm run lint`
- `cd frontend && npm run build`

## UI Verification

API-level smoke is sufficient for this backend foundation:

- Call `GET /api/deployment-profile/secure-offline`.
- Confirm it reports local data root and OpenRouter disabled checks.

## Git Commit

Pending.

## Follow-Up Risks

- Profiles currently check only core local/security conditions. Later slices should add filesystem permissions, library availability, backup target, and model-provider policy checks.
