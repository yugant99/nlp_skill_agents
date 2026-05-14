import type {
  MetricId,
  RunHistoryItem,
  RunResponse,
  SkillPack,
  SkillPackDraftResponse,
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
}): Promise<SkillPackDraftResponse> {
  const response = await fetch(`${API_BASE}/api/skill-packs/draft`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(params)
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || "Skill pack draft failed");
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
