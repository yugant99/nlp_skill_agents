# Metric Authoring Skill

Use this when adding or changing a deterministic metric skill in this repo.

## Goal

Metric skills should produce repeatable analysis from structured inputs. Keep them deterministic, testable, and isolated from generated local outputs.

## Authoring Loop

1. Define the metric contract first: name, input fields, output fields, score scale, failure modes, and any normalization rules.
2. Add the smallest implementation surface that fits existing metric patterns. Prefer pure functions and explicit data structures over hidden state or runtime side effects.
3. Keep thresholds, rubrics, and mappings in code or structured fixtures where tests can inspect them.
4. Add focused tests for edge cases: missing input, empty input, boundary scores, normalization, and stable ordering.
5. Run the narrow metric tests first, then the broader backend test suite used by the repo.

## Determinism Rules

- Do not call LLMs, network services, clocks, random generators, or mutable global state from deterministic metrics.
- If a metric needs generated examples or exploratory outputs, write them under `local_data/` or another ignored local-only path.
- Do not commit generated reports, transcripts, cached model output, or scratch evaluations.
- Keep sample fixtures synthetic and minimal. Do not include transcript content.

## Verification Before Commit Or Push

Before staging, committing, or pushing metric work:

1. Check `git status --short` and confirm only intended files changed.
2. Run the metric-specific test command.
3. Run the repo's broader backend verification command when the change can affect shared analysis behavior.
4. Inspect the diff for accidental local-only outputs, generated artifacts, or runtime-facing documentation changes.
