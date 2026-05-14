# V3 Agentic Extension System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let researchers request new metrics that become tested reusable plugins without breaking the local workbench.

**Architecture:** V3 keeps runtime analysis deterministic. The app captures plugin requests as local artifacts, Codex converts approved requests into metric plugins through isolated branches/worktrees, and the workbench discovers approved plugins through the metric registry.

**Tech Stack:** FastAPI, Python dataclasses, SQLite/local JSON artifacts, React + Tailwind, pytest, Playwright UI smoke.

---

## Modules

### 1. Plugin Request Artifacts

- [ ] Create `backend/extensions/plugin_requests.py`.
- [ ] Define a `PluginRequest` schema with id, title, research question, proposed metric id, output columns, synthetic examples, status, and created timestamp.
- [ ] Persist requests under `local_data/plugin_requests/*.json`.
- [ ] Add `POST /api/plugin-requests` and `GET /api/plugin-requests`.
- [ ] Add frontend request form inside the plugin registry panel.

### 2. Request-to-Plan Generator

- [ ] Add a deterministic request summary file under `local_data/plugin_requests/<id>/implementation_prompt.md`.
- [ ] Include metric contract, examples, expected rows, and verification commands.
- [ ] Exclude real transcript content by default.
- [ ] Add a Codex internal skill for converting request artifacts into plugin branches.

### 3. Plugin Worktree Workflow

- [ ] Add CLI documentation for `git worktree add ../nlp_skill_agents-<request-id> -b codex/<request-id>`.
- [ ] Add branch naming convention `codex/plugin-<slug>`.
- [ ] Add a pre-merge checklist: focused tests, full tests, frontend build, UI smoke, checkpoint update.
- [ ] Record branch/commit provenance in the plugin request artifact after merge.

### 4. Plugin Review UI

- [ ] Add local request status states: `draft`, `ready_for_build`, `implemented`, `approved`, `rejected`.
- [ ] Show request history in the UI.
- [ ] Show generated plugin id and commit hash after implementation.
- [ ] Keep approval manual for V3.

### 5. Demo Target

- [ ] Create a request for an "empathy response" or "repair sequence" metric using synthetic examples.
- [ ] Build it as a metric plugin in a branch.
- [ ] Run it in the workbench with CSV output.
- [ ] Show plugin request, generated metric, tests, and UI results in one walkthrough.
