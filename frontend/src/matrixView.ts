import type { MetricResult } from "./types";

export type MetricMatrixRow = {
  participant_id: string;
  condition: string;
  cells: Record<string, number>;
};

export type MetricMatrixView = {
  metric_id: string;
  valueKey: string;
  weekColumns: string[];
  rows: MetricMatrixRow[];
};

const UNKNOWN_LABEL = "unknown";

const normalizeLabel = (value: unknown): string => {
  if (typeof value !== "string") {
    return UNKNOWN_LABEL;
  }

  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : UNKNOWN_LABEL;
};

const numericValue = (value: unknown): number | null => {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }

  return null;
};

const sortLabel = (left: string, right: string): number =>
  left.localeCompare(right, undefined, { numeric: true, sensitivity: "base" });

export const buildMetricMatrix = (
  result: MetricResult,
  valueKey: string
): MetricMatrixView => {
  const rowMap = new Map<string, { participant_id: string; condition: string; cells: Map<string, number> }>();
  const weeks = new Set<string>();

  for (const row of result.rows) {
    const value = numericValue(row[valueKey]);

    if (value === null) {
      continue;
    }

    const participant_id = normalizeLabel(row.participant_id);
    const condition = normalizeLabel(row.condition);
    const week = normalizeLabel(row.week);
    const rowKey = JSON.stringify([participant_id, condition]);

    weeks.add(week);

    const existing = rowMap.get(rowKey) ?? {
      participant_id,
      condition,
      cells: new Map<string, number>()
    };

    existing.cells.set(week, (existing.cells.get(week) ?? 0) + value);
    rowMap.set(rowKey, existing);
  }

  const weekColumns = [...weeks].sort(sortLabel);

  const rows = [...rowMap.values()]
    .sort((left, right) => {
      const participantCompare = sortLabel(left.participant_id, right.participant_id);
      return participantCompare || sortLabel(left.condition, right.condition);
    })
    .map(({ participant_id, condition, cells }) => ({
      participant_id,
      condition,
      cells: Object.fromEntries(weekColumns.map((week) => [week, cells.get(week) ?? 0]))
    }));

  return {
    metric_id: result.metric_id,
    valueKey,
    weekColumns,
    rows
  };
};
