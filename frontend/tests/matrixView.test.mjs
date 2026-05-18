import assert from "node:assert/strict";
import test from "node:test";

import { buildMetricMatrix } from "../../local_data/tmp/frontend-tests/matrixView.js";

test("groups metric rows into deterministic participant condition week sums", () => {
  const matrix = buildMetricMatrix(
    {
      metric_id: "mobility_score",
      label: "Mobility score",
      rows: [
        {
          participant_id: "P2",
          condition: "lab",
          week: "week_10",
          score: 1
        },
        {
          participant_id: "P1",
          condition: "home",
          week: "week_2",
          score: 2
        },
        {
          participant_id: "P1",
          condition: "home",
          week: "week_1",
          score: 3
        },
        {
          participant_id: "P1",
          condition: "home",
          week: "week_1",
          score: 4
        },
        {
          participant_id: "P2",
          condition: "lab",
          week: "week_2",
          score: "not scored"
        }
      ]
    },
    "score"
  );

  assert.deepEqual(matrix, {
    metric_id: "mobility_score",
    valueKey: "score",
    weekColumns: ["week_1", "week_2", "week_10"],
    rows: [
      {
        participant_id: "P1",
        condition: "home",
        cells: {
          week_1: 7,
          week_2: 2,
          week_10: 0
        }
      },
      {
        participant_id: "P2",
        condition: "lab",
        cells: {
          week_1: 0,
          week_2: 0,
          week_10: 1
        }
      }
    ]
  });
});

test("uses unknown labels for missing participant condition and week", () => {
  const matrix = buildMetricMatrix(
    {
      metric_id: "turn_count",
      label: "Turn count",
      rows: [
        {
          turn_count: 5
        },
        {
          participant_id: "",
          condition: null,
          week: undefined,
          turn_count: 2
        }
      ]
    },
    "turn_count"
  );

  assert.deepEqual(matrix, {
    metric_id: "turn_count",
    valueKey: "turn_count",
    weekColumns: ["unknown"],
    rows: [
      {
        participant_id: "unknown",
        condition: "unknown",
        cells: {
          unknown: 7
        }
      }
    ]
  });
});
