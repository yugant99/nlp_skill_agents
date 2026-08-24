# 2026-08-23 Four-Luna Professor Demo

## Goal

Prove one bounded idea for a classroom walkthrough: a short synthetic transcript
can trigger exactly four remote Luna specialists, four strict JSON results can be
validated and combined locally, native usage and cost can be receipted, and a
human can accept or restore a local demo revision.

This is not Phase 4 completion, an accuracy study, or production research-data
mutation. The lane is explicitly synthetic-only. OpenRouter inference is remote;
the UI, validation, merge, receipt, and revision store are local.

## Implemented

- An isolated provider client hard-pins `openai/gpt-5.6-luna` to `azure/eu`,
  requires ZDR and strict structured outputs, disables fallback and cache, turns
  reasoning off, makes no retries, and caps each completion at 800 tokens.
- A live preflight validates the key, current endpoint metadata, required
  parameters, ZDR listing, bounded request size, and a conservative four-call
  maximum below `$0.25` before inference.
- Four fixed specialists run exactly once: speaker turns, timing and pauses,
  repair and overlap, and redaction plus nonverbal cues.
- Pydantic strict models reject prose, fences, missing or extra fields, wrong
  specialist identities, duplicate or missing line indexes, and unsafe local
  redaction composition.
- The local merger applies fixed field ownership and deterministic ordering. It
  does not use the existing order-sensitive segmentation patch merger.
- Every run persists an atomic local snapshot with four safe call receipts,
  native tokens and cost, original and candidate SHA-256 digests, and revision
  state. Raw provider responses, prompts, reasoning text, headers, and keys are
  not stored.
- Run completion leaves immutable revision 0 active. A separate idempotent Accept
  action activates revision 1. Restore switches back to revision 0 without
  deleting the candidate.
- `/professor-demo` is a dedicated responsive UI route. The existing research
  workbench remains at `/`, and the two apps are split into separate bundles.

## Verification

Focused backend/API/provider gate:

```text
14 passed
```

The complete backend regression suite also passed `760 / 760` on the final
worktree state.

Frontend acceptance-gate helpers:

```text
3 passed
```

The production frontend build passed with 1,714 modules transformed. Browser QA
at 375x812, 768x1024, and 1280x720 found no console errors. The demo route no
longer downloads the full research `App` module.

Final live synthetic run:

```text
run_id: demo_a7924121ce8742f8b53e6b403d8518ec
attempted calls: 4
completed calls: 4
strict valid results: 4
returned provider: Azure on all four calls
returned model: openai/gpt-5.6-luna on all four calls
finish reason: stop on all four calls
total tokens: 1674
reasoning tokens: 0
native total cost: $0.00074558
```

The UI issued one run request and showed the original, the locally composed
candidate, all four specialist receipts, and the human gate. Accept activated
revision 1, reload recovered the accepted revision from disk, and Restore returned
the active pointer to revision 0. Both accepted and reverted timestamps were
retained, the original SHA-256 stayed unchanged, and the candidate remained
available.

Secret scans found no key-shaped value outside ignored `.env`. The live run and
screenshots remain local ignored artifacts.

## Git

Branch: `codex/professor-four-luna-demo`

Pushed checkpoints before this final record:

- `ae88f5e` strict four-Luna backend, local merge, receipt, and revisions
- `465c967` focused professor UI
- `7f2bc12` contract tests and runbook
- `207842b` live-output polish and route bundle isolation

## Remaining Boundary

- Rotate the demo OpenRouter key because it was pasted into chat.
- Re-run live preflight on demo day because provider availability, policy, and
  price can change.
- Use only the bundled synthetic transcript. Real participant data, research
  database mutation, representative accuracy evaluation, multi-user concurrency,
  deployment, and Phase 4 exit criteria remain explicitly out of scope.
