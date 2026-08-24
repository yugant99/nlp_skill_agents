export type ProfessorDemoSpecialistId =
  | "speaker_turn"
  | "timing_pause"
  | "repair_overlap"
  | "redaction_nonverbal";

export type ProfessorDemoCallReceipt = {
  model_requested: string;
  endpoint_requested: string;
  generation_id: string | null;
  model_returned: string | null;
  provider_returned: string | null;
  router_attempt_count: number | null;
  cache_hit: boolean | null;
  finish_reason: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  reasoning_tokens: number | null;
  total_tokens: number | null;
  cost_usd: string | null;
  accounting_complete: boolean;
  latency_ms: number;
};

export type ProfessorDemoSpecialistRun = {
  specialist_id: ProfessorDemoSpecialistId;
  label: string;
  status: "valid" | "error";
  schema_valid: boolean;
  output: Record<string, unknown> | null;
  receipt: ProfessorDemoCallReceipt;
  error_code: string | null;
  error_message: string | null;
};

export type ProfessorDemoPreflightReceipt = {
  model: string;
  endpoint: string;
  provider: string;
  endpoint_is_zdr: boolean;
  required_parameters_supported: boolean;
  metadata_request_count: number;
  max_prompt_tokens_per_call: number;
  max_completion_tokens_per_call: number;
  prompt_price_per_token_usd: string;
  completion_price_per_token_usd: string;
  estimated_max_cost_usd: string;
  cost_ceiling_usd: string;
  checked_at: string;
};

export type ProfessorDemoUsageReceipt = {
  model: string;
  provider: string;
  endpoint: string;
  attempted_call_count: number;
  completed_call_count: number;
  valid_result_count: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  reasoning_tokens: number | null;
  total_tokens: number | null;
  known_cost_subtotal_usd: string;
  total_cost_usd: string | null;
  accounting_complete: boolean;
  currency: "USD";
  preflight: ProfessorDemoPreflightReceipt;
};

export type ProfessorDemoRevision = {
  revision_number: 0 | 1;
  revision_id: string;
  transcript: string;
  sha256: string;
  created_at: string;
};

export type ProfessorDemoRun = {
  run_id: string;
  status: "completed" | "failed";
  source: "synthetic-demo";
  created_at: string;
  original_transcript: string;
  merged_transcript: string | null;
  merged_line_count: number;
  specialists: ProfessorDemoSpecialistRun[];
  receipt: ProfessorDemoUsageReceipt;
  revision_state: {
    active_revision_number: 0 | 1;
    original: ProfessorDemoRevision;
    candidate: ProfessorDemoRevision | null;
    accepted_at: string | null;
    reverted_at: string | null;
  };
  failure_code: string | null;
  failure_message: string | null;
};

export const PROFESSOR_DEMO_SAMPLE = `[00:00] Q: Could you walk me through the delay?
[00:05] A: Um, I got there at nine—no, nine fifteen. [door closes]
[00:12] Q: What happened next?
[00:15] A: My name is Maya Patel, and I... I waited about forty minutes. (long pause)`;

export const PROFESSOR_DEMO_SPECIALISTS: ReadonlyArray<{
  id: ProfessorDemoSpecialistId;
  label: string;
  description: string;
}> = [
  {
    id: "speaker_turn",
    label: "Speaker turns",
    description: "Normalizes who said each line"
  },
  {
    id: "timing_pause",
    label: "Timing + pauses",
    description: "Reads explicit timing evidence"
  },
  {
    id: "repair_overlap",
    label: "Repairs + overlap",
    description: "Cleans speech without changing meaning"
  },
  {
    id: "redaction_nonverbal",
    label: "Privacy + cues",
    description: "Redacts identifiers and keeps nonverbals"
  }
];

const runtimeEnvironment = (
  import.meta as ImportMeta & { readonly env?: { readonly VITE_API_BASE?: string } }
).env;
const API_BASE = runtimeEnvironment?.VITE_API_BASE ?? "http://127.0.0.1:8000";

export function canAcceptProfessorDemoRun(run: ProfessorDemoRun | null): boolean {
  if (
    run === null ||
    run.status !== "completed" ||
    !run.merged_transcript ||
    run.specialists.length !== 4 ||
    run.receipt.attempted_call_count !== 4 ||
    run.receipt.completed_call_count !== 4 ||
    run.receipt.valid_result_count !== 4 ||
    !run.receipt.accounting_complete
  ) {
    return false;
  }
  const specialistIds = new Set(run.specialists.map((item) => item.specialist_id));
  return (
    specialistIds.size === 4 &&
    PROFESSOR_DEMO_SPECIALISTS.every((item) => specialistIds.has(item.id)) &&
    run.specialists.every((item) => item.status === "valid" && item.schema_valid)
  );
}

export function formatProfessorDemoCost(value: string | null): string {
  if (value === null) {
    return "Unknown";
  }
  const amount = Number(value);
  if (!Number.isFinite(amount)) {
    return "Unknown";
  }
  return `$${amount.toFixed(6)}`;
}

export async function runProfessorDemo(transcript: string): Promise<ProfessorDemoRun> {
  return requestJson<ProfessorDemoRun>("/api/professor-demo/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source: "synthetic-demo",
      transcript
    })
  });
}

export async function acceptProfessorDemoRun(runId: string): Promise<ProfessorDemoRun> {
  return requestJson<ProfessorDemoRun>(
    `/api/professor-demo/runs/${encodeURIComponent(runId)}/accept`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirmation: "accept-generated-demo-revision" })
    }
  );
}

export async function revertProfessorDemoRun(runId: string): Promise<ProfessorDemoRun> {
  return requestJson<ProfessorDemoRun>(
    `/api/professor-demo/runs/${encodeURIComponent(runId)}/revert`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirmation: "restore-original-demo-revision" })
    }
  );
}

export async function loadLatestProfessorDemoRun(): Promise<ProfessorDemoRun | null> {
  const response = await fetch(`${API_BASE}/api/professor-demo/revisions/latest`);
  if (response.status === 404) {
    return null;
  }
  return parseResponse<ProfessorDemoRun>(response);
}

async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  return parseResponse<T>(response);
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (response.ok) {
    return response.json() as Promise<T>;
  }
  let message = "The professor demo request failed";
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      message = payload.detail;
    }
  } catch {
    // Keep the fixed safe message for non-JSON failures.
  }
  throw new Error(message);
}
