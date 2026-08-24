export const PILOT_JOB_CONFIRMATION = "authorize-four-specialists-per-chunk" as const;
export const PILOT_CANCEL_CONFIRMATION = "cancel-transcript-pilot-job" as const;
export const PILOT_COMMIT_CONFIRMATION = "create-supervised-child-revision" as const;
export const PILOT_RESTORE_CONFIRMATION = "restore-immutable-original" as const;

export type PilotClassification = "synthetic" | "authorized-deidentified";

export type PilotStudy = {
  study_id: string;
  id?: string;
  name: string;
  description?: string;
  researcher_id: string;
  researcher_name: string;
  researchers?: Array<{
    researcher_id: string;
    display_name: string;
    active?: boolean;
  }>;
  created_at?: string;
};

export type PilotRevision = {
  revision_id: string;
  parent_revision_id?: string | null;
  sha256?: string;
  created_at?: string;
  created_by?: string;
};

export type PilotSource = {
  source_id: string;
  id?: string;
  study_id: string;
  researcher_id: string;
  researcher_name?: string;
  filename: string;
  media_type?: string;
  source_filename?: string;
  source_media_type?: string;
  data_classification: PilotClassification;
  classification?: PilotClassification;
  line_count?: number;
  chunk_count?: number;
  byte_count?: number;
  source_sha256?: string;
  original_revision_id?: string;
  input_revision_id?: string;
  active_revision_id?: string;
  original_revision?: PilotRevision;
  active_revision?: PilotRevision;
  preview_text?: string;
  active_transcript?: string;
  created_at?: string;
};

export type PilotSpecialistId =
  | "speaker_turn"
  | "timing_pause"
  | "repair_overlap"
  | "redaction_nonverbal";

export type PilotSpecialistProgress = {
  specialist_id: PilotSpecialistId;
  label?: string;
  status: "pending" | "running" | "valid" | "error" | "cancelled";
  schema_valid?: boolean;
  completed_call_count?: number;
  expected_call_count?: number;
  error_code?: string | null;
  error_message?: string | null;
};

export type PilotProposalAction = "accept" | "keep_original" | "edit";

export type PilotProposal = {
  proposal_id: string;
  line_index: number;
  chunk_index?: number;
  original_text: string;
  proposed_text: string;
  changed: boolean;
  action?: PilotProposalAction | null;
  decision?: PilotProposalAction | null;
  edited_text?: string | null;
  final_text?: string | null;
  decision_version: number;
  decided_by?: string | null;
  decided_at?: string | null;
  specialist_contributions?: Record<string, unknown>;
  evidence?: Record<string, unknown>;
};

export type PilotUsageReceipt = {
  attempted_call_count?: number;
  completed_call_count?: number;
  valid_result_count?: number;
  expected_call_count?: number;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  reasoning_tokens?: number | null;
  total_tokens?: number | null;
  total_cost_usd?: string | number | null;
  known_cost_subtotal_usd?: string | number | null;
  accounting_complete?: boolean;
  model?: string;
  provider?: string;
  endpoint?: string;
  currency?: string;
};

export type PilotAuditEvent = {
  event_id?: string;
  event_type: string;
  actor_id?: string | null;
  occurred_at?: string;
  created_at?: string;
  summary?: string;
};

export type PilotJobProgress = {
  planned_call_count?: number;
  attempted_call_count?: number;
  completed_call_count?: number;
  valid_call_count?: number;
  completed_chunk_count?: number;
  changed_line_count?: number;
  reviewed_line_count?: number;
  unresolved_line_count?: number;
};

export type PilotJobCall = {
  specialist_id: PilotSpecialistId;
  status?: string;
  schema_valid?: boolean;
  error_code?: string | null;
  error_message?: string | null;
};

export type PilotJobChunk = {
  chunk_index?: number;
  calls: PilotJobCall[];
};

export type PilotJobStatus =
  | "queued"
  | "preflighting"
  | "running"
  | "cancelling"
  | "cancelled"
  | "review_ready"
  | "failed"
  | "committing"
  | "committed"
  | "accepted"
  | "restored"
  | "interrupted";

export type PilotJob = {
  job_id: string;
  source_id: string;
  study_id?: string;
  researcher_id: string;
  input_revision_id: string;
  status: PilotJobStatus;
  stage?: string;
  total_chunks?: number;
  completed_chunks?: number;
  chunk_count?: number;
  progress?: PilotJobProgress;
  usage?: Record<string, unknown>;
  preflight?: Record<string, unknown>;
  provenance?: Record<string, unknown>;
  chunks?: PilotJobChunk[];
  source?: {
    source_id?: string;
    original_revision_id?: string;
    active_revision_id?: string;
  };
  specialists?: PilotSpecialistProgress[];
  proposals: PilotProposal[];
  receipt?: PilotUsageReceipt | null;
  audit_events?: PilotAuditEvent[];
  committed_revision?: PilotRevision | null;
  committed_revision_id?: string | null;
  active_revision_id?: string | null;
  failure_code?: string | null;
  failure_message?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type PilotDecisionResponse = {
  job?: PilotJob;
  proposal?: PilotProposal;
};

export const PILOT_SPECIALISTS: ReadonlyArray<{
  id: PilotSpecialistId;
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
    description: "Reads explicit timing and pause evidence"
  },
  {
    id: "repair_overlap",
    label: "Repairs + overlap",
    description: "Cleans speech while preserving meaning"
  },
  {
    id: "redaction_nonverbal",
    label: "Privacy + cues",
    description: "Finds identifiers and retains nonverbals"
  }
];

const runtimeEnvironment = (
  import.meta as ImportMeta & { readonly env?: { readonly VITE_API_BASE?: string } }
).env;

export const TRANSCRIPT_PILOT_API_BASE =
  runtimeEnvironment?.VITE_API_BASE ?? "http://127.0.0.1:8000";

export function pilotStudyId(study: PilotStudy): string {
  return study.study_id || study.id || "";
}

export function pilotSourceId(source: PilotSource): string {
  return source.source_id || source.id || "";
}

export function pilotSourceInputRevisionId(source: PilotSource): string {
  return (
    source.active_revision_id ||
    source.active_revision?.revision_id ||
    source.input_revision_id ||
    source.original_revision_id ||
    source.original_revision?.revision_id ||
    ""
  );
}

export function pilotSourceClassification(source: PilotSource): PilotClassification {
  return source.data_classification || source.classification || "synthetic";
}

export function proposalAction(proposal: PilotProposal): PilotProposalAction | null {
  return proposal.action ?? proposal.decision ?? null;
}

export function isPilotJobActive(job: PilotJob | null): boolean {
  return Boolean(
    job && ["queued", "preflighting", "running", "cancelling", "committing"].includes(job.status)
  );
}

export function isPilotJobReviewable(job: PilotJob | null): boolean {
  return Boolean(job && job.status === "review_ready" && job.proposals.length > 0);
}

export function isPilotJobCommitted(job: PilotJob | null): boolean {
  return Boolean(job && ["committed", "accepted", "restored"].includes(job.status));
}

export function pilotReviewCounts(job: PilotJob | null): {
  total: number;
  unchanged: number;
  unresolved: number;
  accepted: number;
  kept: number;
  edited: number;
} {
  const proposals = job?.proposals ?? [];
  let accepted = 0;
  let kept = 0;
  let edited = 0;
  let unchanged = 0;
  for (const proposal of proposals) {
    if (!proposal.changed) {
      unchanged += 1;
      continue;
    }
    const action = proposalAction(proposal);
    if (action === "accept") accepted += 1;
    if (action === "keep_original") kept += 1;
    if (action === "edit") edited += 1;
  }
  return {
    total: proposals.length,
    unchanged,
    unresolved: proposals.length - unchanged - accepted - kept - edited,
    accepted,
    kept,
    edited
  };
}

export function canCommitPilotJob(job: PilotJob | null, pendingDecisionCount = 0): boolean {
  if (!isPilotJobReviewable(job) || !job || pendingDecisionCount > 0) {
    return false;
  }
  const counts = pilotReviewCounts(job);
  const receipt = job.receipt;
  const specialists = job.specialists ?? [];
  const expectedCallCount =
    receipt?.expected_call_count ?? Math.max(1, job.total_chunks ?? 1) * PILOT_SPECIALISTS.length;
  const specialistEvidenceIsValid =
    specialists.length === 0 ||
    PILOT_SPECIALISTS.every((definition) =>
      specialists.some(
        (specialist) =>
          specialist.specialist_id === definition.id &&
          (specialist.status === "valid" || specialist.schema_valid === true) &&
          specialist.schema_valid !== false
      )
    );
  return (
    counts.total > 0 &&
    counts.unresolved === 0 &&
    Boolean(receipt?.accounting_complete) &&
    expectedCallCount > 0 &&
    (receipt?.attempted_call_count ?? 0) === expectedCallCount &&
    (receipt?.completed_call_count ?? 0) === expectedCallCount &&
    (receipt?.valid_result_count ?? 0) === expectedCallCount &&
    specialistEvidenceIsValid &&
    pilotJobCreatesRevision(job)
  );
}

export function pilotJobCreatesRevision(job: PilotJob | null): boolean {
  return Boolean(
    job?.proposals.some((proposal) => {
      if (!proposal.changed) return false;
      const action = proposalAction(proposal);
      if (action === "accept") {
        return proposal.proposed_text !== proposal.original_text;
      }
      if (action === "edit") {
        return (proposal.edited_text ?? "").trim() !== proposal.original_text;
      }
      return false;
    })
  );
}

export function formatPilotCost(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "Unknown";
  const amount = Number(value);
  return Number.isFinite(amount) ? `$${amount.toFixed(6)}` : "Unknown";
}

export function formatPilotNumber(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? new Intl.NumberFormat("en-US").format(value)
    : "Unknown";
}

export function pilotExportUrl(jobId: string, filename: "transcript.txt" | "receipt.json"): string {
  return `${TRANSCRIPT_PILOT_API_BASE}/api/transcript-pilot/jobs/${encodeURIComponent(jobId)}/exports/${filename}`;
}

export async function listPilotStudies(): Promise<PilotStudy[]> {
  const payload = await requestPayload("/api/transcript-pilot/studies");
  return normalizeList<unknown>(payload, "studies").map(normalizePilotStudy);
}

export async function createPilotStudy(params: {
  name: string;
  description: string;
  researcher_id: string;
  researcher_name: string;
  study_id?: string;
}): Promise<PilotStudy> {
  const payload = await requestPayload("/api/transcript-pilot/studies", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params)
  });
  if (isRecord(payload) && isRecord(payload.study)) {
    return normalizePilotStudy(
      isRecord(payload.researcher)
        ? { ...payload.study, researcher: payload.researcher }
        : payload.study
    );
  }
  return normalizePilotStudy(normalizeItem<unknown>(payload, "study"));
}

export async function listPilotSources(studyId: string): Promise<PilotSource[]> {
  const payload = await requestPayload(
    `/api/transcript-pilot/studies/${encodeURIComponent(studyId)}/sources`
  );
  return normalizeList<unknown>(payload, "sources").map(normalizePilotSource);
}

export async function uploadPilotSource(
  studyId: string,
  params: {
    file: File;
    data_classification: PilotClassification;
    researcher_id: string;
    authorization_basis: string;
  }
): Promise<PilotSource> {
  const form = new FormData();
  form.set("file", params.file);
  form.set("researcher_id", params.researcher_id);
  form.set("data_classification", params.data_classification);
  form.set("authorization_basis", params.authorization_basis);
  form.set("remote_egress_authorized", "true");
  form.set("contains_direct_identifiers", "false");
  form.set("protocol_version", "research-transcript-v1");
  const payload = await requestPayload(
    `/api/transcript-pilot/studies/${encodeURIComponent(studyId)}/sources`,
    { method: "POST", body: form }
  );
  return normalizePilotSource(normalizeItem<unknown>(payload, "source"));
}

export async function getPilotSource(sourceId: string): Promise<PilotSource> {
  const payload = await requestPayload(
    `/api/transcript-pilot/sources/${encodeURIComponent(sourceId)}`
  );
  return normalizePilotSource(normalizeItem<unknown>(payload, "source"));
}

export async function startPilotJob(
  sourceId: string,
  params: {
    researcher_id: string;
    input_revision_id: string;
    idempotency_key: string;
    authorized_cost_usd: string;
    confirmation: typeof PILOT_JOB_CONFIRMATION;
  }
): Promise<PilotJob> {
  const payload = await requestPayload(
    `/api/transcript-pilot/sources/${encodeURIComponent(sourceId)}/jobs`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params)
    }
  );
  return normalizePilotJob(normalizeItem<unknown>(payload, "job"));
}

export async function getPilotJob(jobId: string): Promise<PilotJob> {
  const payload = await requestPayload(
    `/api/transcript-pilot/jobs/${encodeURIComponent(jobId)}`
  );
  return normalizePilotJob(normalizeItem<unknown>(payload, "job"));
}

export async function cancelPilotJob(
  jobId: string,
  params: {
    researcher_id: string;
    confirmation: typeof PILOT_CANCEL_CONFIRMATION;
  }
): Promise<PilotJob> {
  const payload = await requestPayload(
    `/api/transcript-pilot/jobs/${encodeURIComponent(jobId)}/cancel`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params)
    }
  );
  return normalizePilotJob(normalizeItem<unknown>(payload, "job"));
}

export async function savePilotProposalDecision(
  jobId: string,
  proposalId: string,
  params: {
    researcher_id: string;
    action: PilotProposalAction;
    edited_text: string;
    expected_decision_version: number;
  }
): Promise<PilotDecisionResponse> {
  const payload = await requestPayload(
    `/api/transcript-pilot/jobs/${encodeURIComponent(jobId)}/proposals/${encodeURIComponent(proposalId)}/decision`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params)
    }
  );
  if (isRecord(payload) && "job" in payload) {
    return {
      job: normalizePilotJob(payload.job),
      proposal: "proposal" in payload ? normalizePilotProposal(payload.proposal) : undefined
    };
  }
  if (isRecord(payload) && "proposal" in payload) {
    return { proposal: normalizePilotProposal(payload.proposal) };
  }
  if (isRecord(payload) && "job_id" in payload) {
    return { job: normalizePilotJob(payload) };
  }
  return { proposal: normalizePilotProposal(payload) };
}

export async function commitPilotJob(
  jobId: string,
  params: {
    researcher_id: string;
    expected_active_revision_id: string;
    confirmation: typeof PILOT_COMMIT_CONFIRMATION;
  }
): Promise<PilotJob> {
  const payload = await requestPayload(
    `/api/transcript-pilot/jobs/${encodeURIComponent(jobId)}/commit`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params)
    }
  );
  return normalizePilotJob(normalizeItem<unknown>(payload, "job"));
}

export async function restorePilotSourceOriginal(
  sourceId: string,
  params: {
    researcher_id: string;
    expected_active_revision_id: string;
    confirmation: typeof PILOT_RESTORE_CONFIRMATION;
  }
): Promise<PilotSource> {
  const payload = await requestPayload(
    `/api/transcript-pilot/sources/${encodeURIComponent(sourceId)}/restore-original`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params)
    }
  );
  return normalizePilotSource(normalizeItem<unknown>(payload, "source"));
}

async function requestPayload(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(`${TRANSCRIPT_PILOT_API_BASE}${path}`, init);
  if (response.ok) {
    if (response.status === 204) return {};
    return response.json() as Promise<unknown>;
  }
  let message = "The transcript pilot request failed";
  try {
    const payload = (await response.json()) as { detail?: unknown; message?: unknown };
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      message = payload.detail;
    } else if (typeof payload.message === "string" && payload.message.trim()) {
      message = payload.message;
    } else if (isRecord(payload.detail) && typeof payload.detail.message === "string") {
      message = payload.detail.message;
    }
  } catch {
    // Keep a fixed safe message for non-JSON provider and proxy failures.
  }
  throw new Error(message);
}

function normalizeList<T>(payload: unknown, key: string): T[] {
  if (Array.isArray(payload)) return payload as T[];
  if (isRecord(payload) && Array.isArray(payload[key])) return payload[key] as T[];
  return [];
}

function normalizeItem<T>(payload: unknown, key: string): T {
  if (isRecord(payload) && key in payload) return payload[key] as T;
  return payload as T;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function normalizePilotStudy(value: unknown): PilotStudy {
  const raw = isRecord(value) ? value : {};
  const researcher = isRecord(raw.researcher) ? raw.researcher : {};
  const researchers = Array.isArray(raw.researchers)
    ? raw.researchers
        .filter(isRecord)
        .map((item) => ({
          researcher_id: stringValue(item.researcher_id),
          display_name: stringValue(item.display_name ?? item.researcher_name),
          active: typeof item.active === "boolean" ? item.active : undefined
        }))
    : [];
  const selectedResearcher =
    researchers.find((item) => item.active !== false) ?? researchers[0];
  return {
    ...(raw as unknown as PilotStudy),
    study_id: stringValue(raw.study_id ?? raw.id),
    name: stringValue(raw.name, "Untitled pilot study"),
    description: stringValue(raw.description),
    researcher_id: stringValue(
      raw.researcher_id ?? researcher.researcher_id ?? selectedResearcher?.researcher_id
    ),
    researcher_name: stringValue(
      raw.researcher_name ??
        researcher.researcher_name ??
        researcher.display_name ??
        selectedResearcher?.display_name
    ),
    researchers
  };
}

export function normalizePilotSource(value: unknown): PilotSource {
  const raw = isRecord(value) ? value : {};
  return {
    ...(raw as unknown as PilotSource),
    source_id: stringValue(raw.source_id ?? raw.id),
    study_id: stringValue(raw.study_id),
    researcher_id: stringValue(raw.researcher_id),
    researcher_name: stringValue(raw.researcher_name),
    filename: stringValue(
      raw.filename ?? raw.source_filename ?? raw.original_filename,
      "Transcript source"
    ),
    source_filename: stringValue(raw.source_filename ?? raw.filename),
    media_type: stringValue(raw.media_type ?? raw.source_media_type),
    source_media_type: stringValue(raw.source_media_type ?? raw.media_type),
    data_classification: classificationValue(
      raw.data_classification ?? raw.classification
    ),
    source_sha256: stringValue(raw.source_sha256 ?? raw.source_blob_sha256),
    original_revision_id: stringValue(
      raw.original_revision_id ??
        (isRecord(raw.original_revision) ? raw.original_revision.revision_id : undefined)
    ),
    active_revision_id: stringValue(
      raw.active_revision_id ??
        (isRecord(raw.active_revision) ? raw.active_revision.revision_id : undefined)
    ),
    active_transcript: stringValue(raw.active_transcript ?? raw.preview_text),
    preview_text: stringValue(raw.preview_text ?? raw.active_transcript)
  };
}

function normalizePilotProposal(value: unknown): PilotProposal {
  const raw = isRecord(value) ? value : {};
  const decision = isRecord(raw.decision) ? raw.decision : {};
  const action = proposalActionValue(raw.action ?? decision.action ?? raw.decision);
  return {
    ...(raw as unknown as PilotProposal),
    proposal_id: stringValue(raw.proposal_id ?? raw.id),
    line_index: numberValue(raw.line_index),
    original_text: stringValue(raw.original_text ?? raw.source_text),
    proposed_text: stringValue(raw.proposed_text ?? raw.candidate_text),
    changed:
      typeof raw.changed === "boolean"
        ? raw.changed
        : stringValue(raw.original_text ?? raw.source_text) !==
          stringValue(raw.proposed_text ?? raw.candidate_text),
    action,
    edited_text: nullableString(raw.edited_text ?? decision.edited_text),
    final_text: nullableString(raw.final_text ?? decision.final_text),
    decision_version: numberValue(raw.decision_version ?? decision.version),
    specialist_contributions: isRecord(raw.specialist_contributions)
      ? raw.specialist_contributions
      : isRecord(raw.agent_evidence)
        ? raw.agent_evidence
        : undefined,
    evidence: isRecord(raw.evidence) ? raw.evidence : undefined
  };
}

export function normalizePilotJob(value: unknown): PilotJob {
  const raw = isRecord(value) ? value : {};
  const proposalsValue = raw.proposals ?? raw.line_proposals;
  const auditValue = raw.audit_events ?? raw.events;
  const progress = isRecord(raw.progress) ? raw.progress : {};
  const usage = isRecord(raw.usage) ? raw.usage : {};
  const preflight = isRecord(raw.preflight) ? raw.preflight : {};
  const provenance = isRecord(raw.provenance) ? raw.provenance : {};
  const existingReceipt = isRecord(raw.receipt) ? raw.receipt : {};
  const chunks = Array.isArray(raw.chunks)
    ? raw.chunks.map(normalizePilotChunk)
    : [];
  const status = jobStatusValue(raw.status);
  const totalChunks = numberValue(raw.chunk_count ?? raw.total_chunks) || chunks.length;
  const source = isRecord(raw.source) ? raw.source : {};
  const inputRevisionId = stringValue(
    raw.input_revision_id ?? source.input_revision_id ?? source.active_revision_id
  );
  const activeRevisionId = stringValue(
    source.active_revision_id ?? raw.active_revision_id
  );
  const committedRevisionId = nullableString(raw.committed_revision_id);
  const receipt: PilotUsageReceipt = {
    ...(existingReceipt as unknown as PilotUsageReceipt),
    ...(usage as unknown as PilotUsageReceipt),
    expected_call_count: numberValue(
      progress.planned_call_count ?? existingReceipt.expected_call_count
    ),
    attempted_call_count: numberValue(
      progress.attempted_call_count ?? existingReceipt.attempted_call_count
    ),
    completed_call_count: numberValue(
      progress.completed_call_count ?? existingReceipt.completed_call_count
    ),
    valid_result_count: numberValue(
      progress.valid_call_count ?? existingReceipt.valid_result_count
    ),
    prompt_tokens: optionalNumber(usage.prompt_tokens ?? existingReceipt.prompt_tokens),
    completion_tokens: optionalNumber(
      usage.completion_tokens ?? existingReceipt.completion_tokens
    ),
    reasoning_tokens: optionalNumber(
      usage.reasoning_tokens ?? existingReceipt.reasoning_tokens
    ),
    total_tokens: optionalNumber(usage.total_tokens ?? existingReceipt.total_tokens),
    total_cost_usd: costValue(
      usage.total_cost_usd ??
        usage.actual_cost_usd ??
        existingReceipt.total_cost_usd
    ),
    known_cost_subtotal_usd: costValue(
      usage.known_cost_subtotal_usd ?? existingReceipt.known_cost_subtotal_usd
    ),
    accounting_complete: booleanValue(
      usage.accounting_complete ?? existingReceipt.accounting_complete
    ),
    model: stringValue(
      provenance.model ??
        provenance.model_id ??
        preflight.model ??
        preflight.model_id ??
        existingReceipt.model
    ),
    provider: stringValue(
      provenance.provider ?? preflight.provider ?? existingReceipt.provider
    ),
    endpoint: stringValue(
      provenance.endpoint ??
        provenance.endpoint_tag ??
        preflight.endpoint ??
        preflight.endpoint_tag ??
        existingReceipt.endpoint
    )
  };
  return {
    ...(raw as unknown as PilotJob),
    job_id: stringValue(raw.job_id ?? raw.id),
    source_id: stringValue(raw.source_id),
    researcher_id: stringValue(raw.researcher_id),
    input_revision_id: inputRevisionId,
    status,
    stage: stringValue(raw.stage),
    chunk_count: totalChunks,
    total_chunks: totalChunks,
    completed_chunks: numberValue(
      progress.completed_chunk_count ?? raw.completed_chunks
    ),
    progress: progress as unknown as PilotJobProgress,
    usage,
    preflight,
    provenance,
    chunks,
    source: {
      source_id: stringValue(source.source_id ?? raw.source_id),
      original_revision_id: stringValue(source.original_revision_id),
      active_revision_id: activeRevisionId
    },
    proposals: Array.isArray(proposalsValue)
      ? proposalsValue.map(normalizePilotProposal)
      : [],
    specialists: aggregateSpecialistProgress(chunks, status, Math.max(1, totalChunks)),
    receipt,
    audit_events: Array.isArray(auditValue)
      ? auditValue.map(normalizeAuditEvent)
      : [],
    active_revision_id: activeRevisionId,
    committed_revision_id: committedRevisionId,
    committed_revision: committedRevisionId
      ? {
          revision_id: committedRevisionId,
          parent_revision_id: inputRevisionId
        }
      : isRecord(raw.committed_revision)
        ? (raw.committed_revision as unknown as PilotRevision)
        : null,
    failure_code: nullableString(raw.error_code ?? raw.failure_code),
    failure_message: nullableString(raw.error_message ?? raw.failure_message),
    error_code: nullableString(raw.error_code),
    error_message: nullableString(raw.error_message)
  };
}

function normalizePilotChunk(value: unknown): PilotJobChunk {
  const raw = isRecord(value) ? value : {};
  return {
    ...(raw as unknown as PilotJobChunk),
    chunk_index: numberValue(raw.chunk_index),
    calls: Array.isArray(raw.calls)
      ? raw.calls.map((call) => {
          const callRecord = isRecord(call) ? call : {};
          return {
            ...(callRecord as unknown as PilotJobCall),
            specialist_id: stringValue(callRecord.specialist_id) as PilotSpecialistId,
            status: stringValue(callRecord.status),
            schema_valid: booleanOrUndefined(callRecord.schema_valid),
            error_code: nullableString(callRecord.error_code),
            error_message: nullableString(callRecord.error_message)
          };
        })
      : []
  };
}

function aggregateSpecialistProgress(
  chunks: PilotJobChunk[],
  jobStatus: PilotJobStatus,
  expectedCallCount: number
): PilotSpecialistProgress[] {
  const calls = chunks.flatMap((chunk) => chunk.calls);
  if (calls.length === 0) return [];
  return PILOT_SPECIALISTS.map((definition) => {
    const specialistCalls = calls.filter(
      (call) => call.specialist_id === definition.id
    );
    const failedCall = specialistCalls.find(
      (call) =>
        call.status === "failed" ||
        call.status === "error" ||
        Boolean(call.error_code) ||
        call.schema_valid === false
    );
    const validCount = specialistCalls.filter(
      (call) =>
        (call.status === "valid" ||
          call.status === "completed" ||
          call.status === "succeeded") &&
        call.schema_valid !== false
    ).length;
    const status: PilotSpecialistProgress["status"] = failedCall
      ? "error"
      : validCount >= expectedCallCount
        ? "valid"
        : ["queued", "preflighting"].includes(jobStatus)
          ? "pending"
          : jobStatus === "cancelled"
            ? "cancelled"
            : "running";
    return {
      specialist_id: definition.id,
      label: definition.label,
      status,
      schema_valid: status === "valid" ? true : failedCall ? false : undefined,
      completed_call_count: specialistCalls.filter((call) =>
        ["valid", "completed", "succeeded", "failed", "error"].includes(
          call.status ?? ""
        )
      ).length,
      expected_call_count: expectedCallCount,
      error_code: failedCall?.error_code ?? null,
      error_message: failedCall?.error_message ?? null
    };
  });
}

function normalizeAuditEvent(value: unknown): PilotAuditEvent {
  const raw = isRecord(value) ? value : {};
  return {
    ...(raw as unknown as PilotAuditEvent),
    event_type: stringValue(raw.event_type ?? raw.type ?? raw.kind, "audit_event"),
    summary: stringValue(raw.summary ?? raw.message),
    actor_id: nullableString(raw.actor_id ?? raw.researcher_id),
    occurred_at: stringValue(raw.occurred_at ?? raw.created_at)
  };
}

function stringValue(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function nullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function numberValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function optionalNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function booleanValue(value: unknown): boolean {
  return value === true;
}

function booleanOrUndefined(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function costValue(value: unknown): string | number | null {
  return typeof value === "string" || typeof value === "number" ? value : null;
}

function classificationValue(value: unknown): PilotClassification {
  return value === "authorized-deidentified" ? value : "synthetic";
}

function proposalActionValue(value: unknown): PilotProposalAction | null {
  return value === "accept" || value === "keep_original" || value === "edit"
    ? value
    : null;
}

function jobStatusValue(value: unknown): PilotJobStatus {
  const aliases: Record<string, PilotJobStatus> = {
    preflight: "preflighting",
    cancel_requested: "cancelling",
    ready_for_review: "review_ready",
    needs_review: "review_ready",
    needs_attention: "interrupted",
    completed: "review_ready",
    succeeded: "review_ready"
  };
  if (typeof value === "string" && aliases[value]) return aliases[value];
  const statuses: PilotJobStatus[] = [
    "queued",
    "preflighting",
    "running",
    "cancelling",
    "cancelled",
    "review_ready",
    "failed",
    "committing",
    "committed",
    "accepted",
    "restored",
    "interrupted"
  ];
  return typeof value === "string" && statuses.includes(value as PilotJobStatus)
    ? (value as PilotJobStatus)
    : "failed";
}
