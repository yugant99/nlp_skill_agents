import assert from "node:assert/strict";
import test from "node:test";

import { privacyModeLabel } from "../../local_data/tmp/frontend-tests/privacyMode.js";

test("reports offline readiness when network authoring is disabled", () => {
  assert.equal(
    privacyModeLabel({
      profile: "secure-offline",
      ready: true,
      checks: [
        {
          id: "network_llm_disabled",
          status: "passed",
          message: "No OpenRouter API key is configured."
        }
      ]
    }),
    "Offline ready"
  );
});

test("reports configured external authoring instead of claiming offline mode", () => {
  assert.equal(
    privacyModeLabel({
      profile: "secure-offline",
      ready: false,
      checks: [
        {
          id: "network_llm_disabled",
          status: "failed",
          message: "OPENROUTER_API_KEY is configured."
        }
      ]
    }),
    "External authoring configured"
  );
});

test("fails visibly when the profile omits its network check", () => {
  assert.equal(
    privacyModeLabel({
      profile: "secure-offline",
      ready: false,
      checks: []
    }),
    "Status unavailable"
  );
});
