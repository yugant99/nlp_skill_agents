# Checkpoints

This folder tracks what has been completed, what is being built, and what must be verified before each push.

Use checkpoints as the project memory for autonomous work. Each substantial feature should add or update a checkpoint entry before it is considered done.

## Checkpoint Format

Each checkpoint should record:

- Date.
- Feature or slice name.
- Goal.
- Files changed.
- CLI verification.
- UI verification.
- Git commit.
- Follow-up risks.

## Current Checkpoints

- [2026-05-13 MVP foundation](2026-05-13-mvp-foundation.md)
- [2026-05-13 V1 dynamic skill packs](2026-05-13-v1-dynamic-skill-packs.md)

## Completion Bar

A checkpoint is complete only when:

- The feature has focused tests or an explicit reason tests are not applicable.
- CLI verification has been run wherever possible.
- UI verification has been run for user-facing behavior.
- Local generated outputs remain ignored by Git.
- The change has been committed and pushed.
