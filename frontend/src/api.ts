import type { MetricId, RunResponse, SkillPack } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

export async function loadSkillPack(): Promise<SkillPack> {
  const response = await fetch(`${API_BASE}/api/skill-packs/default`);
  if (!response.ok) {
    throw new Error("Could not load study skill pack");
  }
  return response.json();
}

export async function createAnalysisRun(params: {
  file: File;
  participantId: string;
  selectedMetrics: MetricId[];
  disfluencyTokens: string[];
}): Promise<RunResponse> {
  const form = new FormData();
  form.append("file", params.file);
  form.append(
    "config",
    JSON.stringify({
      participant_id: params.participantId,
      selected_metrics: params.selectedMetrics,
      disfluency_tokens: params.disfluencyTokens
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

