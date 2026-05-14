# V5 Local Orchestration and Review Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the app into an AI-native local research workflow where agents can plan, build, verify, and present changes through explicit review gates.

**Architecture:** V5 adds an orchestration layer around the existing deterministic runtime. Agent tasks are represented as local job artifacts with inputs, allowed actions, verification commands, outputs, and review status. The app never executes arbitrary code from the browser; it prepares auditable work packets for Codex/local automation.

**Tech Stack:** FastAPI, local job artifacts, Git metadata, React + Tailwind, pytest, Playwright, GitHub CLI when authenticated.

---

## Modules

### 1. Local Agent Job Model

- [ ] Add `agent_jobs` local artifact store.
- [ ] Define job types: `skill_pack_draft`, `metric_plugin_build`, `dashboard_compose`, `batch_analysis`.
- [ ] Track status: `queued`, `running`, `needs_review`, `approved`, `failed`.
- [ ] Store allowed files, verification commands, and output paths.

### 2. Review Gate System

- [ ] Add explicit review checklist per job.
- [ ] Require tests/build/UI smoke evidence before `approved`.
- [ ] Store command outputs or summaries locally.
- [ ] Show gate status in the UI.

### 3. Branch and PR Automation

- [ ] Generate branch names from job ids.
- [ ] Record git status before and after agent work.
- [ ] If `gh` is authenticated, create PRs for plugin/dashboard changes.
- [ ] If unavailable, keep local branch instructions.

### 4. Local LLM Provider Abstraction

- [ ] Keep OpenRouter as optional development provider.
- [ ] Add provider interface for local models.
- [ ] Support model capability metadata: JSON mode, context size, privacy mode.
- [ ] Keep transcript-content submission disabled by default.

### 5. Provenance Ledger

- [ ] Record skill pack id/version, plugin id/version, git commit, model provider, and verification evidence per output.
- [ ] Add endpoint to inspect provenance for a run.
- [ ] Include provenance in JSON export bundles.

### 6. Demo Target

- [ ] Create a metric plugin build job from a V3 request.
- [ ] Show review gates.
- [ ] Show branch/commit provenance.
- [ ] Approve the job and run the new metric in the workbench.
