# Rule-Specialist Segmentation Pipeline Design

Date: 2026-05-26
Status: Draft for user review

## Goal

Build the next C-unit segmentation slice as a rule-specialist agent pipeline.
The user gives one Descript-like transcript input and receives one verified
segmented transcript output, with evidence that shows which rules passed,
which rules failed, and which specialist should repair each failure.

The first implementation remains synthetic-only. Official reference materials
may inform abstract rule definitions, but they must not appear in prompts,
fixtures, generated examples, expected outputs, or tests.

## Approved Architecture Decision

Use rule-specialist agents plus a merge/verifier flow.

Each specialist owns a patch, not the whole transcript. The merge agent is the
only component allowed to assemble the final transcript. This keeps specialists
from overwriting each other and makes failures traceable. For example, a
`pause-markers` failure routes back to the timing/pause specialist, not to the
whole system.

```text
Descript input
  -> Source Parser
  -> Synthetic Case Builder
  -> Rule Planner
       -> Speaker/turn specialist
       -> Timing/pause specialist
       -> Repair/overlap specialist
       -> Redaction/nonverbal specialist
  -> Merge Agent
  -> Deterministic Evaluator
  -> Verification Agent
       -> pass: final transcript + evidence bundle
       -> fail: targeted rewrite job for failed rule IDs
```

## Components

### Segmentation Run Store

Add a local store under `local_data/segmentation_runs/`.

Each run stores:

- input transcript text and source filename
- parsed Descript events
- synthetic case metadata
- rule plan
- specialist patch outputs
- merged draft
- deterministic evaluator result
- verification status
- evidence packet paths

Suggested status values:

- `created`
- `planned`
- `patched`
- `merged`
- `verified`
- `needs_rewrite`
- `failed`

### Source Parser

Reuse `backend/segmentation/descript.py`.

The parser remains narrow for v1:

- accepts timestamped speaker turns like `[00:00:03] P: text`
- normalizes whitespace
- ignores malformed lines instead of inventing content

Malformed or empty inputs should produce visible run errors, not silent empty
gold targets.

### Synthetic Case Builder

Transforms parsed events into a synthetic case envelope.

The first slice should not attempt full natural-language synthesis from any
official data. It should build from user-provided Descript-like input and attach
rule coverage metadata.

This component should preserve:

- `case_id`
- `title`
- `descript_text`
- `rule_ids`
- `official_source_guard_tokens`
- `forbidden_source_tokens`

### Rule Planner

Maps rule IDs to specialist work packets.

Initial specialist groups:

- speaker/turn specialist: `speaker-markers`
- timing/pause specialist: `timestamp-markers`, `pause-markers`
- repair/overlap specialist: `filled-pauses`, `overlap-markers`, `abandoned-utterance`
- redaction/nonverbal specialist: `redaction-comments`, `omission-markers`, `communicative-nonverbal`

The planner emits patch contracts, not full transcript rewrite prompts.

### Specialist Patch Output

Each specialist returns a structured patch:

```json
{
  "specialist_id": "timing_pause",
  "rule_ids": ["timestamp-markers", "pause-markers"],
  "patches": [
    {
      "operation": "insert_before_event",
      "event_index": 0,
      "text": "-0:00",
      "reason": "Start timestamp marker required"
    }
  ],
  "evidence": {
    "expected_effect": "Adds one time marker",
    "source_event_indexes": [0]
  }
}
```

Patch operations should be intentionally small. The merge agent applies them in
a deterministic order and records conflicts.

### Merge Agent

The merge agent owns final assembly.

Responsibilities:

- apply specialist patches in a stable order
- detect conflicting patches
- preserve source-event order
- produce one candidate segmented transcript
- write merge evidence

The merge agent should not invent new rule fixes. If a specialist missed a
rule, the evaluator should catch it and route the failure back to the matching
specialist.

### Deterministic Evaluator

Reuse `backend/segmentation/evaluator.py`.

The evaluator is the gate. A model output is not accepted because it looks
right. It is accepted only when deterministic checks pass.

Near-term evaluator gaps to close:

- distinguish required rule failures from warning-level quality notes
- include failure-to-specialist routing metadata
- add per-rule evidence fields that the UI can render directly

### Verification Agent

The verification agent interprets evaluator failures and creates the next
targeted rewrite job.

It does not override the evaluator. It translates failures into repair work:

```text
evaluator failure: pause-markers
  -> target specialist: timing_pause
  -> repair prompt: add semicolon pause markers where elapsed gaps require them
  -> verifier gate: rerun evaluator with same rule IDs
```

## API Shape

Add backend routes:

```text
POST /api/segmentation/runs
GET  /api/segmentation/runs/{run_id}
POST /api/segmentation/runs/{run_id}/verify
POST /api/segmentation/runs/{run_id}/rewrite-job
```

`POST /api/segmentation/runs` accepts:

```json
{
  "source_filename": "session_001_descript.txt",
  "descript_text": "[00:00:00] P: Good morning...",
  "rule_ids": ["speaker-markers", "timestamp-markers", "pause-markers"]
}
```

The response returns the run payload, including current status and next action.

## Frontend Shape

Keep UI changes for the second implementation slice.

The eventual panel should show:

- source Descript transcript
- rule plan
- specialist patch cards
- merged draft
- evaluator score and failures
- rewrite job status
- final transcript export

## Data Flow

```text
INPUT
  |
  v
validate non-empty Descript text
  |
  v
parse timestamped speaker events
  |
  v
build segmentation run record
  |
  v
plan rule-specialist work packets
  |
  v
collect specialist patches
  |
  v
merge patches into one draft
  |
  v
evaluate draft deterministically
  |
  +-- pass --> mark verified, expose final transcript
  |
  +-- fail --> map failed rule IDs to specialists, queue rewrite job
```

## Error Handling

Expected v1 error cases:

- empty `descript_text`: return `400`, store no run
- no parsed events: return `400` with parser diagnostic
- unknown rule ID: return `400` with unsupported rule list
- patch conflict: mark run `failed` or `needs_rewrite` with conflict evidence
- evaluator failure: mark run `needs_rewrite`, not `failed`
- official-source guard hit: mark run `failed` unless explicitly running a
  synthetic leakage test

No catch-all error handling should swallow these states. Every failure should be
visible in the run payload.

## Testing Plan

Backend tests first:

- run creation rejects empty Descript input
- run creation rejects unknown rules
- valid Descript input creates parsed events and a rule plan
- specialist planner maps every known rule to one specialist
- merge agent applies patches in stable order
- merge agent records conflicts instead of silently overwriting
- evaluator pass marks run `verified`
- evaluator failure marks run `needs_rewrite`
- official-source guard marks run as blocked/failed
- rewrite-job endpoint creates a targeted `segmentation_rewrite` job with failed rule IDs

Focused commands:

```bash
.venv/bin/pytest tests/test_segmentation_core.py -q
.venv/bin/pytest tests/test_api.py -k segmentation -q
.venv/bin/pytest tests/test_agent_jobs.py -k segmentation -q
```

Then run:

```bash
.venv/bin/pytest -q
cd frontend && npm run lint && npm run build
```

## Implementation Slices

### Slice 1: Backend Run Model And Store

Add data models and `SegmentationRunStore`. Cover parser, validation, and
local persistence with tests.

### Slice 2: Planner, Patch Contracts, And Merge

Add rule-specialist planning and deterministic merge logic. Specialists can be
stubbed as deterministic patch producers in this slice.

### Slice 3: Verification And Rewrite Routing

Connect merged draft evaluation to status transitions and rewrite-job creation.
Extend agent-job artifacts to include failed rule IDs and target specialist IDs.

### Slice 4: UI Run Timeline

Add a segmentation run panel that shows source input, rule plan, patches,
merged draft, evaluator failures, and rewrite status.

## Not In Scope For First Build

- real official transcript fixtures
- sending transcript content to a network LLM by default
- full audio ASR
- full NVivo-style manual coding
- unrestricted autonomous code execution from the browser
- specialist agents rewriting the full transcript independently

## V1 Decisions

V1 accepts both pasted text and uploaded `.txt` files. Pasted text keeps the
API easy to test, and file upload keeps the demo close to the Descript export
workflow.

Official-source guard failures are terminal failures in v1. A run that leaks
forbidden source tokens should not enter a repair loop because the safest
behavior is to stop and show the leakage evidence.

The first specialist outputs are deterministic patch stubs plus agent-job
artifacts. The deterministic stubs make the run pipeline testable immediately,
and the artifacts preserve the path to real autonomous specialist execution.
