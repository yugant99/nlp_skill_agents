# NLP Skill Agents Roadmap

This roadmap keeps the project dynamic-first. The core product direction is not "add one hardcoded metric at a time"; it is "let researchers define study-specific analysis skills, then run those skills through local deterministic tools, with agent assistance where it is safe."

## V1: Dynamic Config Skills

**Goal:** Demo a local-first transcript workbench where researchers can change study definitions without code changes.

V1 should support one transcript per run and make the flexibility obvious in the demo: the same transcript can produce different outputs when run with different study skill packs.

### Product Capabilities

- Upload or paste one transcript locally.
- Select or upload a study skill pack as JSON/YAML.
- Define speaker roles through the skill pack.
- Define nonverbal cue categories through the skill pack.
- Define disfluency tokens through the skill pack.
- Define healthcare, psychology, or linguistics concept lexicons through the skill pack.
- Run deterministic local metrics from those dynamic definitions.
- Save local JSON/CSV outputs with provenance.
- Show professor-friendly result tables and summary cards in the UI.
- Provide built-in templates for caregiver-participant, interview, and therapy/open conversation studies.

### Technical Capabilities

- Skill-pack schema and validation.
- Skill-pack upload endpoint.
- Dynamic concept-count metric.
- Dynamic cue-inventory metric.
- Dynamic disfluency configuration.
- Output bundle that includes transcript metadata, selected skill pack, metric results, export paths, and validation warnings.
- UI skill-pack selector/uploader/editor.
- Internal Codex skill for research skill authoring.

### Demo Bar

- Run the same transcript with two different skill packs.
- Show that redefining "nonverbal", "healthcare topic", or "disfluency" changes the outputs without changing application code.
- Verify every feature through CLI tests wherever possible and through the UI before calling it done.

## V2: Agent-Assisted Skill Authoring

**Goal:** Make study setup AI-native while keeping execution safe, local, and reviewable.

V2 introduces an assistant that helps researchers create and refine skill packs, but the analysis runtime remains schema-based and deterministic.

### Product Capabilities

- Researcher describes a study in natural language.
- Agent drafts a skill pack from that description.
- Researcher reviews roles, concepts, cue definitions, disfluencies, and outputs before running.
- Researcher asks for refinements such as "split pain into acute and chronic" or "ignore backchannel sounds".
- Skill packs are versioned per study.
- Multi-file batch analysis.
- Cross-transcript aggregate tables.
- Compare two skill-pack versions on the same transcript.
- Generate dashboard-ready schema bundles for a dashboard-maker agent.

### Technical Capabilities

- Schema-aware skill-pack builder.
- Skill-pack repair and validation loop.
- Skill-pack version metadata.
- Batch run storage and aggregate exports.
- Local model path for private deployments when available.
- Internal Codex skill for dashboard composition.

### Demo Bar

- Start from a natural-language study description.
- Generate a valid skill pack.
- Review and edit it.
- Run it on multiple transcripts.
- Show aggregate outputs and per-file drilldown.

## V3: Agentic Extension System

**Goal:** Let the system create new analysis capabilities when config is not enough, with strong guardrails.

V3 is where the agent may modify code, but only through an isolated developer workflow with tests, review, and approval.

### Product Capabilities

- Researcher requests a new analysis that cannot be expressed as a skill-pack config.
- Developer agent creates a branch/worktree.
- Agent writes or modifies a metric plugin.
- Agent generates tests from researcher examples.
- Human reviews the generated metric before it becomes available.
- Approved metric appears as a reusable study capability.
- Institution-level library of reusable study skill packs and metric plugins.

### Technical Capabilities

- Metric plugin interface.
- Plugin input/output schema validation.
- Sandbox execution for local plugins.
- Agent-generated branches and pull requests.
- Provenance tracking for agent, code version, skill-pack version, and outputs.
- Automated CLI and UI verification gates before merge.
- Internal Codex skill for metric plugin authoring.

### Demo Bar

- Request a new metric from examples.
- Agent creates a tested plugin branch.
- Review and approve it.
- Run the new metric inside the workbench without destabilizing existing skills.

## V4: Study Workspace and Dashboard Composer

**Goal:** Move from one-off transcript runs to reusable study workspaces with batch analysis, versioned skill packs, aggregate outputs, and dashboard-ready bundles.

V4 keeps the same safe flow but adds study-level structure: a professor can create a study, draft or upload skill packs, run multiple transcripts, compare versions, and export data for dashboard composition.

### Product Capabilities

- Create a local study workspace.
- Save every drafted/refined skill pack as a versioned study artifact.
- Run multiple transcripts under one study.
- Produce per-file and aggregate metric tables.
- Compare two skill-pack versions on the same transcript or batch.
- Export a dashboard schema bundle with JSON metadata and CSV data.

### Technical Capabilities

- `local_data/studies/<study_id>` workspace layout.
- Study metadata endpoints.
- Skill-pack version store and diff engine.
- Batch run executor with per-file failure isolation.
- Aggregate reducers for metric outputs.
- Dashboard schema exporter.

### Demo Bar

- Create one study workspace.
- Run three synthetic transcripts.
- Show aggregate tables and a dashboard schema bundle.
- Show that changing a skill-pack version changes downstream outputs.

## V5: Local Orchestration and Review Gates

**Goal:** Make the workflow agent-native without giving up reviewability.

V5 introduces local agent job artifacts for skill drafting, plugin building, dashboard composition, and batch analysis. The app prepares auditable work packets; Codex or a local automation runner executes them through explicit verification gates.

### Product Capabilities

- Convert a plugin request into a local build job.
- Track job status and review gates.
- Store verification evidence for tests, builds, and UI smoke checks.
- Record branch, commit, and model-provider provenance.
- Approve or reject agent-produced work before reuse.

### Technical Capabilities

- Local `agent_jobs` artifact store.
- Job schemas for skill packs, plugins, dashboards, and batch runs.
- Verification gate schema.
- Git branch/commit provenance capture.
- Optional GitHub PR creation when `gh` is authenticated.
- Provider abstraction for OpenRouter now and local LLMs later.

### Demo Bar

- Create a metric-plugin build job from a V3 request.
- Show review gates and verification evidence.
- Show generated branch/commit provenance.
- Approve the job and run the new plugin.

## V6: Institutional Research Platform

**Goal:** Prepare the system for lab and institutional use with reusable libraries, reproducibility, audit trails, deployment profiles, and backup/restore.

V6 keeps transcript analysis local-first, but adds operational structure for professors, labs, and secure environments.

### Product Capabilities

- Institution-level library of approved skill packs and metric plugins.
- Import/export reusable research capability bundles.
- Generate reproducibility reports for study outputs.
- Track approvals and exports through an audit log.
- Run in dev, lab-local, or secure-offline profiles.
- Backup and restore local research workspaces.

### Technical Capabilities

- Library bundle manifest format.
- Run report generator with input hashes and provenance.
- Local role model for researcher/reviewer/admin.
- Audit event store.
- Deployment profile checks.
- Synthetic benchmark corpus and release validation harness.

### Demo Bar

- Export a complete study bundle.
- Import it into a clean local profile.
- Re-run and verify matching outputs.
- Show audit report and approved library entries.

## Working Rule

Every feature added from this roadmap must follow the same completion bar:

- Tests first or test plan first.
- CLI verification wherever possible.
- UI verification for every user-facing change.
- Local outputs only.
- Commit and push after each stable feature slice.
- Keep `master` demoable.
