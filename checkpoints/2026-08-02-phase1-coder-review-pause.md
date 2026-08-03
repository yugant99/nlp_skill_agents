# 2026-08-02 Phase 1 Coder-Review Pause Handoff

## Current State

Work is paused on `codex/phase1-coder-review-subsystem`. The branch is pushed
through `22e5c368b2d43490f54057e2ca4541799e9ccf0e` and remains based on merged
master `db29e69ba6257dbc038b072f52946a234d6f5021`.

The worktree intentionally contains an independently approved but uncommitted
cursor-anchor external-preflight fix in:

- `backend/qualitative/research_reviews.py`
- `tests/test_research_reviews.py`

`uv.lock` is an unrelated user-owned untracked file. Preserve it and never stage
it with this feature.

## Completed

- The design contract, contiguous qualitative migration 4, strict service, API,
  archive/restore integration, adversarial tests, README/roadmap truth updates,
  and feature checkpoint are committed and pushed in six coherent commits.
- Researcher provenance, exact imported agent suggestions, append-only reviewer
  decisions, attributable atomic audits, strict external evidence/coding
  validation, canonical pagination, and no-provider boundaries are implemented.
- Independent domain, API/security, storage, archive, and branch-wide reviews
  found and closed chronology, identity-collision, audit-streaming, bootstrap,
  stored-dependency, cursor-canonicalization, archive, and privacy failures.
- The last branch-wide review found one more P1: a page-two cursor could bypass
  corruption in its anchor's external evidence. The fix now includes the loaded
  anchor's complete target closure in the external preflight and was independently
  approved.
- Latest verified gates on the uncommitted fix: targeted anchor regression 1/1,
  research-review service 19/19, changed-file Ruff, Python compilation, and
  `git diff --check`.
- Before that final two-file fix, the pushed branch passed the full backend suite
  690/690, API 96/96, archive 149/149, the production frontend build, and all
  eight frontend helper suites 30/30.

## Resume Resolution

The exact saved worktree was restored without modifying the user-owned `uv.lock`.
Both targeted cursor-anchor regressions and all 20 research-review service tests
passed. The previously interrupted complete backend gate then passed 692/692,
the frontend production build passed with 1,710 modules transformed,
all eight frontend helper suites passed 30/30, and Ruff, Python compilation, and
`git diff --check` remained green. The final checkpoint commit containing this
resolution may now be pushed and taken through normal pull-request review.

## Remaining At The Time Of Pause

1. Rerun the complete backend suite on the exact uncommitted anchor-fix state.
   The expected collection is 691 tests. The previous rerun was interrupted by
   the requested pause, so it must not be inferred as green.
2. If green, update the feature checkpoint from provisional to final counts,
   commit the two code/test files plus checkpoint correction, and push.
3. Confirm the worktree contains only the unrelated `uv.lock`, open the PR against
   `master`, verify checks/review, merge, verify merged master, and delete the
   local and remote feature branch.
4. Continue Phase 1 with saved-query and export entities, then perform
   reconciliation/lifecycle hardening and the Phase 1 exit audit.
5. Phase 2 manual-workbench UI and Phase 4 provider/runtime work remain out of
   scope until their own reviewed slices.

## Resume Commands

```text
git status --short
git diff -- backend/qualitative/research_reviews.py tests/test_research_reviews.py
.venv/bin/pytest -q tests/test_research_reviews.py
.venv/bin/pytest -q
ruff check backend/qualitative/research_reviews.py tests/test_research_reviews.py
git diff --check
```

Do not use `git add -A`; stage only the two intentional implementation files and
the explicitly updated checkpoint documents. Do not touch migration 1.
