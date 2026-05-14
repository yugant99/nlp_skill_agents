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
  diagnostics: TranscriptDiagnostics;
  results: MetricResult[];
  stored: {
    run_dir: string;
    export_dir: string;
    results_json: string;
  };
  exports: ExportLink[];
};

export type SkillPack = {
  id: string;
  name: string;
  version: string;
  description: string;
  metrics: MetricId[];
  disfluency_tokens: string[];
};

export type ExportLink = {
  metric_id: MetricId;
  filename: string;
  download_url: string;
};

export type RunHistoryItem = {
  run_id: string;
  source_filename: string;
  created_at: string;
  metric_count: number;
  results_json: string;
  export_dir: string;
};

export type TranscriptDiagnostics = {
  turn_counts: Record<string, number>;
  warnings: DiagnosticWarning[];
};

export type DiagnosticWarning = {
  code: string;
  message: string;
};
