---
name: metric-plugin-builder
description: Use when implementing queued NLP Skill Agents metric plugin build jobs from local agent job runbooks or plugin request implementation prompts.
---

# Metric Plugin Builder

## Overview

Build one deterministic metric plugin from a queued researcher request. Keep the plugin local-first, tabular, tested, and selectable through the existing metric registry.

## When To Use

Use this for `metric_plugin_build` agent jobs, plugin request implementation prompts, and requests that need Python metric code rather than a JSON/YAML skill-pack configuration.

Do not use this when the requested behavior can be expressed with `concept_lexicons`, `nonverbal_cues`, `speaker_prefixes`, or `disfluency_tokens`.

## Workflow

1. Read the job runbook and generated implementation prompt.
2. Confirm the requested metric id, research question, examples, and output columns.
3. Add a focused failing test in `tests/test_metric_plugins.py`.
4. Implement a pure metric function in `backend/analysis/metrics.py`.
5. Register it in `backend/analysis/pipeline.py` as a `MetricPlugin`.
6. Add or update a demo study skill pack only if it helps activate the metric.
7. Run the exact verification commands from the runbook.
8. Commit only the scoped plugin change and checkpoint.

## Output Contract

- Metric rows must be dictionaries with stable CSV-friendly columns.
- The first column should usually be `speaker`, `role`, `turn_index`, or another grouping key researchers can interpret.
- Examples should be short transcript snippets or turn references, not long transcript copies.
- Counts and rates should be numeric, not formatted strings.

## Guardrails

- Do not send transcript content or local outputs to external models.
- Do not execute researcher-supplied code.
- Do not alter existing metrics unless the new test proves a shared helper must change.
- Do not expand the allowed file scope without documenting why in the checkpoint.
- Do not mark the job `verified` until focused tests, full backend tests, frontend lint/build, and UI smoke have run when applicable.
