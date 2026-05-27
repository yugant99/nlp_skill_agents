# Rule-Specialist Segmentation Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a backend-first rule-specialist segmentation pipeline where one Descript-like transcript becomes one merged, evaluator-gated segmented draft with synthetic test data and targeted rewrite routing.

**Architecture:** Add focused segmentation modules for synthetic corpus generation, run storage, rule planning, patch merge, and verification. Specialists emit structured patches only; the merge layer assembles the transcript and the deterministic evaluator decides pass/fail. Keep UI changes out of this slice except API compatibility.

**Tech Stack:** Python dataclasses, FastAPI, local JSON artifacts under `local_data/`, pytest, existing segmentation evaluator and agent-job store.

---

## File Structure

- Create `backend/segmentation/corpus.py`: seeded synthetic test corpus generation and rule coverage matrix.
- Create `backend/segmentation/pipeline.py`: run model, rule planner, deterministic specialist patch stubs, merge logic, verification routing, and local run store.
- Modify `backend/extensions/agent_jobs.py`: allow segmentation rewrite jobs to carry failed rule IDs and target specialist IDs in generated prompt/runbook text.
- Modify `backend/app/main.py`: add segmentation run request models and routes.
- Modify `tests/test_segmentation_core.py`: add TDD coverage for corpus, planner, merge, verification, and local persistence.
- Modify `tests/test_api.py`: add API coverage for run creation, verify, and rewrite routing.
- Modify `tests/test_agent_jobs.py`: add agent-job prompt/runbook coverage for failed rule routing.

## Task 1: Synthetic Corpus Generator

**Files:**
- Create: `backend/segmentation/corpus.py`
- Test: `tests/test_segmentation_core.py`

- [ ] **Step 1: Write failing corpus tests**

```python
def test_synthetic_corpus_generates_stable_rule_cases_without_source_leakage() -> None:
    from backend.segmentation.corpus import generate_synthetic_corpus

    first = generate_synthetic_corpus(seed=7)
    second = generate_synthetic_corpus(seed=7)

    assert [case.case_id for case in first] == [case.case_id for case in second]
    assert {rule for case in first for rule in case.rule_ids} >= {
        "speaker-markers",
        "timestamp-markers",
        "pause-markers",
        "filled-pauses",
        "overlap-markers",
        "abandoned-utterance",
        "redaction-comments",
        "omission-markers",
        "communicative-nonverbal",
    }
    safe_cases = [case for case in first if "official-source-guard" not in case.rule_ids]
    for case in safe_cases:
        combined = f"{case.descript_text}\n{case.gold_text}".lower()
        for token in case.official_source_guard_tokens:
            assert token.lower() not in combined
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/test_segmentation_core.py::test_synthetic_corpus_generates_stable_rule_cases_without_source_leakage -q`

Expected: FAIL with `ModuleNotFoundError` or missing `generate_synthetic_corpus`.

- [ ] **Step 3: Implement minimal corpus module**

Create `generate_synthetic_corpus(seed: int = 0)` returning deterministic `SyntheticSegmentationCase` objects for specialist groups and one explicit leakage-negative case.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/test_segmentation_core.py::test_synthetic_corpus_generates_stable_rule_cases_without_source_leakage -q`

Expected: PASS.

## Task 2: Run Store, Planner, Patch Merge, And Verification

**Files:**
- Create: `backend/segmentation/pipeline.py`
- Test: `tests/test_segmentation_core.py`

- [ ] **Step 1: Write failing pipeline tests**

```python
def test_rule_specialist_pipeline_plans_patches_merges_and_verifies(tmp_path: Path) -> None:
    from backend.segmentation.pipeline import SegmentationRunStore

    store = SegmentationRunStore(tmp_path)
    run = store.create_run(
        source_filename="session.txt",
        descript_text="[00:00:00] P: Good morning.\n[00:00:03] Av: Uh yes.",
        rule_ids=["speaker-markers", "timestamp-markers", "pause-markers", "filled-pauses"],
    )

    assert run.status == "needs_rewrite"
    assert [packet.specialist_id for packet in run.rule_plan] == [
        "speaker_turn",
        "timing_pause",
        "repair_overlap",
    ]
    assert run.merged_draft
    assert run.evaluation is not None
    assert all(output.patches for output in run.specialist_outputs)
    assert (tmp_path / "segmentation_runs" / f"{run.run_id}.json").exists()

    loaded = store.load_run(run.run_id)

    assert loaded.run_id == run.run_id
    assert loaded.status == run.status
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/test_segmentation_core.py::test_rule_specialist_pipeline_plans_patches_merges_and_verifies -q`

Expected: FAIL with missing `backend.segmentation.pipeline`.

- [ ] **Step 3: Implement minimal pipeline**

Add dataclasses for rule packets, patch operations, specialist outputs, merge evidence, and run records. Add `SegmentationRunStore.create_run/load_run/verify_run`, planner mapping, deterministic patch stubs, merge ordering, evaluator call, and JSON persistence.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/test_segmentation_core.py::test_rule_specialist_pipeline_plans_patches_merges_and_verifies -q`

Expected: PASS.

## Task 3: API Routes

**Files:**
- Modify: `backend/app/main.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing API tests**

```python
def test_segmentation_run_api_creates_and_verifies_rule_specialist_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/segmentation/runs",
        json={
            "source_filename": "session.txt",
            "descript_text": "[00:00:00] P: Good morning.\n[00:00:03] Av: Uh yes.",
            "rule_ids": ["speaker-markers", "timestamp-markers", "pause-markers", "filled-pauses"],
        },
    )

    assert response.status_code == 200
    run = response.json()["run"]
    assert run["source"] == "synthetic"
    assert run["status"] in {"verified", "needs_rewrite"}
    assert run["rule_plan"][0]["specialist_id"] == "speaker_turn"

    verify_response = client.post(f"/api/segmentation/runs/{run['run_id']}/verify")

    assert verify_response.status_code == 200
    assert verify_response.json()["run"]["run_id"] == run["run_id"]
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/test_api.py::test_segmentation_run_api_creates_and_verifies_rule_specialist_run -q`

Expected: FAIL with `404 Not Found`.

- [ ] **Step 3: Add routes and payload helpers**

Add `SegmentationRunCreateRequest`, `POST /api/segmentation/runs`, `GET /api/segmentation/runs/{run_id}`, and `POST /api/segmentation/runs/{run_id}/verify`.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/test_api.py::test_segmentation_run_api_creates_and_verifies_rule_specialist_run -q`

Expected: PASS.

## Task 4: Targeted Rewrite Routing

**Files:**
- Modify: `backend/extensions/agent_jobs.py`
- Modify: `backend/app/main.py`
- Test: `tests/test_agent_jobs.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing rewrite tests**

```python
def test_segmentation_rewrite_job_includes_failed_rules_and_specialists(tmp_path: Path) -> None:
    job = create_segmentation_rewrite_job(
        "run_123",
        failed_rule_ids=["pause-markers"],
        target_specialist_ids=["timing_pause"],
        store=AgentJobStore(tmp_path),
    )

    prompt = (tmp_path / "agent_jobs" / job.id / "rewrite_prompt.html").read_text()
    runbook = (tmp_path / "agent_jobs" / job.id / "runbook.html").read_text()

    assert "pause-markers" in prompt
    assert "timing_pause" in prompt
    assert "targeted segmentation rewrite" in runbook
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/test_agent_jobs.py::test_segmentation_rewrite_job_includes_failed_rules_and_specialists -q`

Expected: FAIL with unexpected keyword argument.

- [ ] **Step 3: Implement targeted rewrite metadata**

Extend `create_segmentation_rewrite_job` and prompt/runbook HTML builders to include failed rule IDs and specialist IDs.

- [ ] **Step 4: Add API rewrite-run route**

Add `POST /api/segmentation/runs/{run_id}/rewrite-job`, loading failed rule routing from the stored run and passing it into the agent job factory.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
.venv/bin/pytest tests/test_agent_jobs.py -k segmentation -q
.venv/bin/pytest tests/test_api.py -k segmentation -q
```

Expected: PASS.

## Task 5: Full Verification And Commit

**Files:**
- All files above

- [ ] **Step 1: Run focused segmentation tests**

```bash
.venv/bin/pytest tests/test_segmentation_core.py -q
.venv/bin/pytest tests/test_api.py -k segmentation -q
.venv/bin/pytest tests/test_agent_jobs.py -k segmentation -q
```

- [ ] **Step 2: Run full backend suite**

```bash
.venv/bin/pytest -q
```

- [ ] **Step 3: Run frontend compatibility checks**

```bash
cd frontend && npm run lint && npm run build
```

- [ ] **Step 4: Review diff**

```bash
git status --short
git diff --stat
```

- [ ] **Step 5: Commit**

```bash
git add backend/segmentation/corpus.py backend/segmentation/pipeline.py backend/extensions/agent_jobs.py backend/app/main.py tests/test_segmentation_core.py tests/test_api.py tests/test_agent_jobs.py docs/superpowers/plans/2026-05-26-rule-specialist-segmentation-pipeline.md
git commit -m "feat: add rule-specialist segmentation pipeline"
```
