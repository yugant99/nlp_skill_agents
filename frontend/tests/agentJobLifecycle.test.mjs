import assert from "node:assert/strict";
import test from "node:test";

import { agentJobActions } from "../../local_data/tmp/frontend-tests/agentJobLifecycle.js";

test("maps only the transitions unlocked by the server", () => {
  assert.deepEqual(
    agentJobActions({ available_transitions: ["in_progress", "blocked"] }),
    [
      { status: "in_progress", label: "Start" },
      { status: "blocked", label: "Block" }
    ]
  );

  assert.deepEqual(
    agentJobActions({ available_transitions: ["verified"] }),
    [{ status: "verified", label: "Verify" }]
  );
});

test("shows no lifecycle action when evidence gates lock the next transition", () => {
  assert.deepEqual(agentJobActions({ available_transitions: [] }), []);
});
