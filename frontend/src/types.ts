export type MetricId = string;

export type SegmentationSource = "researcher_provided" | "synthetic";

export type DeploymentProfileCheck = {
  id: string;
  status: string;
  message: string;
};

export type DeploymentProfile = {
  profile: string;
  ready: boolean;
  checks: DeploymentProfileCheck[];
};

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

export type BatchTranscript = {
  source_filename: string;
  content: string;
  metadata?: Record<string, string>;
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
  status: AgentJobStatus;
  source_request_id: string;
  branch_name: string;
  prompt_path: string;
  runbook_path: string;
  allowed_files: string[];
  verification_commands: string[];
  available_transitions: AgentJobTransition[];
  created_at: string;
};

export type AgentJobStatus = "queued" | "in_progress" | "blocked" | "verified" | "merged";

export type AgentJobTransition = Exclude<AgentJobStatus, "queued">;

export type AgentJobResponse = {
  job: AgentJob;
  artifact_path: string;
};

export type AgentJobEvidence = {
  job_id: string;
  gate: string;
  command: string;
  status: string;
  summary: string;
  artifact_path: string;
  created_at: string;
};

export type SegmentationCase = {
  case_id: string;
  title: string;
  descript_text: string;
  gold_text: string;
  rule_ids: string[];
  official_source_guard_tokens: string[];
  forbidden_source_tokens: string[];
  source: "synthetic";
};

export type SegmentationMetrics = {
  line_count: number;
  utterance_count: number;
  time_marker_count: number;
  pause_marker_count: number;
  speaker_counts: Record<string, number>;
  special_notation_counts: Record<string, number>;
};

export type SegmentationRuleFailure = {
  rule_id: string;
  message: string;
  line_number?: number | null;
};

export type SegmentationEvaluation = {
  score: number;
  metrics: SegmentationMetrics;
  failures: SegmentationRuleFailure[];
};

export type SegmentationEvaluationResponse = {
  case_id: string;
  source: "synthetic";
  evaluation: SegmentationEvaluation;
};

export type CUnitBoundaryDecision = {
  event_index: number;
  speaker: string;
  raw_text: string;
  cleaned_text: string;
  boundary_type: string;
  decision: string;
  cunit_count: number;
  rationale: string;
  confidence: number;
  needs_human_review: boolean;
  excluded_maze: string;
  evidence_terms: string[];
};

export type CUnitAdjudication = {
  total_event_count: number;
  participant_turn_count: number;
  examiner_turn_count: number;
  counted_cunit_count: number;
  needs_review_count: number;
  boundary_type_counts: Record<string, number>;
  decisions: CUnitBoundaryDecision[];
};

export type CUnitRuleDefinition = {
  rule_id: string;
  label: string;
  specialist_id: string;
  deterministic_check: string;
  current_depth: string;
  scientist_language: string;
};

export type ProfessorGradeRuleArea = {
  area_id: string;
  label: string;
  status: string;
  scientist_language: string;
};

export type CUnitRulebookSummary = {
  supported_rule_count: number;
  demo_case_rule_count: number;
  corpus_rule_count: number;
  rule_definitions: CUnitRuleDefinition[];
  professor_grade_areas: ProfessorGradeRuleArea[];
};

export type SegmentationEvent = {
  timestamp_seconds: number;
  speaker: string;
  text: string;
  source_filename: string;
};

export type SegmentationRulePacket = {
  specialist_id: string;
  rule_ids: string[];
};

export type SegmentationPatch = {
  operation: string;
  event_index: number;
  text: string;
  reason: string;
};

export type SegmentationSpecialistOutput = {
  specialist_id: string;
  rule_ids: string[];
  patches: SegmentationPatch[];
  evidence: {
    source_event_indexes?: number[];
    patch_count?: number;
    [key: string]: unknown;
  };
};

export type SegmentationRun = {
  run_id: string;
  source_filename: string;
  descript_text: string;
  events: SegmentationEvent[];
  rule_ids: string[];
  rule_plan: SegmentationRulePacket[];
  specialist_outputs: SegmentationSpecialistOutput[];
  merged_draft: string;
  merge_evidence: {
    applied_patch_count: number;
    conflicts: string[];
  };
  cunit_adjudication: CUnitAdjudication;
  evaluation: SegmentationEvaluation | null;
  status: string;
  failure_routes: {
    rule_id: string;
    specialist_id: string;
    message: string;
  }[];
  source: SegmentationSource;
  created_at: string;
};

export type SegmentationRunResponse = {
  run: SegmentationRun;
};

export type SegmentationCorpusCaseResult = {
  case_id: string;
  title: string;
  run_id: string;
  status: string;
  expected_status: string;
  outcome: string;
  score: number;
  rule_ids: string[];
  failed_rule_ids: string[];
};

export type SegmentationCorpusRun = {
  corpus_run_id: string;
  seed: number;
  status: string;
  total_case_count: number;
  regression_pass_count: number;
  regression_fail_count: number;
  rule_coverage: string[];
  results: SegmentationCorpusCaseResult[];
  source: "synthetic";
  created_at: string;
};

export type SegmentationCorpusRunResponse = {
  corpus_run: SegmentationCorpusRun;
};

export type StudyWorkspace = {
  id: string;
  name: string;
  description: string;
  created_at: string;
};

export type StudySkillPackVersion = {
  study_id: string;
  version_id: string;
  artifact_path: string;
  created_at: string;
  skill_pack: {
    id: string;
    name: string;
    version: string;
    metrics: MetricId[];
  };
};

export type StudySchema = {
  study_id: string;
  participant_count: number;
  participants: string[];
  conditions: string[];
  week_count: number;
  weeks: string[];
  custom_fields: string[];
  updated_at: string;
};

export type StudyBatchResponse = {
  batch: {
    study_id: string;
    batch_id: string;
    skill_pack_version_id: string;
    run_count: number;
    failure_count: number;
    aggregate_dir: string;
    created_at: string;
  };
  aggregate_results_json: string;
  study_schema: StudySchema | null;
  failures: {
    source_filename: string;
    error: string;
  }[];
  results: MetricResult[];
  exports: {
    metric_id: string;
    filename: string;
    path: string;
  }[];
};

export type StudyBatchSummary = StudyBatchResponse["batch"];

export type StudyBatchRunSummary = {
  run_id: string;
  source_filename: string;
  metadata: Record<string, string>;
  created_at: string;
  turn_count: number;
  metric_ids: MetricId[];
};

export type StudyBatchRunDetail = StudyBatchRunSummary & {
  turns: {
    turn_index: number;
    role: string;
    speaker_label: string;
    raw_prefix: string;
    text: string;
  }[];
  results: MetricResult[];
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
