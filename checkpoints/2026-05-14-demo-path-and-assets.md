# 2026-05-14 Demo Path And Assets

## Goal

Lock the professor demo path and add tracked synthetic assets so the demo does not depend on live typing.

## Files Changed

- `demo_assets/healthcare_demo/README.md`
- `demo_assets/healthcare_demo/demo_script.md`
- `demo_assets/healthcare_demo/skill_pack.json`
- `demo_assets/healthcare_demo/transcripts/participant_001.txt`
- `demo_assets/healthcare_demo/transcripts/participant_002.txt`
- `demo_assets/healthcare_demo/transcripts/participant_003.txt`
- `tests/test_demo_assets.py`
- `checkpoints/README.md`

## CLI Verification

Completed before commit:

- `.venv/bin/pytest tests/test_demo_assets.py -q` - passed.
- `.venv/bin/pytest -q` - passed, 78 tests.
- `cd frontend && npm run lint` - passed.
- `cd frontend && npm run build` - passed.

## UI Verification

Completed browser smoke against `http://127.0.0.1:5173/` and backend `http://127.0.0.1:8000/`:

- Pasted and activated `demo_assets/healthcare_demo/skill_pack.json`.
- Ran `participant_001.txt` through the single-transcript workflow.
- Verified results for base metrics, question type metrics, care plan commitment metrics, and concept count metrics.
- Ran the three-transcript Study Workspace batch from the README paste block.
- Queued a plugin job and recorded evidence.
- Exported a reproducibility bundle and checked audit/bundle/profile API paths.
- Screenshot: `/tmp/nlp_skill_agents_demo_path.png`.
- Bundle manifest: `local_data/bundles/demo-healthcare-batch-20260514065936/manifest.json`.

## Git Commit

Completed in `feat: add healthcare demo assets`.

## Follow-Up Risks

- The demo assets are synthetic and should remain clearly labeled as non-identifiable demo data.
- The Study Workspace demo uses a pasted batch block, not multi-file upload.
- Some demo labels appear in both the skill registry and result tables, so browser smoke locators need to target visible workflow regions carefully.
