import assert from "node:assert/strict";
import test from "node:test";

import {
  RULE_CHECK_LIMIT_TEXT,
  ruleCheckSummary,
  validationStatusLabel
} from "../../local_data/tmp/frontend-tests/segmentationValidation.js";

test("labels configured rule checks without presenting an accuracy score", () => {
  assert.equal(
    ruleCheckSummary({ configured_rule_count: 10, passed_rule_count: 9 }),
    "9/10 rule checks"
  );
  assert.equal(
    ruleCheckSummary({ configured_rule_count: 0, passed_rule_count: 0 }),
    "No rule checks configured"
  );
  assert.match(RULE_CHECK_LIMIT_TEXT, /not an accuracy, agreement, or calibrated-confidence/);
});

test("surfaces the unvalidated domain status", () => {
  assert.equal(
    validationStatusLabel({ status: "not_domain_validated" }),
    "Not domain validated"
  );
  assert.equal(
    validationStatusLabel({ status: "unknown" }),
    "Validation status unavailable"
  );
});
