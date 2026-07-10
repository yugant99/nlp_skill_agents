import type {
  AgentJob,
  AgentJobEvidence,
  AgentJobResponse,
  BatchTranscript,
  CUnitRulebookSummary,
  DeploymentProfile,
  MetricId,
  MetricPlugin,
  PluginRequest,
  PluginRequestResponse,
  RunHistoryItem,
  RunResponse,
  SegmentationCase,
  SegmentationCorpusRun,
  SegmentationCorpusRunResponse,
  SegmentationEvaluationResponse,
  SegmentationRun,
  SegmentationRunResponse,
  SkillPack,
  SkillPackDraftResponse,
  SkillPackRefineResponse,
  SkillPackSummary,
  StudyBatchResponse,
  StudyBatchRunDetail,
  StudyBatchRunSummary,
  StudyBatchSummary,
  StudySchema,
  StudySkillPackVersion,
  StudyWorkspace
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

export async function getDeploymentProfile(
  profile = "secure-offline"
): Promise<DeploymentProfile> {
  const response = await fetch(`${API_BASE}/api/deployment-profile/${profile}`);
  if (!response.ok) {
    throw new Error("Could not load privacy mode");
  }
  return response.json();
}

export async function loadSkillPack(): Promise<SkillPack> {
  const response = await fetch(`${API_BASE}/api/skill-packs/default`);
  if (!response.ok) {
    throw new Error("Could not load study skill pack");
  }
  return response.json();
}

export async function listMetricPlugins(): Promise<MetricPlugin[]> {
  const response = await fetch(`${API_BASE}/api/metric-plugins`);
  if (!response.ok) {
    throw new Error("Could not load metric plugin catalog");
  }
  const payload = (await response.json()) as { plugins: MetricPlugin[] };
  return payload.plugins;
}

export async function createPluginRequest(params: {
  title: string;
  researchQuestion: string;
  requestedMetricId: string;
  outputColumns: string;
  exampleTranscript: string;
  expectedBehavior: string;
}): Promise<PluginRequestResponse> {
  const response = await fetch(`${API_BASE}/api/plugin-requests`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      title: params.title,
      research_question: params.researchQuestion,
      requested_metric_id: params.requestedMetricId,
      output_columns: params.outputColumns,
      example_transcript: params.exampleTranscript,
      expected_behavior: params.expectedBehavior
    })
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Plugin request failed");
  }
  return response.json();
}

export async function listPluginRequests(): Promise<PluginRequest[]> {
  const response = await fetch(`${API_BASE}/api/plugin-requests`);
  if (!response.ok) {
    throw new Error("Could not load plugin requests");
  }
  const payload = (await response.json()) as { requests: PluginRequest[] };
  return payload.requests;
}

export async function createPluginBuildJob(requestId: string): Promise<AgentJobResponse> {
  const response = await fetch(`${API_BASE}/api/plugin-requests/${requestId}/build-job`, {
    method: "POST"
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not create plugin build job");
  }
  return response.json();
}

export async function listAgentJobs(): Promise<AgentJob[]> {
  const response = await fetch(`${API_BASE}/api/agent-jobs`);
  if (!response.ok) {
    throw new Error("Could not load agent jobs");
  }
  const payload = (await response.json()) as { jobs: AgentJob[] };
  return payload.jobs;
}

export async function updateAgentJobStatus(
  jobId: string,
  status: string
): Promise<AgentJobResponse> {
  const response = await fetch(`${API_BASE}/api/agent-jobs/${jobId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ status })
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not update agent job");
  }
  return response.json();
}

export async function createAgentJobEvidence(params: {
  jobId: string;
  gate: string;
  command: string;
  status: string;
  summary: string;
}): Promise<AgentJobEvidence> {
  const response = await fetch(`${API_BASE}/api/agent-jobs/${params.jobId}/evidence`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      gate: params.gate,
      command: params.command,
      status: params.status,
      summary: params.summary
    })
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not record agent job evidence");
  }
  const payload = (await response.json()) as { evidence: AgentJobEvidence };
  return payload.evidence;
}

export async function listSegmentationCases(): Promise<SegmentationCase[]> {
  const response = await fetch(`${API_BASE}/api/segmentation/cases`);
  if (!response.ok) {
    throw new Error("Could not load segmentation cases");
  }
  const payload = (await response.json()) as { cases: SegmentationCase[] };
  return payload.cases;
}

export async function getSegmentationCase(caseId: string): Promise<SegmentationCase> {
  const response = await fetch(`${API_BASE}/api/segmentation/cases/${caseId}`);
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not load segmentation case");
  }
  const payload = (await response.json()) as { case: SegmentationCase };
  return payload.case;
}

export async function getSegmentationRulebook(): Promise<CUnitRulebookSummary> {
  const response = await fetch(`${API_BASE}/api/segmentation/rulebook`);
  if (!response.ok) {
    throw new Error("Could not load C-unit rulebook");
  }
  const payload = (await response.json()) as { rulebook: CUnitRulebookSummary };
  return payload.rulebook;
}

export async function evaluateSegmentationDraft(params: {
  caseId: string;
  draftText: string;
}): Promise<SegmentationEvaluationResponse> {
  const response = await fetch(`${API_BASE}/api/segmentation/evaluate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      case_id: params.caseId,
      draft_text: params.draftText
    })
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not evaluate segmentation draft");
  }
  return response.json();
}

export async function createSegmentationRewriteJob(
  caseId: string
): Promise<AgentJobResponse> {
  const response = await fetch(`${API_BASE}/api/segmentation/cases/${caseId}/rewrite-job`, {
    method: "POST"
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not queue segmentation rewrite job");
  }
  return response.json();
}

export async function createSegmentationRun(params: {
  sourceFilename: string;
  descriptText: string;
  ruleIds: string[];
}): Promise<SegmentationRun> {
  const response = await fetch(`${API_BASE}/api/segmentation/runs`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      source_filename: params.sourceFilename,
      descript_text: params.descriptText,
      rule_ids: params.ruleIds
    })
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not create segmentation run");
  }
  const payload = (await response.json()) as SegmentationRunResponse;
  return payload.run;
}

export async function listSegmentationRuns(): Promise<SegmentationRun[]> {
  const response = await fetch(`${API_BASE}/api/segmentation/runs`);
  if (!response.ok) {
    throw new Error("Could not load segmentation runs");
  }
  const payload = (await response.json()) as { runs: SegmentationRun[] };
  return payload.runs;
}

export async function createSegmentationCorpusRun(
  seed: number
): Promise<SegmentationCorpusRun> {
  const response = await fetch(`${API_BASE}/api/segmentation/corpus-runs`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ seed })
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not run synthetic segmentation corpus");
  }
  const payload = (await response.json()) as SegmentationCorpusRunResponse;
  return payload.corpus_run;
}

export async function listSegmentationCorpusRuns(): Promise<SegmentationCorpusRun[]> {
  const response = await fetch(`${API_BASE}/api/segmentation/corpus-runs`);
  if (!response.ok) {
    throw new Error("Could not load segmentation corpus runs");
  }
  const payload = (await response.json()) as { corpus_runs: SegmentationCorpusRun[] };
  return payload.corpus_runs;
}

export async function createSegmentationFileRun(params: {
  file: File;
  ruleIds: string[];
}): Promise<SegmentationRun> {
  const form = new FormData();
  form.append("file", params.file);
  form.append("rule_ids", JSON.stringify(params.ruleIds));
  const response = await fetch(`${API_BASE}/api/segmentation/runs/files`, {
    method: "POST",
    body: form
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not create segmentation run from file");
  }
  const payload = (await response.json()) as SegmentationRunResponse;
  return payload.run;
}

export async function verifySegmentationRun(runId: string): Promise<SegmentationRun> {
  const response = await fetch(`${API_BASE}/api/segmentation/runs/${runId}/verify`, {
    method: "POST"
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not verify segmentation run");
  }
  const payload = (await response.json()) as SegmentationRunResponse;
  return payload.run;
}

export async function createSegmentationRunRewriteJob(
  runId: string
): Promise<AgentJobResponse> {
  const response = await fetch(`${API_BASE}/api/segmentation/runs/${runId}/rewrite-job`, {
    method: "POST"
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not queue segmentation rewrite job");
  }
  return response.json();
}

export async function analyzeSegmentationRun(
  runId: string,
  params?: {
    selectedMetrics?: MetricId[];
    disfluencyTokens?: string[];
    skillPack?: unknown;
  }
): Promise<RunResponse> {
  const response = await fetch(`${API_BASE}/api/segmentation/runs/${runId}/analysis`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      config: {
        selected_metrics: params?.selectedMetrics,
        disfluency_tokens: params?.disfluencyTokens,
        ...(params?.skillPack ? { skill_pack: params.skillPack } : {})
      }
    })
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not analyze segmentation run");
  }
  return response.json();
}

export async function createStudy(params: {
  name: string;
  description: string;
}): Promise<StudyWorkspace> {
  const response = await fetch(`${API_BASE}/api/studies`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(params)
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not create study");
  }
  const payload = (await response.json()) as { study: StudyWorkspace };
  return payload.study;
}

export async function listStudies(): Promise<StudyWorkspace[]> {
  const response = await fetch(`${API_BASE}/api/studies`);
  if (!response.ok) {
    throw new Error("Could not load studies");
  }
  const payload = (await response.json()) as { studies: StudyWorkspace[] };
  return payload.studies;
}

export async function getStudySchema(studyId: string): Promise<StudySchema> {
  const response = await fetch(`${API_BASE}/api/studies/${studyId}/schema`);
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not load study schema");
  }
  const payload = (await response.json()) as { schema: StudySchema };
  return payload.schema;
}

export async function updateStudySchema(
  studyId: string,
  payload: {
    participant_count: number;
    conditions: string[];
    week_count: number;
    custom_fields: string[];
  }
): Promise<StudySchema> {
  const response = await fetch(`${API_BASE}/api/studies/${studyId}/schema`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not save study schema");
  }
  const body = (await response.json()) as { schema: StudySchema };
  return body.schema;
}

export async function listStudyBatches(studyId: string): Promise<StudyBatchSummary[]> {
  const response = await fetch(`${API_BASE}/api/studies/${studyId}/batches`);
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not load study batches");
  }
  const payload = (await response.json()) as { batches: StudyBatchSummary[] };
  return payload.batches;
}

export async function getStudyBatch(
  studyId: string,
  batchId: string
): Promise<StudyBatchResponse> {
  const response = await fetch(`${API_BASE}/api/studies/${studyId}/batches/${batchId}`);
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not load study batch");
  }
  return response.json();
}

export async function listStudyBatchRuns(
  studyId: string,
  batchId: string
): Promise<StudyBatchRunSummary[]> {
  const response = await fetch(`${API_BASE}/api/studies/${studyId}/batches/${batchId}/runs`);
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not load batch transcript runs");
  }
  const payload = (await response.json()) as { runs: StudyBatchRunSummary[] };
  return payload.runs;
}

export async function getStudyBatchRun(
  studyId: string,
  batchId: string,
  runId: string
): Promise<StudyBatchRunDetail> {
  const response = await fetch(`${API_BASE}/api/studies/${studyId}/batches/${batchId}/runs/${runId}`);
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not load transcript run");
  }
  const payload = (await response.json()) as { run: StudyBatchRunDetail };
  return payload.run;
}

export async function addStudySkillPackVersion(
  studyId: string,
  payload: unknown
): Promise<StudySkillPackVersion> {
  const response = await fetch(`${API_BASE}/api/studies/${studyId}/skill-pack-versions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not save study skill-pack version");
  }
  const body = (await response.json()) as { version: StudySkillPackVersion };
  return body.version;
}

export async function createStudyTextBatch(params: {
  studyId: string;
  skillPackVersionId: string;
  transcripts: BatchTranscript[];
}): Promise<StudyBatchResponse> {
  const response = await fetch(`${API_BASE}/api/studies/${params.studyId}/batches/text`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      skill_pack_version_id: params.skillPackVersionId,
      transcripts: params.transcripts
    })
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not run study batch");
  }
  return response.json();
}

export async function createStudyFileBatch(params: {
  studyId: string;
  skillPackVersionId: string;
  files: File[];
  metadataByFilename: Record<string, Record<string, string>>;
}): Promise<StudyBatchResponse> {
  const formData = new FormData();
  formData.append("skill_pack_version_id", params.skillPackVersionId);
  formData.append("metadata", JSON.stringify(params.metadataByFilename));
  for (const file of params.files) {
    formData.append("files", file);
  }
  const response = await fetch(`${API_BASE}/api/studies/${params.studyId}/batches/files`, {
    method: "POST",
    body: formData
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Could not run study file batch");
  }
  return response.json();
}

export async function validateSkillPack(payload: unknown): Promise<SkillPackSummary> {
  const response = await fetch(`${API_BASE}/api/skill-packs/validate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Skill pack validation failed");
  }
  const body = (await response.json()) as { skill_pack: SkillPackSummary };
  return body.skill_pack;
}

export async function validateSkillPackText(params: {
  filename: string;
  content: string;
}): Promise<{ summary: SkillPackSummary; payload: unknown }> {
  const response = await fetch(`${API_BASE}/api/skill-packs/validate-text`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      filename: params.filename,
      content: params.content
    })
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Skill pack validation failed");
  }
  const body = (await response.json()) as {
    skill_pack: SkillPackSummary;
    payload: unknown;
  };
  return { summary: body.skill_pack, payload: body.payload };
}

export async function draftSkillPack(params: {
  brief: string;
  name?: string;
  authoringEngine?: string;
  model?: string;
}): Promise<SkillPackDraftResponse> {
  const response = await fetch(`${API_BASE}/api/skill-packs/draft`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      brief: params.brief,
      name: params.name,
      authoring_engine: params.authoringEngine ?? "local",
      model: params.model
    })
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Skill pack draft failed");
  }
  return response.json();
}

export async function refineSkillPack(params: {
  payload: unknown;
  instruction: string;
  authoringEngine?: string;
  model?: string;
}): Promise<SkillPackRefineResponse> {
  const response = await fetch(`${API_BASE}/api/skill-packs/refine`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      payload: params.payload,
      instruction: params.instruction,
      authoring_engine: params.authoringEngine ?? "local",
      model: params.model
    })
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Skill pack refinement failed");
  }
  return response.json();
}

export async function createAnalysisRun(params: {
  file: File;
  participantId: string;
  speakerPrefixes: {
    caregiver: string;
    participant: string;
  };
  selectedMetrics: MetricId[];
  disfluencyTokens: string[];
  skillPack?: unknown;
}): Promise<RunResponse> {
  const form = new FormData();
  form.append("file", params.file);
  form.append(
    "config",
    JSON.stringify({
      participant_id: params.participantId,
      speaker_prefixes: params.speakerPrefixes,
      selected_metrics: params.selectedMetrics,
      disfluency_tokens: params.disfluencyTokens,
      ...(params.skillPack ? { skill_pack: params.skillPack } : {})
    })
  );

  const response = await fetch(`${API_BASE}/api/runs`, {
    method: "POST",
    body: form
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Analysis run failed");
  }
  return response.json();
}

export async function createTextAnalysisRun(params: {
  content: string;
  sourceFilename: string;
  participantId: string;
  speakerPrefixes: {
    caregiver: string;
    participant: string;
  };
  selectedMetrics: MetricId[];
  disfluencyTokens: string[];
  skillPack?: unknown;
}): Promise<RunResponse> {
  const response = await fetch(`${API_BASE}/api/runs/text`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      source_filename: params.sourceFilename,
      content: params.content,
      config: {
        participant_id: params.participantId,
        speaker_prefixes: params.speakerPrefixes,
        selected_metrics: params.selectedMetrics,
        disfluency_tokens: params.disfluencyTokens,
        ...(params.skillPack ? { skill_pack: params.skillPack } : {})
      }
    })
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Text analysis run failed");
  }
  return response.json();
}

export async function listRuns(): Promise<RunHistoryItem[]> {
  const response = await fetch(`${API_BASE}/api/runs`);
  if (!response.ok) {
    throw new Error("Could not load local run history");
  }
  const payload = (await response.json()) as { runs: RunHistoryItem[] };
  return payload.runs;
}
