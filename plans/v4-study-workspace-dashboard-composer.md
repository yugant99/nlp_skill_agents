# V4 Study Workspace and Dashboard Composer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move from single-run analysis to a study workspace that can compare runs, manage skill versions, and emit dashboard-ready bundles.

**Architecture:** V4 introduces study-level local state. Skill packs, runs, plugin requests, exports, and dashboard schemas are grouped into a study workspace under `local_data/studies/<study_id>`, while the existing single-transcript flow remains a fast path.

**Tech Stack:** FastAPI, local JSON/SQLite metadata, React + Tailwind, CSV/JSON bundle exports, pytest, Playwright.

---

## Modules

### 1. Study Workspace Model

- [ ] Add study metadata: id, title, description, default skill pack, created timestamp.
- [ ] Add `POST /api/studies`, `GET /api/studies`, and `GET /api/studies/{study_id}`.
- [ ] Store workspace metadata under `local_data/studies`.
- [ ] Keep runs compatible with the current single-run API.

### 2. Skill Pack Versioning

- [ ] Save every drafted/refined skill pack as a versioned local artifact.
- [ ] Add version labels and parent version ids.
- [ ] Add compare endpoint for two versions.
- [ ] Show role, metric, concept, cue, and disfluency diffs in the UI.

### 3. Multi-Transcript Batch Runs

- [ ] Add batch upload endpoint accepting multiple TXT/DOCX files.
- [ ] Execute each file through the same deterministic pipeline.
- [ ] Persist per-file outputs and aggregate outputs.
- [ ] Add failure isolation so one bad file does not kill the batch.

### 4. Aggregate Tables

- [ ] Add aggregate reducers for each metric result.
- [ ] Output cross-transcript CSVs.
- [ ] Show per-file drilldown plus aggregate summary cards.
- [ ] Record skill-pack version used for each aggregate.

### 5. Dashboard Composer Bundle

- [ ] Emit `dashboard_schema.json` with metrics, columns, file paths, chart candidates, and labels.
- [ ] Add `dashboard_data/*.csv` export folder.
- [ ] Add internal Codex dashboard composition skill that consumes the schema bundle.
- [ ] Keep generated dashboards outside committed source unless explicitly promoted.

### 6. Demo Target

- [ ] Create one study workspace.
- [ ] Draft two skill-pack versions.
- [ ] Run three synthetic transcripts.
- [ ] Show aggregate outputs and a dashboard schema bundle.
