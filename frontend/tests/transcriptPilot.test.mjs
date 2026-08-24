import assert from "node:assert/strict";
import test from "node:test";

import {
  canCommitPilotJob,
  normalizePilotJob,
  normalizePilotStudy,
  pilotReviewCounts
} from "../../local_data/tmp/frontend-tests/transcriptPilot.js";

const specialists = [
  "speaker_turn",
  "timing_pause",
  "repair_overlap",
  "redaction_nonverbal"
];

function backendJob() {
  return {
    job_id: "tpj_00000000000000000000000000000000",
    source_id: "psrc_00000000000000000000000000000000",
    study_id: "pilot-study",
    researcher_id: "researcher_01",
    input_revision_id: "trv_00000000000000000000000000000000",
    status: "needs_review",
    chunk_count: 1,
    progress: {
      planned_call_count: 4,
      attempted_call_count: 4,
      completed_call_count: 4,
      valid_call_count: 4,
      completed_chunk_count: 1
    },
    usage: {
      accounting_complete: true,
      total_cost_usd: "0.0004",
      total_tokens: 120
    },
    provenance: {
      model: "openai/gpt-5.6-luna",
      provider: "Azure",
      endpoint: "azure/eu"
    },
    source: {
      active_revision_id: "trv_00000000000000000000000000000000"
    },
    chunks: [
      {
        chunk_index: 0,
        calls: specialists.map((specialist_id) => ({
          specialist_id,
          status: "valid"
        }))
      }
    ],
    proposals: [
      {
        proposal_id: "tpp_00000000000000000000000000000000",
        line_index: 0,
        original_text: "unchanged",
        proposed_text: "unchanged",
        changed: false,
        decision_version: 0
      },
      {
        proposal_id: "tpp_11111111111111111111111111111111",
        line_index: 1,
        original_text: "before",
        proposed_text: "after",
        changed: true,
        decision_version: 1,
        decision: {
          action: "accept",
          edited_text: ""
        }
      }
    ]
  };
}

test("normalizes the backend review, receipt, and specialist wire contract", () => {
  const job = normalizePilotJob(backendJob());

  assert.equal(job.status, "review_ready");
  assert.equal(job.receipt?.expected_call_count, 4);
  assert.equal(job.receipt?.valid_result_count, 4);
  assert.equal(job.receipt?.accounting_complete, true);
  assert.equal(job.specialists?.length, 4);
  assert.ok(job.specialists?.every((specialist) => specialist.status === "valid"));
  assert.equal(canCommitPilotJob(job), true);
});

test("does not require a researcher decision for unchanged lines", () => {
  const counts = pilotReviewCounts(normalizePilotJob(backendJob()));

  assert.deepEqual(counts, {
    total: 2,
    unchanged: 1,
    unresolved: 0,
    accepted: 1,
    kept: 0,
    edited: 0
  });
});

test("preserves the nested researcher returned by study creation", () => {
  const study = normalizePilotStudy({
    study_id: "pilot-study",
    name: "Pilot",
    researcher: {
      researcher_id: "researcher_01",
      display_name: "Researcher One",
      active: true
    }
  });

  assert.equal(study.researcher_id, "researcher_01");
  assert.equal(study.researcher_name, "Researcher One");
});

test("blocks commit when strict accounting is incomplete", () => {
  const raw = backendJob();
  raw.usage.accounting_complete = false;
  assert.equal(canCommitPilotJob(normalizePilotJob(raw)), false);
});

test("blocks commit when every reviewed line keeps the original", () => {
  const raw = backendJob();
  raw.proposals[1].decision.action = "keep_original";
  assert.equal(canCommitPilotJob(normalizePilotJob(raw)), false);
});

test("blocks commit when an edit normalizes to the original line", () => {
  const raw = backendJob();
  raw.proposals[1].decision.action = "edit";
  raw.proposals[1].decision.edited_text = "  before  ";
  assert.equal(canCommitPilotJob(normalizePilotJob(raw)), false);
});
