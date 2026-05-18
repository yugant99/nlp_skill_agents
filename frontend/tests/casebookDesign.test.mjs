import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCasebookOptions,
  normalizeConditionList,
  validateBatchAssignments
} from "../../local_data/tmp/frontend-tests/casebookDesign.js";

test("builds deterministic participant condition and week options", () => {
  assert.deepEqual(buildCasebookOptions(3, "home, lab, telehealth", 2), {
    participants: ["P1", "P2", "P3"],
    conditions: ["home", "lab", "telehealth"],
    weeks: ["week_1", "week_2"]
  });
});

test("normalizes repeated condition labels", () => {
  assert.deepEqual(normalizeConditionList(" Home, lab, HOME ,, clinic "), [
    "home",
    "lab",
    "clinic"
  ]);
});

test("flags uploaded assignments outside the casebook design", () => {
  const warnings = validateBatchAssignments(
    [
      {
        source_filename: "P4_field_week3.txt",
        content: "CG: Hello.\nP: Hi.",
        metadata: {
          participant_id: "P4",
          condition: "field",
          week: "week_3"
        }
      }
    ],
    buildCasebookOptions(2, "home, lab", 2)
  );

  assert.deepEqual(warnings, [
    "P4_field_week3.txt uses participant P4 outside P1-P2.",
    "P4_field_week3.txt uses condition field outside home, lab.",
    "P4_field_week3.txt uses week week_3 outside week_1-week_2."
  ]);
});
