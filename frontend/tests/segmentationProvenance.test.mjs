import assert from "node:assert/strict";
import test from "node:test";

import { segmentationSourceLabel } from "../../local_data/tmp/frontend-tests/segmentationProvenance.js";

test("labels tracked synthetic inputs as synthetic", () => {
  assert.equal(segmentationSourceLabel("synthetic"), "Synthetic source");
});

test("labels uploaded and pasted inputs as researcher provided", () => {
  assert.equal(
    segmentationSourceLabel("researcher_provided"),
    "Researcher-provided source"
  );
});
