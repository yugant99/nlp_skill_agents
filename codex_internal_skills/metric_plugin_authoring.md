# Metric Plugin Authoring Skill

Use this when a researcher asks for a new analysis capability that cannot be expressed through a JSON/YAML study skill pack.

## Goal

Create a reusable metric plugin with a stable contract, focused tests, and local-only execution. The plugin should become selectable from skill packs without destabilizing existing metrics.

## Intake

- Ask for or infer the exact research question.
- Collect two to four synthetic transcript examples that should pass or fail the intended behavior.
- Define speaker roles, transcript notation, and whether disfluencies or nonverbals count toward the metric.
- Define output columns before implementation.
- Decide whether the request is actually configurable through `concept_lexicons`, `nonverbal_cues`, or `disfluency_tokens`; if yes, prefer a skill-pack change over code.

## Implementation Contract

- Add or update a pure metric function in `backend/analysis/metrics.py`.
- Register it through `backend/analysis/pipeline.py` using `MetricPlugin`.
- Include `id`, `label`, `description`, `category`, `output_schema`, and `calculate`.
- Add a focused test in `tests/test_metric_plugins.py`.
- If useful for demos, add a JSON template under `study_skill_packs/`.
- Keep all generated run outputs under ignored `local_data/`.

## Verification

Run these before committing:

```bash
.venv/bin/pytest tests/test_metric_plugins.py -q
.venv/bin/pytest -q
npm run lint
npm run build
```

Run frontend commands from `frontend/`.

## Guardrails

- Do not send transcript text to external models while implementing or running a metric plugin.
- Do not add arbitrary researcher-provided code execution in-process.
- Do not merge a plugin without tests that demonstrate expected rows.
- Keep plugin output tabular and exportable as CSV.
