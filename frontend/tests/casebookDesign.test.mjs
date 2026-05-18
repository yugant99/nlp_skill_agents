import assert from "node:assert/strict";
import test from "node:test";

import {
  CASEBOOK_TEMPLATES,
  buildCasebookOptions,
  casebookRequestFromControls,
  normalizeConditionList,
  schemaControlsFromStudySchema,
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

test("exposes professor-facing casebook templates", () => {
  assert.deepEqual(Object.keys(CASEBOOK_TEMPLATES), [
    "caregiver_mobility",
    "interview",
    "therapy_session"
  ]);
  assert.equal(CASEBOOK_TEMPLATES.caregiver_mobility.conditions, "home, lab, clinic");
  assert.deepEqual(CASEBOOK_TEMPLATES.caregiver_mobility.customFields, [
    "site",
    "study_arm"
  ]);
});

test("converts controls into backend schema request payload", () => {
  assert.deepEqual(
    casebookRequestFromControls({
      participantCount: 4,
      conditions: "home, lab, home",
      weekCount: 3,
      customFields: "site, arm, site"
    }),
    {
      participant_count: 4,
      conditions: ["home", "lab"],
      week_count: 3,
      custom_fields: ["site", "arm"]
    }
  );
});

test("converts persisted backend schema into editable controls", () => {
  assert.deepEqual(
    schemaControlsFromStudySchema({
      study_id: "demo",
      participant_count: 3,
      participants: ["P1", "P2", "P3"],
      conditions: ["home", "lab"],
      week_count: 2,
      weeks: ["week_1", "week_2"],
      custom_fields: ["site", "arm"],
      updated_at: "2026-05-18T00:00:00Z"
    }),
    {
      participantCount: 3,
      conditions: "home, lab",
      weekCount: 2,
      customFields: "site, arm"
    }
  );
});
