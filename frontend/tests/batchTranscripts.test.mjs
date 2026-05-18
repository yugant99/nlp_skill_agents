import assert from "node:assert/strict";
import test from "node:test";

import {
  createBatchTranscriptFromTextFile,
  inferBatchMetadataFromFilename,
  parseBatchTranscriptText,
  serializeBatchTranscriptText,
  updateBatchTranscriptMetadata
} from "../../local_data/tmp/frontend-tests/batchTranscripts.js";

test("parses batch transcript headers into editable file assignments", () => {
  const transcripts = parseBatchTranscriptText(
    [
      "participant_001.txt | participant_id=P1 | condition=home | week=week_1",
      "P1_c: How did walking feel?",
      "P1_p: It hurt.",
      "---",
      "participant_002.txt | participant_id=P2 | condition=lab | week=week_2 | site=north",
      "P2_c: Did sleep improve?",
      "P2_p: Yes."
    ].join("\n")
  );

  assert.equal(transcripts.length, 2);
  assert.equal(transcripts[0].source_filename, "participant_001.txt");
  assert.deepEqual(transcripts[0].metadata, {
    participant_id: "P1",
    condition: "home",
    week: "week_1"
  });
  assert.equal(transcripts[1].metadata?.site, "north");
});

test("updates assignment metadata without changing transcript content", () => {
  const transcripts = parseBatchTranscriptText("one.txt\nCG: Hello.\nP: Hi.");
  const updated = updateBatchTranscriptMetadata(transcripts, 0, "condition", "lab");

  assert.equal(updated[0].content, "CG: Hello.\nP: Hi.");
  assert.deepEqual(updated[0].metadata, { condition: "lab" });
  assert.deepEqual(transcripts[0].metadata, undefined);
});

test("serializes file assignments back into paste format", () => {
  const text = serializeBatchTranscriptText([
    {
      source_filename: "one.txt",
      content: "P1_c: Hello.\nP1_p: Hi.",
      metadata: {
        participant_id: "P1",
        condition: "home",
        week: "week_1"
      }
    }
  ]);

  assert.equal(
    text,
    "one.txt | participant_id=P1 | condition=home | week=week_1\nP1_c: Hello.\nP1_p: Hi."
  );
});

test("infers participant condition and week from uploaded filenames", () => {
  assert.deepEqual(inferBatchMetadataFromFilename("P1_home_week1.txt"), {
    participant_id: "P1",
    condition: "home",
    week: "week_1"
  });
  assert.deepEqual(inferBatchMetadataFromFilename("participant_003_lab_week_2.txt"), {
    participant_id: "P3",
    condition: "lab",
    week: "week_2"
  });
});

test("creates a batch transcript from uploaded text file content", () => {
  assert.deepEqual(
    createBatchTranscriptFromTextFile(
      "P2_clinic_week4.txt",
      "P2_c: Did balance improve?\nP2_p: A little."
    ),
    {
      source_filename: "P2_clinic_week4.txt",
      content: "P2_c: Did balance improve?\nP2_p: A little.",
      metadata: {
        participant_id: "P2",
        condition: "clinic",
        week: "week_4"
      }
    }
  );
});
