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

## Working Rule

Every feature added from this roadmap must follow the same completion bar:

- Tests first or test plan first.
- CLI verification wherever possible.
- UI verification for every user-facing change.
- Local outputs only.
- Commit and push after each stable feature slice.
- Keep `master` demoable.
