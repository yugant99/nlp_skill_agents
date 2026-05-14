export type MetricId = string;

export type MetricResult = {
  metric_id: MetricId;
  label: string;
  rows: Record<string, unknown>[];
};

export type MetricPlugin = {
  id: MetricId;
  label: string;
  description: string;
  category: string;
  output_schema: Record<string, string>;
};

export type PluginRequest = {
  id: string;
  title: string;
  research_question: string;
  requested_metric_id: string;
  output_columns: string[];
  examples: {
    transcript: string;
    expected_behavior: string;
  }[];
  status: string;
  created_at: string;
};

export type PluginRequestResponse = {
  request: PluginRequest;
  artifact_path: string;
  implementation_prompt_path: string;
};

export type AgentJob = {
  id: string;
  job_type: string;
  status: string;
  source_request_id: string;
  branch_name: string;
  prompt_path: string;
  allowed_files: string[];
  verification_commands: string[];
  created_at: string;
};

export type AgentJobResponse = {
  job: AgentJob;
  artifact_path: string;
};

export type RunResponse = {
  run_id: string;
  source_filename: string;
  created_at: string;
  turn_count: number;
  skill_pack: SkillPackProvenance | null;
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
  description?: string;
  metrics: MetricId[];
  disfluency_tokens: string[];
  speaker_roles?: Record<string, string | { label?: string; prefixes?: string[] }>;
  speaker_prefixes?: Record<string, string[]>;
  concept_lexicons?: Record<string, string[]>;
  nonverbal_cues?: Record<string, string[]>;
};

export type SkillPackSummary = {
  id: string;
  name: string;
  version: string;
  metric_ids: MetricId[];
  speaker_roles: Record<string, string>;
  speaker_prefixes: Record<string, string[]>;
  disfluency_tokens: string[];
  concept_lexicons: Record<string, string[]>;
  nonverbal_cues: Record<string, string[]>;
};

export type SkillPackDraftResponse = {
  payload: SkillPack;
  skill_pack: SkillPackSummary;
  warnings: string[];
  authoring?: {
    engine: string;
    model: string;
  };
};

export type SkillPackRefineResponse = SkillPackDraftResponse & {
  applied_changes: string[];
};

export type SkillPackProvenance = {
  id: string;
  name: string;
  version: string;
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
