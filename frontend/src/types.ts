export type MetricId = "base_metrics" | "lexical_metrics" | "disfluency_metrics";

export type MetricResult = {
  metric_id: MetricId;
  label: string;
  rows: Record<string, unknown>[];
};

export type RunResponse = {
  run_id: string;
  source_filename: string;
  created_at: string;
  turn_count: number;
  results: MetricResult[];
  stored: {
    run_dir: string;
    export_dir: string;
    results_json: string;
  };
};

export type SkillPack = {
  id: string;
  name: string;
  version: string;
  description: string;
  metrics: MetricId[];
  disfluency_tokens: string[];
};

