# 2026-08-02 Phase 1 Qualitative-Export Pause Handoff

## Exact Git State

Work is paused on `codex/phase1-qualitative-export-subsystem` at merged master
`ef68b63a740c1e161d980e7121bb76d919d11af5` (PR #27). Local `master`,
`origin/master`, and the feature-branch base all resolve to that commit.

The tracked base is unchanged. The only task-owned worktree change is the new,
untracked proposed design contract:

- `docs/architecture/qualitative-export-service-design.md`

The untracked root `uv.lock` is user-owned and must remain untouched and unstaged.

No qualitative migration 6, runtime service, API, archive hook, test, README, or
roadmap implementation change exists yet. No commit or push has been made on this
feature branch.

## Merged And Verified Phase 1 State

The following qualitative slices are merged into master and were independently
reviewed and regression-gated:

- qualitative core and audit boundary (PR #18);
- versioned codebooks and strict code ordering (PRs #21 and #22);
- cases and typed attributes (PR #23);
- coding references (PR #24);
- memos and annotations (PR #25);
- local researcher identity, stored agent suggestions, and reviewer decisions
  (PR #26); and
- saved-query definitions (PR #27).

The exact post-merge saved-query master state passed 752 backend tests, the
frontend production build with 1,710 modules, and all eight frontend helper suites
with 30 tests. Those results prove `ef68b63`; they do not verify the untracked
qualitative-export design or any future implementation.

Of the roadmap's 17 named Phase 1 durable entity families, 16 are implemented;
the qualitative export is the remaining entity. Phase 1 is nevertheless not 94%
complete as an exit gate because reconciliation, lifecycle integrity, and the
written exit audit remain separate required work after the entity layer.

## Read-Only Export Discovery Completed

Three independent discovery reviews mapped the domain boundary, migration and
filesystem journal, API, archive, and security requirements. The current proposed
decision is:

- add only contiguous qualitative migration 6;
- support exactly one Phase 1 export kind: one frozen codebook version in the
  existing portable `nlp-skill-agents.codebook-version` JSON format;
- use caller-stable `qex_` identity and a deterministic managed local artifact;
- reserve the artifact identity transactionally, publish it atomically without
  overwriting divergent bytes, then atomically close the export row, audit event,
  and operation;
- allow exact response-loss/crash retry and reject divergent reuse;
- never acquire a qualitative transaction while already holding the workspace
  lock;
- make format-2 archive/restore validate complete database/filesystem closure and
  make format 1 reject populated export state directly; and
- keep query results, coded excerpts, matrices, reliability reports, audit-trail
  exports, and reproducibility bundles in their later phases.

The proposed contract records the exact request digest, state machine, crash
matrix, API response/download shape, privacy-safe errors, path/symlink defenses,
archive behavior, and adversarial verification matrix. It is not yet independently
reviewed as a written artifact and is not an approved implementation contract.

## Model, Privacy, And Cost Boundary

The active goal now records this hard Phase 4 contract:

- only `openai/gpt-5.6-luna` for all four specialist calls;
- no auto-routing, alias, fallback, retry, judge, or response-healing call;
- strict JSON Schema with `additionalProperties=false` and required provider
  parameter support;
- lowest supported reasoning, no reasoning text, and at most 800 response tokens;
- Zero Data Retention routing with provider data collection denied;
- at most four calls total;
- calculate worst-case cost before egress and abort above USD $0.25; and
- persist only returned usage/cost, never credentials, headers, or unrestricted
  raw responses.

No OpenRouter/provider/model call has occurred, no transcript has left the local
machine, and no cost has been incurred by this goal run.

## Exact Remaining Phase 1 Work

1. Independently review the proposed qualitative-export contract, resolve all
   material findings, then checkpoint-commit and push it.
2. Add only migration 6 and its adversarial migration proof; never modify
   migrations 1 through 5.
3. Implement the strict recoverable service and exclusive atomic artifact
   publication, then checkpoint-commit and push.
4. Add the narrow create/read/list/download API, format-2 staged validation,
   format-1 rejection, privacy-safe error translation, and focused adversarial
   tests; checkpoint-commit and push.
5. Run focused and full backend/frontend/helper gates, complete independent
   reviews, fix and rerun, open/merge the PR, verify merged master, and delete the
   redundant branch.
6. Implement and merge the separate operation reconciliation/recovery slice.
7. Implement and merge close/reopen, retention, and backup/restore lifecycle
   integrity.
8. Write and verify the Phase 1 exit audit against the published gate: import,
   mutate, close, reopen, back up, restore, retain data, and preserve evidence IDs.

Only after step 8 is verified on merged master does Phase 2 UI/manual-workbench
scope begin. Phase 3 rigor/dashboard work and the Phase 4 bounded Luna runtime
remain after that.

## Resume Command

Resume from this branch and treat the worktree as authoritative. Read this
checkpoint and the proposed design, verify the exact diff, collect independent
contract reviews, and do not change schema/runtime until the contract is approved.
