import type {
  AgentJob,
  AgentJobResponse,
  MetricId,
  MetricPlugin,
  PluginRequest,
  PluginRequestResponse,
  RunHistoryItem,
  RunResponse,
  SkillPack,
  SkillPackDraftResponse,
  SkillPackRefineResponse,
  SkillPackSummary
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
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
