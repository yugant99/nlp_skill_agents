import assert from "node:assert/strict";
import test from "node:test";

import {
  canAcceptProfessorDemoRun,
  formatProfessorDemoCost
} from "../../local_data/tmp/frontend-tests/professorDemo.js";

const specialistIds = [
  "speaker_turn",
  "timing_pause",
  "repair_overlap",
  "redaction_nonverbal"
];

function completedRun() {
  return {
    status: "completed",
    merged_transcript: "[00:00] INTERVIEWER: Hello",
    specialists: specialistIds.map((specialist_id) => ({
      specialist_id,
      status: "valid",
      schema_valid: true
    })),
    receipt: {
      attempted_call_count: 4,
      completed_call_count: 4,
      valid_result_count: 4,
      accounting_complete: true
    }
  };
}

test("enables human acceptance only after four distinct strict results", () => {
  assert.equal(canAcceptProfessorDemoRun(completedRun()), true);

  const duplicate = completedRun();
  duplicate.specialists[3].specialist_id = "speaker_turn";
  assert.equal(canAcceptProfessorDemoRun(duplicate), false);

  const incomplete = completedRun();
  incomplete.receipt.valid_result_count = 3;
  assert.equal(canAcceptProfessorDemoRun(incomplete), false);
});

test("blocks acceptance when cost accounting or merge is incomplete", () => {
  const unknownCost = completedRun();
  unknownCost.receipt.accounting_complete = false;
  assert.equal(canAcceptProfessorDemoRun(unknownCost), false);

  const noMerge = completedRun();
  noMerge.merged_transcript = null;
  assert.equal(canAcceptProfessorDemoRun(noMerge), false);
});

test("shows small native charges without rounding them to zero", () => {
  assert.equal(formatProfessorDemoCost("0.0012345"), "$0.001234");
  assert.equal(formatProfessorDemoCost(null), "Unknown");
});
