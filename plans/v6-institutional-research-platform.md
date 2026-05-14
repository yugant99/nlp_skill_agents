# V6 Institutional Research Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare the system for lab/institution use: reusable libraries, auditability, access control boundaries, packaging, and reproducible research outputs.

**Architecture:** V6 promotes the local workbench into a deployable research platform. It keeps analysis local-first but adds library management, user/workspace boundaries, reproducibility reports, and deployment modes for professor/lab environments.

**Tech Stack:** FastAPI, SQLite with migration path, React + Tailwind, local filesystem artifact store, export bundles, optional container packaging.

---

## Modules

### 1. Reusable Library System

- [ ] Add institution-level library folder for approved skill packs and metric plugins.
- [ ] Add import/export of library bundles.
- [ ] Add compatibility metadata for plugins.
- [ ] Add deprecation and replacement metadata.

### 2. Audit and Reproducibility Reports

- [ ] Generate run reproducibility reports as JSON and PDF/HTML.
- [ ] Include input filenames, hashes, skill versions, plugin versions, git commit, and environment metadata.
- [ ] Add report download endpoint.
- [ ] Keep PHI/transcript text out of reports by default.

### 3. Access Boundary Model

- [ ] Add local user roles: researcher, reviewer, admin.
- [ ] Gate approval actions by role in local configuration.
- [ ] Keep simple local auth for demo; design for institutional SSO later.
- [ ] Add audit events for approval/rejection and exports.

### 4. Deployment Profiles

- [ ] Define `dev`, `lab-local`, and `secure-offline` profiles.
- [ ] Add startup checks for env, local storage, model provider, and browser UI.
- [ ] Add backup/export command for local data.
- [ ] Add restore/import command.

### 5. Validation Harness

- [ ] Add synthetic benchmark corpus.
- [ ] Add regression suite for every approved skill/plugin.
- [ ] Add UI smoke suite for core workflows.
- [ ] Add release checklist.

### 6. Demo Target

- [ ] Export one complete study as a reproducible bundle.
- [ ] Import it into a clean local profile.
- [ ] Re-run analysis and verify matching outputs.
- [ ] Show audit report and approved library entries.
