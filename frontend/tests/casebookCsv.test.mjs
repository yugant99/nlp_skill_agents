import assert from "node:assert/strict";
import test from "node:test";

import {
  exportCasebookCsv,
  parseCasebookCsv
} from "../../local_data/tmp/frontend-tests/casebookCsv.js";

test("exports assignment metadata with sorted custom columns and no transcript content", () => {
  const csv = exportCasebookCsv([
    {
      source_filename: "P1_home_week1.txt",
      content: "CG: This transcript text must not be exported.",
      metadata: {
        participant_id: "P1",
        condition: "home",
        week: "week_1",
        site: "north",
        study_arm: "A"
      }
    },
    {
      source_filename: "P2_lab_week2.txt",
      content: "P: More private transcript content.",
      metadata: {
        participant_id: "P2",
        condition: "lab",
        week: "week_2",
        cohort: "fall"
      }
    }
  ]);

  assert.equal(
    csv,
    [
      "source_filename,participant_id,condition,week,cohort,site,study_arm",
      "P1_home_week1.txt,P1,home,week_1,,north,A",
      "P2_lab_week2.txt,P2,lab,week_2,fall,,"
    ].join("\n")
  );
  assert.equal(csv.includes("transcript content"), false);
});

test("quotes exported cells containing commas quotes or newlines", () => {
  const csv = exportCasebookCsv([
    {
      source_filename: "P1, intake.txt",
      content: "CG: Hidden.",
      metadata: {
        participant_id: "P1",
        condition: "home, remote",
        week: "week_1",
        note: 'participant said "yes"\nthen paused'
      }
    }
  ]);

  assert.equal(
    csv,
    [
      "source_filename,participant_id,condition,week,note",
      '"P1, intake.txt",P1,"home, remote",week_1,"participant said ""yes""\nthen paused"'
    ].join("\n")
  );
});

test("parses casebook CSV into metadata by source filename", () => {
  const metadata = parseCasebookCsv(
    [
      "source_filename,participant_id,condition,week,note,site",
      '"P1, intake.txt",P1,"home, remote",week_1,"participant said ""yes""",',
      "P2_lab_week2.txt,P2,lab,week_2,,north"
    ].join("\n")
  );

  assert.deepEqual(metadata, {
    "P1, intake.txt": {
      participant_id: "P1",
      condition: "home, remote",
      week: "week_1",
      note: 'participant said "yes"'
    },
    "P2_lab_week2.txt": {
      participant_id: "P2",
      condition: "lab",
      week: "week_2",
      site: "north"
    }
  });
});

test("omits blank metadata values while parsing", () => {
  assert.deepEqual(parseCasebookCsv("source_filename,participant_id,condition\none.txt,P1, "), {
    "one.txt": {
      participant_id: "P1"
    }
  });
});
