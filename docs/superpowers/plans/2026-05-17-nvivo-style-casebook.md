# NVivo-Style Casebook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a study setup layer where batch transcript outputs carry participant, condition, week, and custom metadata for NVivo-like comparisons.

**Architecture:** Keep transcript analysis deterministic and local. Treat participant/condition/week as file-level casebook metadata attached to each batch transcript, then propagate that metadata into aggregate JSON/CSV rows and frontend aggregate tables.

**Tech Stack:** FastAPI, Pydantic, local JSON/CSV storage, React + TypeScript + Tailwind, pytest, npm build.

---

### Task 1: Backend Batch Metadata Propagation

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/storage/study_store.py`
- Test: `tests/test_study_workspaces.py`
- Test: `tests/test_api.py`

- [ ] Write failing tests showing `metadata` on each text batch item appears in run JSON, aggregate JSON, CSV headers, and API batch response.
- [ ] Run focused tests and confirm they fail because metadata is not accepted or returned.
- [ ] Add `metadata: dict[str, str]` to batch transcript request items.
- [ ] Normalize metadata values to trimmed strings and drop empty keys/values.
- [ ] Store metadata on per-run batch JSON records.
- [ ] Prefix aggregate metric rows with `participant_id`, `condition`, `week`, then custom metadata keys, followed by `source_filename`, `run_id`, and metric fields.
- [ ] Return aggregate metric results in `_study_batch_payload` so the frontend can render batch tables without reading local files directly.
- [ ] Run focused tests, then full backend tests.
- [ ] Commit and push.

### Task 2: Frontend Batch Assignment and Aggregate Tables

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Test: frontend lint/build

- [ ] Extend `StudyBatchResponse` and batch transcript API types with metadata and aggregate results.
- [ ] Update `parseBatchTranscripts` to support header syntax like `participant_001.txt | participant_id=P1 | condition=home | week=week_1`.
- [ ] Keep old batch blocks working with only a filename line.
- [ ] Add helper text showing the participant/condition/week header format.
- [ ] Render aggregate result tables below the batch summary.
- [ ] Run frontend lint/build.
- [ ] Commit and push.

### Task 3: Demo Fixtures and Checkpoint

**Files:**
- Modify: `demo_assets/healthcare_demo/README.md`
- Add: `checkpoints/2026-05-17-nvivo-style-casebook.md`

- [ ] Update the demo batch block to include participant, condition, and week metadata.
- [ ] Document the professor-facing explanation: casebook metadata enables participant x condition x week comparisons.
- [ ] Run backend tests and frontend build once more.
- [ ] Commit and push.

### Self-Review

- The plan keeps the first slice narrow: metadata propagation and visible aggregate tables.
- It does not add arbitrary custom coding, manual qualitative annotation, or full NVivo parity yet.
- The fixed first-class metadata fields are `participant_id`, `condition`, and `week`; custom metadata keys can travel alongside them.
