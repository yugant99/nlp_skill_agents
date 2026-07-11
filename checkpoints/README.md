# Checkpoints

This folder tracks what has been completed, what is being built, and what must be verified before each push.

Use checkpoints as the project memory for autonomous work. Each substantial feature should add or update a checkpoint entry before it is considered done.

## Current Source Of Truth

- [Psychology Research Platform Roadmap](../goals/psychology-research-platform-roadmap.md)
  defines the active product boundary, end goal, gap register, delivery rules, and
  phase gates.
- Checkpoints below are historical implementation and verification records. They
  prove what existed at a point in time but do not override the active roadmap or
  current code.
- [V1/V2/V3 Roadmap](../goals/v1-v2-v3-roadmap.md) is retained as historical
  planning context.

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
- [2026-05-13 V2 skill pack studio](2026-05-13-v2-skill-pack-studio.md)
- [2026-05-13 V3 metric plugin foundation](2026-05-13-v3-metric-plugin-foundation.md)
- [2026-05-13 V3 plugin request artifacts](2026-05-13-v3-plugin-request-artifacts.md)
- [2026-05-13 V3 implementation packets](2026-05-13-v3-implementation-packets.md)
- [2026-05-13 V3 build job artifacts](2026-05-13-v3-build-job-artifacts.md)
- [2026-05-13 V3 agent job runbooks](2026-05-13-v3-agent-job-runbooks.md)
- [2026-05-13 V3 agent job lifecycle](2026-05-13-v3-agent-job-lifecycle.md)
- [2026-05-13 V3 metric plugin builder skill](2026-05-13-v3-metric-plugin-builder-skill.md)
- [2026-05-13 V3 care plan commitments metric](2026-05-13-v3-care-plan-commitments.md)
- [2026-05-13 V3 question type metrics](2026-05-13-v3-question-types.md)
- [2026-05-14 V4 study workspace backend](2026-05-14-v4-study-workspace-backend.md)
- [2026-05-14 V4 study workspace UI](2026-05-14-v4-study-workspace-ui.md)
- [2026-05-14 V5 agent job evidence](2026-05-14-v5-agent-job-evidence.md)
- [2026-05-14 V6 reproducibility bundles](2026-05-14-v6-reproducibility-bundles.md)
- [2026-05-14 V6 audit log](2026-05-14-v6-audit-log.md)
- [2026-05-14 V6 approved library](2026-05-14-v6-approved-library.md)
- [2026-05-14 V6 deployment profiles](2026-05-14-v6-deployment-profiles.md)
- [2026-05-14 Demo path and assets](2026-05-14-demo-path-and-assets.md)
- [2026-05-17 NVivo-style casebook](2026-05-17-nvivo-style-casebook.md)
- [2026-05-17 Study file assignment grid](2026-05-17-study-file-assignment-grid.md)
- [2026-05-17 Batch file import inference](2026-05-17-batch-file-import-inference.md)
- [2026-05-17 DOCX study batch upload](2026-05-17-docx-study-batch-upload.md)
- [2026-05-17 Casebook design controls](2026-05-17-casebook-design-controls.md)
- [2026-05-17 Persistent study schema](2026-05-17-persistent-study-schema.md)
- [2026-05-17 NVivo gap slices](2026-05-17-nvivo-gap-slices.md)
- [2026-05-17 Study batch history](2026-05-17-study-batch-history.md)
- [2026-05-17 Batch run drilldown](2026-05-17-batch-run-drilldown.md)
- [2026-05-24 C-unit segmentation demo](2026-05-24-cunit-segmentation-demo.html)
- [2026-05-24 C-unit developer-experience review](2026-05-24-cunit-devex-review.html)
- [2026-07-10 Phase 0 repository truth](2026-07-10-phase0-repository-truth.md)
- [2026-07-10 Phase 0 active privacy mode](2026-07-10-phase0-active-privacy-mode.md)
- [2026-07-10 Phase 0 segmentation provenance](2026-07-10-phase0-segmentation-provenance.md)
- [2026-07-10 Phase 0 study participant cap](2026-07-10-phase0-study-participant-cap.md)
- [2026-07-10 Phase 0 agent-job gates](2026-07-10-phase0-agent-job-gates.md)
- [2026-07-10 Phase 0 recoverable artifact writes](2026-07-10-phase0-recoverable-artifact-writes.md)
- [2026-07-10 Phase 0 calibrated confidence](2026-07-10-phase0-calibrated-confidence.md)

## Completion Bar

A checkpoint is complete only when:

- The feature has focused tests or an explicit reason tests are not applicable.
- CLI verification has been run wherever possible.
- UI verification has been run for user-facing behavior.
- Local generated outputs remain ignored by Git.
- The change has been committed and pushed.
