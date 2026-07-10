# Psychology Research Platform Roadmap

Status: Active product north star

Created: 2026-07-10

Initial source branch: `codex/cunit-demo-system` at `f3f2d36`

This document is the current product-direction and gap-tracking source of truth.
The older `v1-v2-v3-roadmap.md` remains useful as implementation history, but it
does not describe the current C-unit-first product or the complete path from demo
to production.

## Product Boundary

The primary user is a psychology researcher, research assistant, lab analyst, or
principal investigator working with transcript-heavy qualitative or mixed-method
studies.

The first product is not a clinical decision-support system. It must not diagnose,
recommend treatment, or present automated psychological interpretation as ground
truth. If clinical use becomes a goal, it requires a separate product, regulatory,
validation, and risk plan.

## End Goal

Build a local-first psychology evidence workbench where researchers can:

1. Create a study and import a transcript corpus plus participant/session data.
2. Verify source-preserving transcript and C-unit segmentation.
3. Create, import, review, and version a qualitative codebook.
4. Manually code exact source passages and attach memos and annotations.
5. Use local agents to propose codes, summaries, or repairs without silently
   changing accepted research data.
6. Compare participants, conditions, sessions, weeks, themes, and coders through
   evidence-linked queries and matrices.
7. Measure coder agreement and adjudicate disagreements.
8. Export every result with source evidence, human/agent provenance, audit history,
   and reproducibility metadata.

The dashboard is one view over this evidence system. It is not the product by
itself.

The core evidence chain is:

```text
source passage
  <-> transcript revision / C-unit
  <-> code and codebook version
  <-> human coder or agent proposal
  <-> participant/case attributes
  <-> memo and annotation
  <-> saved query or matrix result
  <-> report and reproducibility export
```

## Product Strategy

Use NVivo as a workflow benchmark, not as a feature-by-feature cloning target.
The focused product advantage should be:

- transparent transcript evidence;
- C-unit-aware analysis;
- researcher-controlled agents;
- local-first handling of sensitive data;
- reproducible qualitative and mixed-method analysis;
- a clearer, more approachable workflow than a general-purpose QDA suite.

## Deployment Direction

The first production topology is a single-host local appliance:

- one Windows 11 Alienware laptop;
- one researcher initially;
- browser UI and FastAPI service bound to loopback;
- built frontend served locally;
- SQLite as the authoritative metadata and workflow store;
- immutable or versioned files for large source and result artifacts;
- a dedicated background worker for analysis and inference;
- a local OpenAI-compatible model endpoint selected through hardware benchmarks;
- optional OpenRouter access for explicitly permitted, non-sensitive tasks;
- no automatic cloud fallback.

Do not introduce Postgres, Redis, Celery, Kubernetes, distributed workers, or
real-time cloud collaboration until observed load or multi-host requirements prove
they are needed.

## Reversible Development And Delivery Rules

This is an agentic repository. Every feature must remain independently reviewable,
provable, and reversible.

For each feature:

1. Start from the current remote default branch or the explicitly approved stacked
   feature base.
2. Create one narrowly named `codex/<feature>` branch.
3. Make small coherent commits. Push every commit before starting the next logical
   change so work is never trapped only in a local session.
4. Preserve unrelated work and avoid refactoring outside the feature boundary.
5. Add or update focused tests for changed behavior.
6. Run the focused tests plus the established regression gates that cover affected
   backend, frontend, storage, and workflow behavior.
7. Treat a failing or missing required gate as incomplete work, not as a successful
   feature.
8. Review the complete branch diff and record implementation, verification, known
   limitations, and rollback information in the feature checkpoint.
9. Push the final verified commit, open a pull request, and merge it into the
   repository's actual default branch. The current default branch is `master`; do
   not create a parallel `main` branch merely for naming consistency.
10. Verify the merged default-branch commit and required regression gates.
11. Delete the local and remote feature branch after a successful merge unless the
    branch has an explicit long-lived integration, release, or support purpose.

Default branch-retention policy: delete merged feature branches. Git history and the
pull request preserve reversibility without leaving stale branches. Keep a branch
only when its continuing purpose, owner, and retirement condition are documented.

## Current State

The current repository is a credible local segmentation and transcript-metrics
prototype. It already has:

- TXT/DOCX ingestion and pasted transcript support;
- deterministic transcript parsing and metrics;
- configurable JSON/YAML skill packs;
- optional OpenRouter-assisted skill-pack authoring;
- study workspaces, batch analysis, failure isolation, and basic casebook metadata;
- C-unit segmentation, specialist patch contracts, deterministic merge/evaluation,
  human-review flags, run history, and evidence exports;
- local JSON/CSV/SQLite artifacts;
- basic audit events, approved-library artifacts, and bundle hashes;
- agent-job prompts, statuses, runbooks, and evidence contracts.

It is not yet a qualitative research platform because it lacks the central manual
coding, memoing, retrieval, coder-comparison, and adjudication workflow.

## Gap Register

### Research Workflow

- [ ] Stable source, transcript-revision, passage, and C-unit identifiers.
- [ ] Manual source-span/C-unit coding and uncoding.
- [ ] Hierarchical, versioned codebooks with definitions, inclusion criteria,
      exclusion criteria, examples, notes, and colors.
- [ ] Typed participant/case/session/dyad/condition/timepoint attributes.
- [ ] Study-, source-, case-, code-, and excerpt-linked memos and annotations.
- [ ] Researcher identity on every coding and decision.
- [ ] Evidence search, filters, and saved coding queries.
- [ ] Evidence-linked case-by-theme, condition-by-theme, and time-by-theme matrices.
- [ ] Framework-cell summaries linked to supporting excerpts.
- [ ] Blind dual-coder assignment, percentage agreement, Cohen's kappa, and
      disagreement adjudication.
- [ ] Versioned codebook freeze/revision and affected-coding review.
- [ ] Portable codebook, coded-excerpt, reliability, matrix, audit, and project
      exports.
- [ ] Authorized representative-data validation and documented method limitations.

### Agent System

- [ ] Small provider boundary for deterministic, local-model, and OpenRouter modes.
- [ ] Server-enforced data-classification and egress policy.
- [ ] Durable job queue and dedicated worker.
- [ ] Leases, heartbeats, retries, cancellation, timeouts, progress, idempotency,
      and restart recovery.
- [ ] Structured agent proposals tied to exact source evidence.
- [ ] Accept, edit, reject, or defer review controls.
- [ ] No direct agent writes to accepted coding or final research conclusions.
- [ ] Provider, model digest, prompt version, input hash, latency, and reviewer
      provenance.
- [ ] Schema, quality, privacy, prompt-injection, and regression evaluations.
- [ ] GPU serialization and measured resource limits.
- [ ] Explicit provider errors with no silent cloud fallback.

### Production Platform

- [ ] SQLite schema migrations, referential integrity, and compatibility policy.
- [ ] Atomic artifact writes and transactional workflow boundaries.
- [ ] Project archive, deletion, retention, withdrawal, and recovery states.
- [ ] Real backup archives and restore verification, not manifest-only bundles.
- [ ] Windows identity for the loopback pilot and role-based authorization before
      any LAN access.
- [ ] Researcher, reviewer, and administrator roles.
- [ ] Device/storage encryption policy, restrictive file permissions, encrypted
      backups, and OS-managed secrets.
- [ ] Consent, data classification, purpose, retention, deletion, sharing, and
      export controls.
- [ ] Authenticated and integrity-protected audit trail.
- [ ] Upload size/type validation and resource limits.
- [ ] Structured privacy-safe logs, request/job IDs, readiness checks, and a local
      operations/status page.
- [ ] Pagination, background processing, search indexes, and measured project-size
      budgets.
- [ ] Keyboard, screen-reader, zoom, contrast, and reduced-motion accessibility.
- [ ] Locked dependencies, CI build/evaluation gates, signed Windows packaging,
      update/rollback, and offline update support.
- [ ] Sanitized support bundle and recovery runbooks.
- [ ] Named release/support ownership.

### Demo Assumptions To Remove

- [x] Remove the four-participant study cap.
- [x] Stop labeling arbitrary uploaded transcripts and merged outputs as synthetic.
- [x] Replace unconditional `No cloud I/O` messaging with the active privacy mode.
- [ ] Replace manually editable job statuses with enforced transition and evidence
      gates.
- [ ] Replace direct final-path writes with recoverable writes.
- [ ] Replace synthetic-only confidence claims with validated and calibrated limits.
- [x] Reconcile the README, old plans, checkpoint catalog, and current branch.

## Roadmap And Exit Gates

### Phase 0: Product Contract And Repository Truth

Deliver:

- approve the research-only product boundary;
- identify the first supported study workflow and data classifications;
- confirm single-user loopback as the initial topology;
- merge or supersede post-PR branch work intentionally;
- make README, roadmap, plans, checkpoints, UI claims, and current code agree;
- record the Alienware hardware and expected workload when available.

Exit gate: one approved product contract, one current roadmap, one truthful default
branch, and no contradictory demo claims.

### Phase 1: Canonical Evidence Model And Durable Storage

Deliver:

- project, source, transcript revision, passage/C-unit, case, attribute, codebook,
  code, coding reference, memo, annotation, coder, agent suggestion, reviewer
  decision, saved query, export, and audit entities;
- stable IDs and immutable source hashes;
- SQLite migrations and transactions;
- atomic artifact writes;
- project backup and restore.

Exit gate: a project can be imported, changed, closed, reopened, backed up, and
restored without losing data or changing evidence identifiers.

### Phase 2: Manual Qualitative Research Workbench

Deliver:

- codebook editor/importer;
- source selection and manual coding;
- apply/remove multiple codes and undo;
- coding stripes and source retrieval;
- memos and annotations;
- participant/case editor;
- search and transparent filters.

Exit gate: a psychology researcher can complete a small thematic-analysis workflow
without any agent.

### Phase 3: Research Rigor And Credible Dashboard

Deliver:

- evidence-linked matrices and saved queries;
- selectable and clearly defined denominators;
- two-coder calibration assignments;
- agreement/kappa results and disagreement adjudication;
- codebook freeze/version workflow;
- publication and reproducibility exports.

Exit gate: complete the credible acceptance demo below using synthetic or authorized
deidentified data.

### Phase 4: Bounded Agent Runtime

Deliver:

- provider and privacy boundary;
- durable worker and recoverable queue;
- structured source-linked proposals;
- human review workflow;
- provenance and evaluation;
- optional OpenRouter adapter for permitted tasks.

Exit gate: all agent work is source-linked, reviewable, reversible, policy-checked,
and unable to silently change accepted research data.

### Phase 5: Alienware Local-Model Appliance

Deliver:

- hardware benchmark corpus and acceptance thresholds;
- local-model runtime selected by measured quality, latency, VRAM, RAM, disk, and
  thermal behavior;
- serialized GPU work and asynchronous UI behavior;
- Windows installation/startup and local readiness checks;
- reboot and job-recovery behavior.

Exit gate: the full workflow runs without network access, survives reboot, meets
quality/resource thresholds, and requires no developer commands to launch.

### Phase 6: Controlled Pilot And Stable Release

Deliver:

- identity, authorization, privacy lifecycle, encryption, and complete audit;
- scheduled encrypted backups and restore drills;
- operational diagnostics and support bundle;
- accessibility and large-project verification;
- signed installer, update, migration, rollback, and offline release path;
- security, institutional, and REB review where applicable;
- support and incident runbooks.

Exit gate: a lab administrator can install, recover, update, and support the product,
and failures cannot silently lose or disclose research data.

### Phase 7: Optional Collaboration And Institutional Scale

Only after single-workstation demand is proven:

- asynchronous coder assignment and conflict-safe merge;
- LAN deployment with institutional identity;
- study-level permissions;
- multi-workstation synchronization;
- on-premise lab server if required.

Exit gate: concurrent researchers cannot overwrite one another, and every merge or
approval remains attributable and reversible.

## Credible Acceptance Demo

The first psychologist-credible demo is:

1. Import eight interview transcripts and a participant/session CSV.
2. Review transcript and C-unit segmentation warnings.
3. Build or import a 12-20-code psychology codebook with definitions and examples.
4. Have two coders independently code two calibration transcripts.
5. Calculate agreement/kappa and adjudicate disagreements.
6. Use human-reviewed agent suggestions on the remaining transcripts.
7. Compare themes across condition and week in an evidence-linked matrix.
8. Export the codebook, coded excerpts, reliability report, matrix, audit trail, and
   reproducibility bundle.

## Inputs And Autonomy Boundary

The implementation can progress without an API key through Phases 0-3 and most of
the Phase 4 infrastructure.

| Input from project owner | First needed | Why |
|---|---|---|
| Product confirmation that this is research support, not clinical decision support | Before calling Phase 0 complete | Changes the safety, regulatory, and validation boundary |
| First target psychology study method and desired codebook workflow | Before validating Phase 2 | Determines the researcher workflow and terminology |
| Authorized synthetic/deidentified transcripts, a representative codebook, and human coding examples | Before Phase 3 can be called domain-valid | Needed for realistic usability and method validation |
| Two-coder calibration rules and preferred agreement denominator | Before final Phase 3 reliability validation | Agreement results are method-dependent |
| OpenRouter API key | Only for the optional live OpenRouter portion of Phase 4 | Provider wiring and local/deterministic work do not require it |
| Alienware access and exact Windows/GPU/VRAM/RAM/disk information | Before Phase 5 benchmarking | Local model and packaging choices must be hardware-driven |
| Institutional identity provider details | Before LAN or multi-user Phase 6/7 | Avoids building throwaway password authentication |
| Privacy/REB/security requirements and approved backup location | Before accepting real sensitive data in Phase 6 | Defines data lifecycle and deployment controls |

When OpenRouter is enabled, the key must be configured locally and never pasted into
source code, Git, frontend code, generated artifacts, logs, or chat. Start with a
dedicated limited-spend key and synthetic/study-schema tasks. Transcript egress stays
denied by default.

## Explicitly Deferred

- clinical diagnosis or treatment recommendations;
- audio/video timeline editing and hosted transcription;
- social-media capture;
- PDF/image region coding;
- broad visualization galleries and word clouds;
- unrestricted general-purpose query language;
- real-time cloud collaboration;
- enterprise SSO before LAN access is needed;
- full NVivo/QDPX round-trip compatibility;
- distributed infrastructure;
- browser-triggered arbitrary code execution;
- automatic cloud fallback.

## Source-Of-Truth Order

When sources disagree, use this order:

1. this roadmap for product direction and phase gates;
2. current branch code for implemented behavior;
3. `backend/segmentation/rulebook.py` for current C-unit capability claims;
4. approved feature-specific design documents;
5. checkpoints for historical evidence;
6. the older roadmap and plans for historical intent only.

Update this document whenever a phase gate, product boundary, required owner input,
or major deferred item changes.
