import {
  Activity,
  AlertTriangle,
  Braces,
  Database,
  FileCheck2,
  FileText,
  FileUp,
  FlaskConical,
  Loader2,
  Play,
  Download,
  Sparkles,
  ShieldCheck,
  TableProperties
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  apiUrl,
  addStudySkillPackVersion,
  analyzeSegmentationRun,
  createAnalysisRun,
  createAgentJobEvidence,
  createPluginBuildJob,
  createPluginRequest,
  createSegmentationCorpusRun,
  createSegmentationFileRun,
  createSegmentationRun,
  createSegmentationRunRewriteJob,
  createSegmentationRewriteJob,
  createStudy,
  createStudyFileBatch,
  createStudyTextBatch,
  createTextAnalysisRun,
  draftSkillPack,
  evaluateSegmentationDraft,
  getDeploymentProfile,
  getSegmentationCase,
  getSegmentationRulebook,
  getStudyBatch,
  getStudyBatchRun,
  getStudySchema,
  listSegmentationCases,
  listStudyBatchRuns,
  listStudyBatches,
  listAgentJobs,
  listMetricPlugins,
  listPluginRequests,
  listRuns,
  listStudies,
  listSegmentationCorpusRuns,
  listSegmentationRuns,
  loadSkillPack,
  refineSkillPack,
  verifySegmentationRun,
  updateStudySchema,
  updateAgentJobStatus,
  validateSkillPack,
  validateSkillPackText
} from "./api";
import {
  createBatchTranscriptFromFilePreview,
  createBatchTranscriptFromTextFile,
  isSupportedBatchTranscriptFile,
  parseBatchTranscriptText,
  serializeBatchTranscriptText,
  updateBatchTranscriptMetadata
} from "./batchTranscripts";
import { exportCasebookCsv, parseCasebookCsv } from "./casebookCsv";
import {
  CASEBOOK_TEMPLATES,
  MAX_STUDY_PARTICIPANTS,
  buildCasebookOptions,
  casebookRequestFromControls,
  schemaControlsFromStudySchema,
  validateBatchAssignments,
  type CasebookOptions
} from "./casebookDesign";
import { buildMetricMatrix } from "./matrixView";
import { privacyModeLabel } from "./privacyMode";
import { segmentationSourceLabel } from "./segmentationProvenance";
import type {
  AgentJob,
  BatchTranscript,
  CUnitAdjudication,
  CUnitRulebookSummary,
  MetricId,
  MetricResult,
  PluginRequest,
  RunHistoryItem,
  RunResponse,
  SegmentationCase,
  SegmentationCorpusRun,
  SegmentationEvaluation,
  SegmentationRun,
  SegmentationSource,
  SkillPack,
  StudyBatchResponse,
  StudyBatchRunDetail,
  StudyBatchRunSummary,
  StudyBatchSummary,
  StudyWorkspace
} from "./types";
import type { MetricPlugin } from "./types";

const metricLabels: Record<string, string> = {
  base_metrics: "Base metrics",
  lexical_metrics: "Lexical metrics",
  disfluency_metrics: "Disfluencies",
  concept_count_metrics: "Concept counts",
  cue_inventory_metrics: "Cue inventory",
  care_plan_commitment_metrics: "Care plan commitments",
  question_type_metrics: "Question types"
};

const metricDescriptions: Record<string, string> = {
  base_metrics: "Turn structure",
  lexical_metrics: "Lexical profile",
  disfluency_metrics: "Speech markers",
  concept_count_metrics: "Research lexicon",
  cue_inventory_metrics: "Nonverbal coding",
  care_plan_commitment_metrics: "Care coordination",
  question_type_metrics: "Prompting style"
};

const orderedMetrics: MetricId[] = [
  "base_metrics",
  "lexical_metrics",
  "disfluency_metrics"
];

const DEFAULT_OPENROUTER_MODEL = "openai/gpt-oss-120b";
const DEFAULT_BATCH_TRANSCRIPT_TEXT =
  "participant_001.txt | participant_id=P1 | condition=home | week=week_1\n" +
  "P1_c: How did walking feel today?\nP1_p: It hurt after lunch.\n---\n" +
  "participant_002.txt | participant_id=P2 | condition=lab | week=week_1\n" +
  "P2_c: Did medication help last night?\nP2_p: Yes, I slept better.\n---\n" +
  "participant_003.txt | participant_id=P3 | condition=home | week=week_2\n" +
  "P3_c: I will call the clinic tomorrow.\nP3_p: Thank you.";

const DEFAULT_SEGMENTATION_RULE_IDS = [
  "speaker-markers",
  "timestamp-markers",
  "pause-markers",
  "filled-pauses"
];

function metadataBySourceFilename(
  transcripts: BatchTranscript[]
): Record<string, Record<string, string>> {
  return Object.fromEntries(
    transcripts.map((transcript) => [
      transcript.source_filename,
      transcript.metadata ?? {}
    ])
  );
}

export function App() {
  const [skillPack, setSkillPack] = useState<SkillPack | null>(null);
  const [skillPackPayload, setSkillPackPayload] = useState<unknown | null>(null);
  const [skillPackJson, setSkillPackJson] = useState("");
  const [skillPackStatus, setSkillPackStatus] = useState("");
  const [studyBrief, setStudyBrief] = useState(
    "Caregiver participant healthcare study. Track pain, medication, walking and balance. Nonverbal cues include pause and laughter."
  );
  const [draftName, setDraftName] = useState("Caregiver Mobility Study");
  const [draftWarnings, setDraftWarnings] = useState<string[]>([]);
  const [refinementInstruction, setRefinementInstruction] = useState(
    "Split pain into acute and chronic pain, and add sleep disruption."
  );
  const [appliedChanges, setAppliedChanges] = useState<string[]>([]);
  const [authoringEngine, setAuthoringEngine] = useState<"local" | "openrouter">("local");
  const [authoringModel, setAuthoringModel] = useState(DEFAULT_OPENROUTER_MODEL);
  const [inputMode, setInputMode] = useState<"file" | "paste">("file");
  const [file, setFile] = useState<File | null>(null);
  const [pastedTranscript, setPastedTranscript] = useState("");
  const [participantId, setParticipantId] = useState("vr001");
  const [caregiverPrefix, setCaregiverPrefix] = useState("vr001_c");
  const [participantPrefix, setParticipantPrefix] = useState("vr001_p");
  const [selectedMetrics, setSelectedMetrics] = useState<MetricId[]>([
    "base_metrics",
    "lexical_metrics",
    "disfluency_metrics"
  ]);
  const [disfluencyText, setDisfluencyText] = useState("");
  const [run, setRun] = useState<RunResponse | null>(null);
  const [runHistory, setRunHistory] = useState<RunHistoryItem[]>([]);
  const [metricPlugins, setMetricPlugins] = useState<MetricPlugin[]>([]);
  const [pluginRequests, setPluginRequests] = useState<PluginRequest[]>([]);
  const [agentJobs, setAgentJobs] = useState<AgentJob[]>([]);
  const [pluginRequestStatus, setPluginRequestStatus] = useState("");
  const [pluginJobStatus, setPluginJobStatus] = useState("");
  const [pluginRequestTitle, setPluginRequestTitle] = useState("Empathy Response Metric");
  const [pluginRequestQuestion, setPluginRequestQuestion] = useState(
    "Count caregiver turns that acknowledge participant distress or difficulty."
  );
  const [pluginRequestColumns, setPluginRequestColumns] = useState(
    "speaker, empathy_count, examples"
  );
  const [pluginRequestExample, setPluginRequestExample] = useState(
    "CG: That sounds really hard.\nP: It was difficult."
  );
  const [pluginRequestExpected, setPluginRequestExpected] = useState(
    "Count the caregiver turn as one empathy response."
  );
  const [studies, setStudies] = useState<StudyWorkspace[]>([]);
  const [selectedStudyId, setSelectedStudyId] = useState("");
  const [studyName, setStudyName] = useState("Demo Healthcare Batch");
  const [studyDescription, setStudyDescription] = useState(
    "Three-transcript demo workspace for aggregate prompting and care-plan metrics."
  );
  const [casebookParticipantCount, setCasebookParticipantCount] = useState(3);
  const [casebookConditions, setCasebookConditions] = useState("home, lab");
  const [casebookWeekCount, setCasebookWeekCount] = useState(2);
  const [casebookCustomFields, setCasebookCustomFields] = useState("site, study_arm");
  const [batchTranscriptText, setBatchTranscriptText] = useState(DEFAULT_BATCH_TRANSCRIPT_TEXT);
  const [batchTranscripts, setBatchTranscripts] = useState<BatchTranscript[]>(() =>
    parseBatchTranscriptText(DEFAULT_BATCH_TRANSCRIPT_TEXT)
  );
  const [batchFiles, setBatchFiles] = useState<File[]>([]);
  const [batchParseError, setBatchParseError] = useState("");
  const [batchUploadStatus, setBatchUploadStatus] = useState("");
  const [studyBatches, setStudyBatches] = useState<StudyBatchSummary[]>([]);
  const [studyBatch, setStudyBatch] = useState<StudyBatchResponse | null>(null);
  const [batchRuns, setBatchRuns] = useState<StudyBatchRunSummary[]>([]);
  const [selectedBatchRun, setSelectedBatchRun] = useState<StudyBatchRunDetail | null>(null);
  const [studyWorkspaceStatus, setStudyWorkspaceStatus] = useState("");
  const [isStudyWorkspaceOpen, setIsStudyWorkspaceOpen] = useState(false);
  const [segmentationCases, setSegmentationCases] = useState<SegmentationCase[]>([]);
  const [selectedSegmentationCaseId, setSelectedSegmentationCaseId] = useState("");
  const [selectedSegmentationCase, setSelectedSegmentationCase] =
    useState<SegmentationCase | null>(null);
  const [segmentationDraft, setSegmentationDraft] = useState("");
  const [segmentationEvaluation, setSegmentationEvaluation] =
    useState<SegmentationEvaluation | null>(null);
  const [segmentationRunSource, setSegmentationRunSource] = useState("");
  const [segmentationRunFile, setSegmentationRunFile] = useState<File | null>(null);
  const [segmentationRunSourceType, setSegmentationRunSourceType] =
    useState<SegmentationSource>("researcher_provided");
  const [segmentationRun, setSegmentationRun] = useState<SegmentationRun | null>(null);
  const [segmentationAnalysisRun, setSegmentationAnalysisRun] =
    useState<RunResponse | null>(null);
  const [segmentationRuns, setSegmentationRuns] = useState<SegmentationRun[]>([]);
  const [segmentationCorpusSeed, setSegmentationCorpusSeed] = useState(0);
  const [segmentationCorpusRuns, setSegmentationCorpusRuns] = useState<
    SegmentationCorpusRun[]
  >([]);
  const [segmentationRulebook, setSegmentationRulebook] =
    useState<CUnitRulebookSummary | null>(null);
  const [segmentationStatus, setSegmentationStatus] = useState("");
  const [privacyMode, setPrivacyMode] = useState("Checking mode");
  const [error, setError] = useState("");
  const [isRunning, setIsRunning] = useState(false);

  const casebookOptions = useMemo(
    () =>
      buildCasebookOptions(
        casebookParticipantCount,
        casebookConditions,
        casebookWeekCount
      ),
    [casebookConditions, casebookParticipantCount, casebookWeekCount]
  );
  const casebookWarnings = useMemo(
    () => validateBatchAssignments(batchTranscripts, casebookOptions),
    [batchTranscripts, casebookOptions]
  );

  useEffect(() => {
    loadSkillPack()
      .then((pack) => {
        setSkillPack(pack);
        setSkillPackPayload(pack);
        setSkillPackJson(JSON.stringify(pack, null, 2));
        setDisfluencyText(pack.disfluency_tokens.join(", "));
      })
      .catch((err: Error) => setError(err.message));
    listRuns()
      .then(setRunHistory)
      .catch(() => setRunHistory([]));
    listMetricPlugins()
      .then(setMetricPlugins)
      .catch(() => setMetricPlugins([]));
    listPluginRequests()
      .then(setPluginRequests)
      .catch(() => setPluginRequests([]));
    listAgentJobs()
      .then(setAgentJobs)
      .catch(() => setAgentJobs([]));
    listStudies()
      .then(setStudies)
      .catch(() => setStudies([]));
    listSegmentationRuns()
      .then(setSegmentationRuns)
      .catch(() => setSegmentationRuns([]));
    listSegmentationCorpusRuns()
      .then(setSegmentationCorpusRuns)
      .catch(() => setSegmentationCorpusRuns([]));
    getSegmentationRulebook()
      .then(setSegmentationRulebook)
      .catch(() => setSegmentationRulebook(null));
    getDeploymentProfile()
      .then((profile) => setPrivacyMode(privacyModeLabel(profile)))
      .catch(() => setPrivacyMode("Status unavailable"));
    listSegmentationCases()
      .then((cases) => {
        setSegmentationCases(cases);
        if (cases[0]) {
          setSelectedSegmentationCaseId(cases[0].case_id);
          setSelectedSegmentationCase(cases[0]);
          setSegmentationDraft(cases[0].gold_text);
          setSegmentationRunSource(cases[0].descript_text);
          setSegmentationRunSourceType("synthetic");
        }
      })
      .catch(() => setSegmentationStatus("Could not load segmentation cases."));
  }, []);

  const disfluencyTokens = useMemo(
    () =>
      disfluencyText
        .split(",")
        .map((token) => token.trim().toLowerCase())
        .filter(Boolean),
    [disfluencyText]
  );

  async function runAnalysis() {
    if (inputMode === "file" && !file) {
      setError("Upload a DOCX or TXT transcript first.");
      return;
    }
    if (inputMode === "paste" && !pastedTranscript.trim()) {
      setError("Paste transcript text first.");
      return;
    }
    setError("");
    setIsRunning(true);
    try {
      const speakerPrefixes = {
        caregiver: caregiverPrefix,
        participant: participantPrefix
      };
      const response =
        inputMode === "file"
          ? await createAnalysisRun({
              file: file as File,
              participantId,
              speakerPrefixes,
              selectedMetrics,
              disfluencyTokens,
              skillPack: skillPackPayload
            })
          : await createTextAnalysisRun({
              content: pastedTranscript,
              sourceFilename: `${participantId || "pasted"}_pasted_transcript.txt`,
              participantId,
              speakerPrefixes,
              selectedMetrics,
              disfluencyTokens,
              skillPack: skillPackPayload
            });
      setRun(response);
      setRunHistory(await listRuns());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analysis failed");
    } finally {
      setIsRunning(false);
    }
  }

  async function validateCurrentSkillPack() {
    try {
      let payload: unknown;
      let summary;
      try {
        payload = JSON.parse(skillPackJson);
        summary = await validateSkillPack(payload);
      } catch (jsonError) {
        if (!(jsonError instanceof SyntaxError)) {
          throw jsonError;
        }
        const validated = await validateSkillPackText({
          filename: "pasted_skill_pack.yaml",
          content: skillPackJson
        });
        payload = validated.payload;
        summary = validated.summary;
      }
      setSkillPackPayload(payload);
      setSkillPack({
        id: summary.id,
        name: summary.name,
        version: summary.version,
        metrics: summary.metric_ids,
        disfluency_tokens: summary.disfluency_tokens,
        speaker_prefixes: summary.speaker_prefixes,
        concept_lexicons: summary.concept_lexicons,
        nonverbal_cues: summary.nonverbal_cues
      });
      setSelectedMetrics(summary.metric_ids);
      setDisfluencyText(summary.disfluency_tokens.join(", "));
      applyPackPrefixes(summary.speaker_prefixes);
      setSkillPackStatus(`Active: ${summary.name} v${summary.version}`);
      setError("");
    } catch (err) {
      setSkillPackStatus("");
      setError(err instanceof Error ? err.message : "Skill pack validation failed");
    }
  }

  async function draftPackFromBrief() {
    if (!studyBrief.trim()) {
      setError("Add a study brief before drafting a skill pack.");
      return;
    }
    try {
      setError("");
      setSkillPackStatus("Drafting skill pack from study brief...");
      const draft = await draftSkillPack({
        brief: studyBrief,
        name: draftName.trim() || undefined,
        authoringEngine,
        model: authoringEngine === "openrouter" ? authoringModel : undefined
      });
      activateSkillPack(draft.skill_pack, draft.payload);
      setSkillPackJson(JSON.stringify(draft.payload, null, 2));
      setDraftWarnings(draft.warnings);
      setAppliedChanges([]);
      setSkillPackStatus(`Drafted: ${draft.skill_pack.name} v${draft.skill_pack.version}`);
    } catch (err) {
      setSkillPackStatus("");
      setError(err instanceof Error ? err.message : "Could not draft skill pack");
    }
  }

  async function refineActiveSkillPack() {
    if (!skillPackPayload) {
      setError("Draft or load a skill pack before refining it.");
      return;
    }
    if (!refinementInstruction.trim()) {
      setError("Add a refinement instruction first.");
      return;
    }
    try {
      setError("");
      setSkillPackStatus("Refining active skill pack...");
      const refined = await refineSkillPack({
        payload: skillPackPayload,
        instruction: refinementInstruction,
        authoringEngine,
        model: authoringEngine === "openrouter" ? authoringModel : undefined
      });
      activateSkillPack(refined.skill_pack, refined.payload);
      setSkillPackJson(JSON.stringify(refined.payload, null, 2));
      setAppliedChanges(refined.applied_changes);
      setDraftWarnings(refined.warnings);
      setSkillPackStatus(`Refined: ${refined.skill_pack.name} v${refined.skill_pack.version}`);
    } catch (err) {
      setSkillPackStatus("");
      setError(err instanceof Error ? err.message : "Could not refine skill pack");
    }
  }

  async function loadSkillPackFile(file: File | null) {
    if (!file) {
      return;
    }
    try {
      const text = await file.text();
      setSkillPackJson(text);
      const { summary, payload } = await validateSkillPackText({
        filename: file.name,
        content: text
      });
      activateSkillPack(summary, payload);
      setSkillPackStatus(`Loaded ${file.name}`);
      setDraftWarnings([]);
      setAppliedChanges([]);
      setError("");
    } catch (err) {
      setSkillPackStatus("");
      setError(err instanceof Error ? err.message : "Could not load skill pack");
    }
  }

  function activateSkillPack(
    summary: {
      id: string;
      name: string;
      version: string;
      metric_ids: string[];
      disfluency_tokens: string[];
      speaker_prefixes: Record<string, string[]>;
      concept_lexicons: Record<string, string[]>;
      nonverbal_cues: Record<string, string[]>;
    },
    payload: unknown
  ) {
    setSkillPackPayload(payload);
    setSkillPack({
      id: summary.id,
      name: summary.name,
      version: summary.version,
      metrics: summary.metric_ids,
      disfluency_tokens: summary.disfluency_tokens,
      speaker_prefixes: summary.speaker_prefixes,
      concept_lexicons: summary.concept_lexicons,
      nonverbal_cues: summary.nonverbal_cues
    });
    setSelectedMetrics(summary.metric_ids);
    setDisfluencyText(summary.disfluency_tokens.join(", "));
    applyPackPrefixes(summary.speaker_prefixes);
  }

  function applyPackPrefixes(prefixes?: Record<string, string[]>) {
    const caregiver = prefixes?.caregiver?.[0];
    const participant = prefixes?.participant?.[0];
    if (caregiver) {
      setCaregiverPrefix(caregiver);
    }
    if (participant) {
      setParticipantPrefix(participant);
    }
  }

  function toggleMetric(metric: MetricId) {
    setSelectedMetrics((current) =>
      current.includes(metric)
        ? current.filter((item) => item !== metric)
        : [...current, metric]
    );
  }

  function applyParticipantPrefixTemplate(nextParticipantId: string) {
    const previousCaregiverPrefix = `${participantId}_c`;
    const previousParticipantPrefix = `${participantId}_p`;
    setParticipantId(nextParticipantId);
    if (!caregiverPrefix || caregiverPrefix === previousCaregiverPrefix) {
      setCaregiverPrefix(`${nextParticipantId}_c`);
    }
    if (!participantPrefix || participantPrefix === previousParticipantPrefix) {
      setParticipantPrefix(`${nextParticipantId}_p`);
    }
  }

  async function submitPluginRequest() {
    try {
      setError("");
      const response = await createPluginRequest({
        title: pluginRequestTitle,
        researchQuestion: pluginRequestQuestion,
        requestedMetricId: pluginRequestTitle,
        outputColumns: pluginRequestColumns,
        exampleTranscript: pluginRequestExample,
        expectedBehavior: pluginRequestExpected
      });
      setPluginRequestStatus(
        `Saved request: ${response.request.id} -> ${response.implementation_prompt_path}`
      );
      setPluginRequests(await listPluginRequests());
      setAgentJobs(await listAgentJobs());
    } catch (err) {
      setPluginRequestStatus("");
      setError(err instanceof Error ? err.message : "Could not save plugin request");
    }
  }

  async function queuePluginBuildJob(requestId: string) {
    try {
      setError("");
      const response = await createPluginBuildJob(requestId);
      setPluginJobStatus(
        `Queued build job: ${response.job.id} -> ${response.job.runbook_path}`
      );
      setAgentJobs(await listAgentJobs());
    } catch (err) {
      setPluginJobStatus("");
      setError(err instanceof Error ? err.message : "Could not queue build job");
    }
  }

  async function updatePluginJobStatus(jobId: string, status: string) {
    try {
      setError("");
      const response = await updateAgentJobStatus(jobId, status);
      setPluginJobStatus(`Updated ${response.job.id} -> ${response.job.status}`);
      setAgentJobs(await listAgentJobs());
    } catch (err) {
      setPluginJobStatus("");
      setError(err instanceof Error ? err.message : "Could not update agent job");
    }
  }

  async function recordPluginJobEvidence(jobId: string) {
    try {
      setError("");
      const evidence = await createAgentJobEvidence({
        jobId,
        gate: "ui_review",
        command: "manual workbench review",
        status: "passed",
        summary: "Recorded from the Agent jobs panel."
      });
      setPluginJobStatus(`Evidence recorded: ${evidence.artifact_path}`);
    } catch (err) {
      setPluginJobStatus("");
      setError(err instanceof Error ? err.message : "Could not record evidence");
    }
  }

  async function selectSegmentationCase(caseId: string) {
    try {
      setError("");
      setSegmentationStatus("");
      const cachedCase = segmentationCases.find((item) => item.case_id === caseId);
      const nextCase = cachedCase ?? (await getSegmentationCase(caseId));
      setSelectedSegmentationCaseId(nextCase.case_id);
      setSelectedSegmentationCase(nextCase);
      setSegmentationDraft(nextCase.gold_text);
      setSegmentationRunSource(nextCase.descript_text);
      setSegmentationRunFile(null);
      setSegmentationRunSourceType("synthetic");
      setSegmentationRun(null);
      setSegmentationAnalysisRun(null);
      setSegmentationEvaluation(null);
    } catch (err) {
      setSegmentationStatus("");
      setError(err instanceof Error ? err.message : "Could not load segmentation case");
    }
  }

  function selectExistingSegmentationRun(nextRun: SegmentationRun) {
    setSegmentationRun(nextRun);
    setSegmentationRunSource(nextRun.descript_text);
    setSegmentationRunFile(null);
    setSegmentationRunSourceType(nextRun.source);
    setSegmentationDraft(nextRun.merged_draft);
    setSegmentationEvaluation(nextRun.evaluation);
    setSegmentationAnalysisRun(null);
  }

  async function runSegmentationEvaluation() {
    if (!selectedSegmentationCaseId || !segmentationDraft.trim()) {
      setError("Select a synthetic case and draft before evaluating.");
      return;
    }
    try {
      setError("");
      const response = await evaluateSegmentationDraft({
        caseId: selectedSegmentationCaseId,
        draftText: segmentationDraft
      });
      setSegmentationEvaluation(response.evaluation);
      setSegmentationStatus(
        response.evaluation.failures.length
          ? `${response.evaluation.failures.length} rule issue detected.`
          : "Verifier accepted this synthetic draft."
      );
    } catch (err) {
      setSegmentationStatus("");
      setError(err instanceof Error ? err.message : "Could not evaluate draft");
    }
  }

  async function queueSegmentationRewriteJob() {
    if (!selectedSegmentationCaseId) {
      setError("Select a synthetic case before queueing a rewrite agent.");
      return;
    }
    try {
      setError("");
      const response = await createSegmentationRewriteJob(selectedSegmentationCaseId);
      setSegmentationStatus(
        `Queued rewrite agent: ${response.job.id} -> ${response.job.runbook_path}`
      );
      setAgentJobs(await listAgentJobs());
    } catch (err) {
      setSegmentationStatus("");
      setError(err instanceof Error ? err.message : "Could not queue rewrite agent");
    }
  }

  async function runRuleSpecialistSegmentationPipeline() {
    const ruleIds = selectedSegmentationCase?.rule_ids ?? DEFAULT_SEGMENTATION_RULE_IDS;
    try {
      setError("");
      setSegmentationStatus("");
      const nextRun = segmentationRunFile
        ? await createSegmentationFileRun({
            file: segmentationRunFile,
            ruleIds
          })
        : await createSegmentationRun({
            sourceFilename: selectedSegmentationCase
              ? `${selectedSegmentationCase.case_id}.txt`
              : "pasted_descript_export.txt",
            descriptText: segmentationRunSource,
            ruleIds,
            source: segmentationRunSourceType
          });
      setSegmentationRun(nextRun);
      setSegmentationAnalysisRun(null);
      setSegmentationRuns(await listSegmentationRuns());
      setSegmentationDraft(nextRun.merged_draft);
      setSegmentationEvaluation(nextRun.evaluation);
      setSegmentationStatus(
        `Specialist pipeline ${nextRun.status}: ${nextRun.rule_plan.length} specialist packet(s), ${nextRun.merge_evidence.applied_patch_count} patch(es).`
      );
    } catch (err) {
      setSegmentationStatus("");
      setError(err instanceof Error ? err.message : "Could not run specialist pipeline");
    }
  }

  async function verifyCurrentSegmentationRun() {
    if (!segmentationRun) {
      setError("Run the specialist pipeline before verifying.");
      return;
    }
    try {
      setError("");
      const nextRun = await verifySegmentationRun(segmentationRun.run_id);
      setSegmentationRun(nextRun);
      setSegmentationAnalysisRun(null);
      setSegmentationRuns(await listSegmentationRuns());
      setSegmentationEvaluation(nextRun.evaluation);
      setSegmentationStatus(`Verifier status: ${nextRun.status}.`);
    } catch (err) {
      setSegmentationStatus("");
      setError(err instanceof Error ? err.message : "Could not verify segmentation run");
    }
  }

  async function queueSegmentationRunRewriteJob() {
    if (!segmentationRun) {
      setError("Run the specialist pipeline before queueing targeted rewrite.");
      return;
    }
    try {
      setError("");
      const response = await createSegmentationRunRewriteJob(segmentationRun.run_id);
      setSegmentationStatus(
        `Queued targeted rewrite: ${response.job.id} -> ${response.job.runbook_path}`
      );
      setAgentJobs(await listAgentJobs());
    } catch (err) {
      setSegmentationStatus("");
      setError(err instanceof Error ? err.message : "Could not queue targeted rewrite");
    }
  }

  async function analyzeCurrentSegmentationRun() {
    if (!segmentationRun) {
      setError("Run the specialist pipeline before analysis.");
      return;
    }
    try {
      setError("");
      const nextRun = await analyzeSegmentationRun(segmentationRun.run_id, {
        selectedMetrics,
        disfluencyTokens,
        skillPack
      });
      setSegmentationAnalysisRun(nextRun);
      setRun(nextRun);
      setRunHistory(await listRuns());
      setSegmentationStatus(
        `Analysis run created: ${nextRun.run_id.slice(0, 12)} with ${nextRun.results.length} metric set(s).`
      );
    } catch (err) {
      setSegmentationStatus("");
      setError(err instanceof Error ? err.message : "Could not analyze segmentation run");
    }
  }

  async function runSegmentationEndToEnd() {
    const ruleIds = selectedSegmentationCase?.rule_ids ?? DEFAULT_SEGMENTATION_RULE_IDS;
    try {
      setError("");
      setSegmentationStatus("");
      const nextSegmentationRun = segmentationRunFile
        ? await createSegmentationFileRun({
            file: segmentationRunFile,
            ruleIds
          })
        : await createSegmentationRun({
            sourceFilename: selectedSegmentationCase
              ? `${selectedSegmentationCase.case_id}.txt`
              : "pasted_descript_export.txt",
            descriptText: segmentationRunSource,
            ruleIds,
            source: segmentationRunSourceType
          });
      setSegmentationRun(nextSegmentationRun);
      setSegmentationRuns(await listSegmentationRuns());
      setSegmentationDraft(nextSegmentationRun.merged_draft);
      setSegmentationEvaluation(nextSegmentationRun.evaluation);
      if (nextSegmentationRun.status !== "verified") {
        setSegmentationAnalysisRun(null);
        setSegmentationStatus(
          `End-to-end paused at verifier: ${nextSegmentationRun.status}.`
        );
        return;
      }
      const nextAnalysisRun = await analyzeSegmentationRun(nextSegmentationRun.run_id, {
        selectedMetrics,
        disfluencyTokens,
        skillPack
      });
      setSegmentationAnalysisRun(nextAnalysisRun);
      setRun(nextAnalysisRun);
      setRunHistory(await listRuns());
      setSegmentationStatus(
        `End-to-end complete: gold transcript verified and ${nextAnalysisRun.results.length} table set(s) generated.`
      );
    } catch (err) {
      setSegmentationStatus("");
      setError(err instanceof Error ? err.message : "Could not complete end-to-end run");
    }
  }

  async function runSyntheticSegmentationCorpus() {
    try {
      setError("");
      setSegmentationStatus("");
      const nextCorpusRun = await createSegmentationCorpusRun(segmentationCorpusSeed);
      setSegmentationCorpusRuns(await listSegmentationCorpusRuns());
      setSegmentationRuns(await listSegmentationRuns());
      setSegmentationStatus(
        `Synthetic corpus ${nextCorpusRun.status}: ${nextCorpusRun.regression_pass_count}/${nextCorpusRun.total_case_count} regression checks passed.`
      );
    } catch (err) {
      setSegmentationStatus("");
      setError(
        err instanceof Error ? err.message : "Could not run synthetic segmentation corpus"
      );
    }
  }

  async function runStudyWorkspaceBatch() {
    if (!skillPackPayload) {
      setError("Activate a skill pack before running a study batch.");
      return;
    }
    if (batchParseError || !batchTranscripts.length) {
      setError(batchParseError || "Add at least one transcript before running a batch.");
      return;
    }
    try {
      setError("");
      const study = selectedStudyId
        ? studies.find((item) => item.id === selectedStudyId) ??
          {
            id: selectedStudyId,
            name: studyName,
            description: studyDescription,
            created_at: new Date().toISOString()
          }
        : await createStudy({
            name: studyName,
            description: studyDescription
          });
      const schema = await updateStudySchema(
        study.id,
        casebookRequestFromControls({
          participantCount: casebookParticipantCount,
          conditions: casebookConditions,
          weekCount: casebookWeekCount,
          customFields: casebookCustomFields
        })
      );
      const version = await addStudySkillPackVersion(study.id, skillPackPayload);
      const batch = batchFiles.length
        ? await createStudyFileBatch({
            studyId: study.id,
            skillPackVersionId: version.version_id,
            files: batchFiles,
            metadataByFilename: metadataBySourceFilename(batchTranscripts)
          })
        : await createStudyTextBatch({
            studyId: study.id,
            skillPackVersionId: version.version_id,
            transcripts: batchTranscripts
          });
      setStudyBatch(batch);
      setStudyWorkspaceStatus(
        `${selectedStudyId ? "Existing study reused" : "Study created"}; schema saved for ${schema.participants.length} participant(s); batch complete: ${batch.batch.run_count} run(s), ${batch.batch.failure_count} failure(s)`
      );
      setStudies(await listStudies());
      setSelectedStudyId(study.id);
      setStudyBatches(await listStudyBatches(study.id));
      await refreshBatchRuns(study.id, batch.batch.batch_id);
    } catch (err) {
      setStudyWorkspaceStatus("");
      setError(err instanceof Error ? err.message : "Could not run study batch");
    }
  }

  async function refreshBatchRuns(studyId: string, batchId: string) {
    try {
      setBatchRuns(await listStudyBatchRuns(studyId, batchId));
      setSelectedBatchRun(null);
    } catch {
      setBatchRuns([]);
      setSelectedBatchRun(null);
    }
  }

  function updateBatchTranscriptText(value: string) {
    setBatchFiles([]);
    setBatchUploadStatus("");
    setBatchTranscriptText(value);
    try {
      setBatchTranscripts(parseBatchTranscriptText(value));
      setBatchParseError("");
    } catch (err) {
      setBatchTranscripts([]);
      setBatchParseError(err instanceof Error ? err.message : "Could not parse batch text.");
    }
  }

  function updateBatchAssignment(index: number, key: string, value: string) {
    const updated = updateBatchTranscriptMetadata(batchTranscripts, index, key, value);
    setBatchTranscripts(updated);
    setBatchTranscriptText(serializeBatchTranscriptText(updated));
    setBatchParseError("");
  }

  function exportCurrentCasebookCsv() {
    const csv = exportCasebookCsv(batchTranscripts);
    const blobUrl = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const link = document.createElement("a");
    link.href = blobUrl;
    link.download = `${studyName.trim() || "study"}-casebook.csv`;
    link.click();
    URL.revokeObjectURL(blobUrl);
    setBatchUploadStatus("Exported casebook CSV without transcript content.");
  }

  async function importCasebookCsv(files: FileList | null) {
    const file = files?.[0];
    if (!file) {
      return;
    }
    try {
      const metadataByFilename = parseCasebookCsv(await file.text());
      const updated = batchTranscripts.map((transcript) => ({
        ...transcript,
        metadata: {
          ...(transcript.metadata ?? {}),
          ...(metadataByFilename[transcript.source_filename] ?? {})
        }
      }));
      setBatchTranscripts(updated);
      setBatchTranscriptText(serializeBatchTranscriptText(updated));
      setBatchUploadStatus(`Imported casebook metadata for ${Object.keys(metadataByFilename).length} file(s).`);
      setBatchParseError("");
    } catch (err) {
      setBatchParseError(err instanceof Error ? err.message : "Could not import casebook CSV.");
    }
  }

  function applyCasebookTemplate(templateId: string) {
    const template = CASEBOOK_TEMPLATES[templateId];
    if (!template) {
      return;
    }
    setCasebookParticipantCount(template.participantCount);
    setCasebookConditions(template.conditions);
    setCasebookWeekCount(template.weekCount);
    setCasebookCustomFields(template.customFields.join(", "));
  }

  async function selectExistingStudy(study: StudyWorkspace) {
    setSelectedStudyId(study.id);
    setStudyName(study.name);
    setStudyDescription(study.description);
    setStudyWorkspaceStatus(`Using existing study: ${study.name}`);
    setStudyBatches([]);
    setBatchRuns([]);
    setSelectedBatchRun(null);
    try {
      const schema = await getStudySchema(study.id);
      const controls = schemaControlsFromStudySchema(schema);
      setCasebookParticipantCount(controls.participantCount);
      setCasebookConditions(controls.conditions);
      setCasebookWeekCount(controls.weekCount);
      setCasebookCustomFields(controls.customFields);
    } catch {
      setStudyWorkspaceStatus(`Using existing study without saved schema: ${study.name}`);
    }
    try {
      setStudyBatches(await listStudyBatches(study.id));
    } catch {
      setStudyBatches([]);
    }
  }

  function startNewStudy() {
    setSelectedStudyId("");
    setStudyBatches([]);
    setBatchRuns([]);
    setSelectedBatchRun(null);
    setStudyWorkspaceStatus("");
  }

  async function loadStudyBatch(batchId: string) {
    if (!selectedStudyId) {
      return;
    }
    try {
      const batch = await getStudyBatch(selectedStudyId, batchId);
      setStudyBatch(batch);
      setStudyWorkspaceStatus(`Loaded batch ${batch.batch.batch_id.slice(0, 18)}`);
      await refreshBatchRuns(selectedStudyId, batchId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load study batch");
    }
  }

  async function loadStudyBatchRun(runId: string) {
    if (!selectedStudyId || !studyBatch) {
      return;
    }
    try {
      const transcriptRun = await getStudyBatchRun(
        selectedStudyId,
        studyBatch.batch.batch_id,
        runId
      );
      setSelectedBatchRun(transcriptRun);
      setStudyWorkspaceStatus(`Loaded transcript: ${transcriptRun.source_filename}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load transcript run");
    }
  }

  async function importBatchFiles(files: FileList | null) {
    if (!files?.length) {
      return;
    }
    try {
      const fileArray = Array.from(files);
      const unsupported = fileArray.filter((item) => !isSupportedBatchTranscriptFile(item.name));
      if (unsupported.length) {
        throw new Error("Study batch file import supports TXT and DOCX files.");
      }
      const transcripts = await Promise.all(
        fileArray.map(async (item) => {
          if (item.name.toLowerCase().endsWith(".txt")) {
            return createBatchTranscriptFromTextFile(item.name, await item.text());
          }
          return createBatchTranscriptFromFilePreview(item.name);
        })
      );
      setBatchFiles(fileArray);
      setBatchTranscripts(transcripts);
      setBatchTranscriptText(serializeBatchTranscriptText(transcripts));
      setBatchParseError("");
      setBatchUploadStatus(
        `Imported ${transcripts.length} transcript file${transcripts.length === 1 ? "" : "s"} for local extraction`
      );
    } catch (err) {
      setBatchFiles([]);
      setBatchUploadStatus("");
      setBatchParseError(err instanceof Error ? err.message : "Could not import batch files.");
    }
  }

  return (
    <main className="app-shell h-screen overflow-hidden text-[#171717]">
      <div className="mx-auto flex h-full max-w-[1500px] flex-col gap-3 px-4 py-3 sm:px-5 lg:px-6">
        <header className="app-header">
          <div>
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-[#47615d]">
              <FlaskConical size={18} />
              Local transcript review studio
            </div>
            <h1 className="max-w-4xl text-2xl font-semibold tracking-normal text-[#171717] md:text-3xl">
              C-unit transcript workspace
            </h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-[#4c4a44]">
              Turn one Descript-style transcript into a verified gold transcript, then
              produce the local analysis tables a research team can inspect.
            </p>
          </div>
          <div className="status-ribbon self-end">
            <StatusTile icon={<ShieldCheck size={18} />} label="Privacy" value={privacyMode} />
            <StatusTile icon={<Sparkles size={18} />} label="Agents" value="Patch-based" />
            <StatusTile icon={<TableProperties size={18} />} label="Output" value="Transcript + tables" />
          </div>
        </header>

        <section className="workspace-grid">
          <aside className="quiet-rail">
            <Panel title="Transcript setup" icon={<FileText size={18} />}>
              <div className="mode-switch" aria-label="Transcript input mode">
                <button
                  className={inputMode === "file" ? "mode-button mode-button-active" : "mode-button"}
                  type="button"
                  onClick={() => setInputMode("file")}
                >
                  File
                </button>
                <button
                  className={inputMode === "paste" ? "mode-button mode-button-active" : "mode-button"}
                  type="button"
                  onClick={() => setInputMode("paste")}
                >
                  Paste
                </button>
              </div>
              {inputMode === "file" ? (
                <label className="dropzone">
                  <input
                    className="sr-only"
                    type="file"
                    accept=".txt,.docx"
                    onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                  />
                  {file ? (
                    <span className="loaded-file">
                      <span className="loaded-file-icon">
                        <FileCheck2 size={18} />
                      </span>
                      <span className="min-w-0">
                        <span className="block text-xs font-semibold uppercase text-[#47615d]">
                          Demo transcript loaded
                        </span>
                        <span className="mt-1 block overflow-hidden text-ellipsis whitespace-nowrap text-sm font-semibold text-[#171717]">
                          {file.name}
                        </span>
                        <span className="mt-1 block text-xs text-[#676157]">
                          {(file.size / 1024).toFixed(1)} KB - click to replace
                        </span>
                      </span>
                    </span>
                  ) : (
                    <>
                      <span className="text-sm font-semibold">Choose DOCX or TXT</span>
                      <span className="mt-2 block text-sm text-[#676157]">
                        One transcript per run for v1
                      </span>
                    </>
                  )}
                </label>
              ) : (
                <label className="field-label mt-0">
                  Transcript text
                  <textarea
                    className="field-input min-h-36 resize-y font-mono text-xs"
                    value={pastedTranscript}
                    onChange={(event) => setPastedTranscript(event.target.value)}
                    placeholder={"vr001_c: Um, look at this picture.\\nvr001_p: I remember that."}
                  />
                </label>
              )}
              <label className="field-label">
                Participant ID
                <input
                  className="field-input"
                  value={participantId}
                  onChange={(event) => applyParticipantPrefixTemplate(event.target.value)}
                  placeholder="vr001"
                />
              </label>
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <label className="block text-sm font-medium text-[#3e3b35]">
                  Caregiver prefix
                  <input
                    className="field-input"
                    value={caregiverPrefix}
                    onChange={(event) => setCaregiverPrefix(event.target.value)}
                    placeholder="vr001_c"
                  />
                </label>
                <label className="block text-sm font-medium text-[#3e3b35]">
                  Participant prefix
                  <input
                    className="field-input"
                    value={participantPrefix}
                    onChange={(event) => setParticipantPrefix(event.target.value)}
                    placeholder="vr001_p"
                  />
                </label>
              </div>
            </Panel>

            <details className="quiet-details">
              <summary>
                <span>Advanced metric skills</span>
                <span>Skill packs, plugins, local analysis</span>
              </summary>
              <div className="space-y-4 pt-4">
                <Panel title="Skill Pack Studio" icon={<Sparkles size={18} />}>
                  <div className="studio-block">
                    <div className="engine-switch" aria-label="Skill authoring engine">
                      <button
                        className={
                          authoringEngine === "local"
                            ? "mode-button mode-button-active"
                            : "mode-button"
                        }
                        type="button"
                        onClick={() => setAuthoringEngine("local")}
                      >
                        Local rules
                      </button>
                      <button
                        className={
                          authoringEngine === "openrouter"
                            ? "mode-button mode-button-active"
                            : "mode-button"
                        }
                        type="button"
                        onClick={() => setAuthoringEngine("openrouter")}
                      >
                        OpenRouter
                      </button>
                    </div>
                    {authoringEngine === "openrouter" ? (
                      <label className="field-label mt-3">
                        Model
                        <input
                          className="field-input"
                          value={authoringModel}
                          onChange={(event) => setAuthoringModel(event.target.value)}
                        />
                        <span className="field-helper">
                          Only study briefs, refinement instructions, and skill-pack schemas are sent.
                        </span>
                      </label>
                    ) : null}
                    <label className="field-label mt-0">
                      Study brief
                      <textarea
                        className="field-input min-h-28 resize-y"
                        value={studyBrief}
                        onChange={(event) => setStudyBrief(event.target.value)}
                      />
                    </label>
                    <label className="field-label">
                      Draft name
                      <input
                        className="field-input"
                        value={draftName}
                        onChange={(event) => setDraftName(event.target.value)}
                      />
                    </label>
                    <button className="secondary-button" type="button" onClick={draftPackFromBrief}>
                      <Sparkles size={16} />
                      Draft skill pack
                    </button>
                    <label className="field-label">
                      Refinement request
                      <textarea
                        className="field-input min-h-20 resize-y"
                        value={refinementInstruction}
                        onChange={(event) => setRefinementInstruction(event.target.value)}
                      />
                    </label>
                    <button className="secondary-button" type="button" onClick={refineActiveSkillPack}>
                      <Sparkles size={16} />
                      Refine active pack
                    </button>
                    {appliedChanges.length ? (
                      <div className="applied-change-list">
                        {appliedChanges.map((change) => (
                          <span key={change} className="applied-change-pill">
                            {change}
                          </span>
                        ))}
                      </div>
                    ) : null}
                    {draftWarnings.length ? (
                      <div className="mt-3 space-y-2">
                        {draftWarnings.map((warning) => (
                          <div key={warning} className="diagnostic-warning">
                            <AlertTriangle size={16} />
                            <span>{warning}</span>
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </div>
                  <div className="skill-pack-header">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-[#171717]">
                        {skillPack?.name ?? "No active pack"}
                      </div>
                      <div className="mt-1 text-xs text-[#756f64]">
                        {skillPack
                          ? `${skillPack.id} · v${skillPack.version}`
                          : "Upload or paste JSON/YAML"}
                      </div>
                    </div>
                    <label className="json-upload-button">
                      JSON/YAML
                      <input
                        className="sr-only"
                        type="file"
                        accept=".json,.yaml,.yml,application/json,text/yaml,application/x-yaml"
                        onChange={(event) => void loadSkillPackFile(event.target.files?.[0] ?? null)}
                      />
                    </label>
                  </div>
                  <textarea
                    className="field-input min-h-36 resize-y font-mono text-xs"
                    value={skillPackJson}
                    onChange={(event) => setSkillPackJson(event.target.value)}
                    spellCheck={false}
                  />
                  <button className="secondary-button" type="button" onClick={validateCurrentSkillPack}>
                    <Braces size={16} />
                    Validate + activate skill pack
                  </button>
                  {skillPackStatus ? <p className="success-text">{skillPackStatus}</p> : null}
                </Panel>

                <Panel title="Metric Skills" icon={<TableProperties size={18} />}>
                  <div className="space-y-2">
                    {(skillPack?.metrics ?? selectedMetrics).map((metric) => (
                      <label key={metric} className="metric-toggle">
                        <input
                          type="checkbox"
                          checked={selectedMetrics.includes(metric)}
                          onChange={() => toggleMetric(metric)}
                        />
                        <span>{metricLabel(metric)}</span>
                      </label>
                    ))}
                  </div>
                  <label className="field-label">
                    Disfluency inventory
                    <textarea
                      className="field-input min-h-24 resize-y"
                      value={disfluencyText}
                      onChange={(event) => setDisfluencyText(event.target.value)}
                    />
                  </label>
                  <button className="run-button" disabled={isRunning} onClick={runAnalysis}>
                    {isRunning ? <Loader2 className="animate-spin" size={18} /> : <Play size={18} />}
                    Run local analysis
                  </button>
                  <PluginCatalog
                    plugins={metricPlugins}
                    activeMetrics={selectedMetrics}
                    requests={pluginRequests}
                    jobs={agentJobs}
                    requestTitle={pluginRequestTitle}
                    requestQuestion={pluginRequestQuestion}
                    requestColumns={pluginRequestColumns}
                    requestExample={pluginRequestExample}
                    requestExpected={pluginRequestExpected}
                    requestStatus={pluginRequestStatus}
                    jobStatus={pluginJobStatus}
                    onRequestTitleChange={setPluginRequestTitle}
                    onRequestQuestionChange={setPluginRequestQuestion}
                    onRequestColumnsChange={setPluginRequestColumns}
                    onRequestExampleChange={setPluginRequestExample}
                    onRequestExpectedChange={setPluginRequestExpected}
                    onSubmitRequest={submitPluginRequest}
                    onQueueBuildJob={queuePluginBuildJob}
                    onUpdateJobStatus={updatePluginJobStatus}
                    onRecordEvidence={recordPluginJobEvidence}
                  />
                  {error ? <p className="error-text">{error}</p> : null}
                </Panel>
              </div>
            </details>
          </aside>

          <section className="main-workspace">
            <SegmentationDemoPanel
              cases={segmentationCases}
              selectedCase={selectedSegmentationCase}
              selectedCaseId={selectedSegmentationCaseId}
              draft={segmentationDraft}
              evaluation={segmentationEvaluation}
              runSource={segmentationRunSource}
              runSourceType={segmentationRunSourceType}
              runFile={segmentationRunFile}
              run={segmentationRun}
              analysisRun={segmentationAnalysisRun}
              runs={segmentationRuns}
              corpusSeed={segmentationCorpusSeed}
              corpusRuns={segmentationCorpusRuns}
              rulebook={segmentationRulebook}
              status={segmentationStatus}
              onSelectCase={selectSegmentationCase}
              onDraftChange={setSegmentationDraft}
              onRunSourceChange={(value) => {
                setSegmentationRunSource(value);
                setSegmentationRunFile(null);
                setSegmentationRunSourceType("researcher_provided");
              }}
              onRunFileChange={(nextFile) => {
                setSegmentationRunFile(nextFile);
                if (nextFile) {
                  setSegmentationRunSourceType("researcher_provided");
                }
              }}
              onSelectRun={selectExistingSegmentationRun}
              onUseGoldDraft={() =>
                selectedSegmentationCase
                  ? setSegmentationDraft(selectedSegmentationCase.gold_text)
                  : undefined
              }
              onEvaluate={runSegmentationEvaluation}
              onRunPipeline={runRuleSpecialistSegmentationPipeline}
              onRunEndToEnd={runSegmentationEndToEnd}
              onVerifyRun={verifyCurrentSegmentationRun}
              onQueueRewriteJob={queueSegmentationRewriteJob}
              onQueueRunRewriteJob={queueSegmentationRunRewriteJob}
              onAnalyzeRun={analyzeCurrentSegmentationRun}
              onCorpusSeedChange={setSegmentationCorpusSeed}
              onRunCorpus={runSyntheticSegmentationCorpus}
            />
            <details className="quiet-details" open={isStudyWorkspaceOpen}>
              <summary
                aria-expanded={isStudyWorkspaceOpen}
                onClick={(event) => {
                  event.preventDefault();
                  setIsStudyWorkspaceOpen((current) => !current);
                }}
              >
                <span>Study batches and general analysis</span>
                <span>Open for multi-transcript tables, skill outputs, and run history</span>
              </summary>
              <div className="space-y-4 pt-4">
                <Panel title="Run Output" icon={<Database size={18} />}>
                  {run ? (
                    <div className="space-y-4">
                      <RunSummaryStrip run={run} />
                      <DiagnosticsPanel run={run} />
                      <div className="grid gap-3 md:grid-cols-3">
                        <OutputFact label="Run ID" value={run.run_id.slice(0, 12)} />
                        <OutputFact label="Transcript" value={run.source_filename} />
                        <OutputFact label="Turns" value={String(run.turn_count)} />
                        {run.skill_pack ? (
                          <OutputFact
                            label="Skill pack"
                            value={`${run.skill_pack.name} v${run.skill_pack.version}`}
                          />
                        ) : null}
                        <OutputFact label="JSON" value={run.stored.results_json} wide />
                        <OutputFact label="Exports" value={run.stored.export_dir} wide />
                      </div>
                    </div>
                  ) : (
                    <EmptyState />
                  )}
                </Panel>

                {run?.results.map((result) => (
                  <MetricTable
                    key={result.metric_id}
                    result={result}
                    downloadUrl={
                      run.exports.find((item) => item.metric_id === result.metric_id)
                        ?.download_url
                    }
                  />
                ))}
                <StudyWorkspacePanel
                  studies={studies}
                  selectedStudyId={selectedStudyId}
                  studyName={studyName}
                  studyDescription={studyDescription}
                  casebookParticipantCount={casebookParticipantCount}
                  casebookConditions={casebookConditions}
                  casebookWeekCount={casebookWeekCount}
                  casebookCustomFields={casebookCustomFields}
                  casebookOptions={casebookOptions}
                  casebookWarnings={casebookWarnings}
                  batchTranscriptText={batchTranscriptText}
                  batchTranscripts={batchTranscripts}
                  batchParseError={batchParseError}
                  batchUploadStatus={batchUploadStatus}
                  studyBatches={studyBatches}
                  batch={studyBatch}
                  batchRuns={batchRuns}
                  selectedBatchRun={selectedBatchRun}
                  status={studyWorkspaceStatus}
                  onStudyNameChange={setStudyName}
                  onStudyDescriptionChange={setStudyDescription}
                  onSelectStudy={selectExistingStudy}
                  onStartNewStudy={startNewStudy}
                  onCasebookParticipantCountChange={setCasebookParticipantCount}
                  onCasebookConditionsChange={setCasebookConditions}
                  onCasebookWeekCountChange={setCasebookWeekCount}
                  onCasebookCustomFieldsChange={setCasebookCustomFields}
                  onApplyCasebookTemplate={applyCasebookTemplate}
                  onBatchTranscriptTextChange={updateBatchTranscriptText}
                  onBatchFilesSelected={importBatchFiles}
                  onCasebookCsvExport={exportCurrentCasebookCsv}
                  onCasebookCsvImport={importCasebookCsv}
                  onBatchAssignmentChange={updateBatchAssignment}
                  onLoadBatch={loadStudyBatch}
                  onLoadBatchRun={loadStudyBatchRun}
                  onRunBatch={runStudyWorkspaceBatch}
                />
                <RecentRunsPanel runs={runHistory} />
              </div>
            </details>
          </section>
        </section>
      </div>
    </main>
  );
}

function SegmentationDemoPanel({
  cases,
  selectedCase,
  selectedCaseId,
  draft,
  evaluation,
  runSource,
  runSourceType,
  runFile,
  run,
  analysisRun,
  runs,
  corpusSeed,
  corpusRuns,
  rulebook,
  status,
  onSelectCase,
  onDraftChange,
  onRunSourceChange,
  onRunFileChange,
  onSelectRun,
  onUseGoldDraft,
  onEvaluate,
  onRunPipeline,
  onRunEndToEnd,
  onVerifyRun,
  onQueueRewriteJob,
  onQueueRunRewriteJob,
  onAnalyzeRun,
  onCorpusSeedChange,
  onRunCorpus
}: {
  cases: SegmentationCase[];
  selectedCase: SegmentationCase | null;
  selectedCaseId: string;
  draft: string;
  evaluation: SegmentationEvaluation | null;
  runSource: string;
  runSourceType: SegmentationSource;
  runFile: File | null;
  run: SegmentationRun | null;
  analysisRun: RunResponse | null;
  runs: SegmentationRun[];
  corpusSeed: number;
  corpusRuns: SegmentationCorpusRun[];
  rulebook: CUnitRulebookSummary | null;
  status: string;
  onSelectCase: (caseId: string) => void;
  onDraftChange: (value: string) => void;
  onRunSourceChange: (value: string) => void;
  onRunFileChange: (file: File | null) => void;
  onSelectRun: (run: SegmentationRun) => void;
  onUseGoldDraft: () => void;
  onEvaluate: () => void;
  onRunPipeline: () => void;
  onRunEndToEnd: () => void;
  onVerifyRun: () => void;
  onQueueRewriteJob: () => void;
  onQueueRunRewriteJob: () => void;
  onAnalyzeRun: () => void;
  onCorpusSeedChange: (seed: number) => void;
  onRunCorpus: () => void;
}) {
  const [activeTab, setActiveTab] = useState<
    "source" | "specialists" | "gold" | "adjudication" | "verification" | "tables"
  >("source");
  const runLabel = run
    ? `${run.status} · ${run.merge_evidence.applied_patch_count} patches`
    : "Ready";

  useEffect(() => {
    if (analysisRun) {
      setActiveTab("tables");
    }
  }, [analysisRun]);

  function runEndToEndAndShowTables() {
    setActiveTab("tables");
    onRunEndToEnd();
  }

  function runPipelineAndShowSpecialists() {
    setActiveTab("specialists");
    onRunPipeline();
  }

  function verifyAndShowEvidence() {
    setActiveTab("verification");
    onVerifyRun();
  }

  function evaluateAndShowEvidence() {
    setActiveTab("verification");
    onEvaluate();
  }

  function analyzeAndShowTables() {
    setActiveTab("tables");
    onAnalyzeRun();
  }

  return (
    <section className="console-panel">
      <div className="console-toolbar">
        <div className="min-w-0">
          <div className="section-kicker">C-unit specialist pipeline</div>
          <div className="mt-1 flex flex-wrap items-center gap-3">
            <h2>Transcript console</h2>
            <span className="console-status-pill">{runLabel}</span>
          </div>
        </div>
        <div className="console-toolbar-actions">
          <button className="secondary-button mt-0" type="button" onClick={runPipelineAndShowSpecialists}>
            <Sparkles size={16} />
            Specialists
          </button>
          <button
            className="secondary-button mt-0"
            type="button"
            onClick={verifyAndShowEvidence}
            disabled={!run}
          >
            <ShieldCheck size={16} />
            Verify
          </button>
          <button
            className="secondary-button mt-0"
            type="button"
            onClick={analyzeAndShowTables}
            disabled={!run || run.status !== "verified"}
          >
            <TableProperties size={16} />
            Tables
          </button>
          <button className="run-button mt-0" type="button" onClick={runEndToEndAndShowTables}>
            <Play size={16} />
            Generate gold transcript
          </button>
        </div>
      </div>

      <div className="console-tabbar" role="tablist" aria-label="Transcript workflow">
        {[
          ["source", "Transcript", runSource ? "Loaded" : "Empty"],
          ["specialists", "Specialists", run ? `${run.rule_plan.length} packets` : "Pending"],
          ["gold", "Gold Transcript", run ? run.status : "Draft"],
          [
            "adjudication",
            "C-unit Decisions",
            run ? `${run.cunit_adjudication.counted_cunit_count} C-units` : "Pending"
          ],
          ["verification", "Verification", evaluation ? `Score ${evaluation.score}` : "Pending"],
          ["tables", "Analysis Tables", analysisRun ? `${analysisRun.results.length} tables` : "Pending"]
        ].map(([id, label, meta]) => (
          <button
            key={id}
            className={activeTab === id ? "console-tab console-tab-active" : "console-tab"}
            type="button"
            role="tab"
            aria-selected={activeTab === id}
            onClick={() => setActiveTab(id as typeof activeTab)}
          >
            <span>{label}</span>
            <span>{meta}</span>
          </button>
        ))}
      </div>

      <div className="console-grid">
        <div className="console-stage">
          {activeTab === "source" ? (
            <div className="console-view">
              <div className="view-heading">
                <div>
                  <div className="section-kicker">Source</div>
                  <h3>Transcript input</h3>
                </div>
                <span className="casebook-pill">
                  {segmentationSourceLabel(runSourceType)}
                </span>
              </div>
              <div className="two-pane-editor">
                <div>
                  <label className="field-label mt-0">
                    Synthetic case
                    <select
                      className="field-input"
                      value={selectedCaseId}
                      onChange={(event) => onSelectCase(event.target.value)}
                    >
                      {cases.map((item) => (
                        <option key={item.case_id} value={item.case_id}>
                          {item.title}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field-label">
                    Descript input
                    <textarea
                      className="field-input console-textarea"
                      value={runSource}
                      onChange={(event) => onRunSourceChange(event.target.value)}
                    />
                  </label>
                  <label className="field-label">
                    TXT export
                    <input
                      className="field-input"
                      type="file"
                      accept=".txt,text/plain"
                      onChange={(event) => onRunFileChange(event.target.files?.[0] ?? null)}
                    />
                  </label>
                  {runFile ? (
                    <div className="field-helper">
                      Using upload: <span className="font-semibold">{runFile.name}</span>
                    </div>
                  ) : null}
                </div>
                <div className="reference-panel h-full">
                  <div className="mb-2 text-sm font-semibold text-[#2f413f]">
                    Synthetic reference
                  </div>
                  <pre className="transcript-preview transcript-preview-tall">
                    {selectedCase?.descript_text ?? "No synthetic case loaded."}
                  </pre>
                </div>
              </div>
            </div>
          ) : null}

          {activeTab === "specialists" ? (
            <div className="console-view">
              <div className="view-heading">
                <div>
                  <div className="section-kicker">Specialists</div>
                  <h3>Rule packets</h3>
                </div>
                {run ? (
                  <span className="casebook-pill">
                    {run.rule_plan.length} packets
                  </span>
                ) : null}
              </div>
              {run ? (
                <SpecialistPacketsPanel run={run} />
              ) : (
                <div className="quiet-empty">
                  Run the specialist pipeline to see rule packets, patch evidence, and packet exports.
                </div>
              )}
            </div>
          ) : null}

          {activeTab === "gold" ? (
            <div className="console-view">
              <div className="view-heading">
                <div>
                  <div className="section-kicker">Merge</div>
                  <h3>Gold transcript</h3>
                </div>
                {run ? <span className="casebook-pill">{run.status}</span> : null}
              </div>
              <label className="field-label mt-0">
                Draft to verify
                <textarea
                  className="field-input console-textarea console-textarea-large"
                  value={draft}
                  onChange={(event) => onDraftChange(event.target.value)}
                />
              </label>
              <div className="action-row">
                <button className="secondary-button mt-0" type="button" onClick={onUseGoldDraft}>
                  <FileCheck2 size={16} />
                  Use gold
                </button>
                <button className="primary-button mt-0" type="button" onClick={evaluateAndShowEvidence}>
                  <Play size={16} />
                  Verify draft
                </button>
                <button
                  className="secondary-button mt-0"
                  type="button"
                  onClick={onQueueRunRewriteJob}
                  disabled={!run || !run.failure_routes.length}
                >
                  <FileUp size={16} />
                  Rewrite
                </button>
                <button className="secondary-button mt-0" type="button" onClick={onQueueRewriteJob}>
                  <Sparkles size={16} />
                  Queue agent
                </button>
              </div>
              {status ? <div className="success-text">{status}</div> : null}
            </div>
          ) : null}

          {activeTab === "verification" ? (
            <div className="console-view">
              <div className="view-heading">
                <div>
                  <div className="section-kicker">Verification</div>
                  <h3>Rule evidence</h3>
                </div>
                {evaluation ? <span className="casebook-pill">score {evaluation.score}</span> : null}
              </div>
              {evaluation ? (
                <SegmentationEvaluationPanel evaluation={evaluation} />
              ) : (
                <div className="quiet-empty">
                  Run the verifier to see score, notation counts, and routed failures.
                </div>
              )}
            </div>
          ) : null}

          {activeTab === "adjudication" ? (
            <div className="console-view">
              <div className="view-heading">
                <div>
                  <div className="section-kicker">Semantic adjudication</div>
                  <h3>C-unit decisions</h3>
                </div>
                {run ? (
                  <span className="casebook-pill">
                    {run.cunit_adjudication.counted_cunit_count} counted
                  </span>
                ) : null}
              </div>
              {run ? (
                <CUnitAdjudicationPanel adjudication={run.cunit_adjudication} />
              ) : (
                <div className="quiet-empty">
                  Run the specialist pipeline to inspect semantic C-unit boundaries.
                </div>
              )}
            </div>
          ) : null}

          {activeTab === "tables" ? (
            <div className="console-view">
              <div className="view-heading">
                <div>
                  <div className="section-kicker">Analysis</div>
                  <h3>Tables</h3>
                </div>
                {analysisRun ? (
                  <span className="casebook-pill">{analysisRun.results.length} table sets</span>
                ) : null}
              </div>
              {analysisRun ? <SegmentationAnalysisTables run={analysisRun} /> : <EmptyState />}
            </div>
          ) : null}
        </div>

        <aside className="console-inspector">
          <div className="inspector-section">
            <div>
              <div className="section-kicker">Run state</div>
              <h3>Current transcript</h3>
            </div>
            <div className="inspector-facts">
              <OutputFact label="Status" value={run?.status ?? "not run"} />
              <OutputFact
                label="Specialists"
                value={run ? String(run.rule_plan.length) : "0"}
              />
              <OutputFact
                label="Tables"
                value={analysisRun ? String(analysisRun.results.length) : "0"}
              />
              <OutputFact
                label="C-units"
                value={run ? String(run.cunit_adjudication.counted_cunit_count) : "0"}
              />
            </div>
          </div>
          <div className="inspector-section">
            <div className="section-kicker">Selected rules</div>
            <div className="mt-2 flex flex-wrap gap-2">
              {selectedCase ? (
                <>
                  {selectedCase.rule_ids.slice(0, 5).map((ruleId) => (
                    <span key={ruleId} className="casebook-pill muted">
                      {ruleId}
                    </span>
                  ))}
                </>
              ) : (
                <span className="text-sm text-[#676157]">No case selected.</span>
              )}
            </div>
          </div>
          {rulebook ? (
            <div className="inspector-section">
              <div className="section-kicker">Agent coverage</div>
              <h3>What it knows now</h3>
              <div className="inspector-facts">
                <OutputFact
                  label="Supported rules"
                  value={String(rulebook.supported_rule_count)}
                />
                <OutputFact
                  label="Demo covers"
                  value={`${rulebook.demo_case_rule_count}/10`}
                />
                <OutputFact
                  label="Corpus covers"
                  value={`${rulebook.corpus_rule_count}/10`}
                />
              </div>
              <div className="mt-3 grid gap-2">
                {rulebook.professor_grade_areas.slice(0, 3).map((area) => (
                  <div key={area.area_id} className="rulebook-gap">
                    <div>
                      <span>{area.label}</span>
                      <strong>{area.status}</strong>
                    </div>
                    <p>{area.scientist_language}</p>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          {run ? (
            <div className="inspector-section">
              <SegmentationRunPanel run={run} />
            </div>
          ) : null}
          {runs.length ? (
            <div className="inspector-section">
              <div className="mb-2 text-xs font-semibold uppercase text-[#5f594f]">
                Recent segmentation runs
              </div>
              <div className="space-y-2">
                {runs.slice(0, 3).map((item) => (
                  <button
                    key={item.run_id}
                    className="recent-run-row w-full text-left"
                    type="button"
                    onClick={() => {
                      onSelectRun(item);
                      setActiveTab("gold");
                    }}
                  >
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-[#171717]">
                        {item.source_filename}
                      </div>
                      <div className="mt-1 font-mono text-xs text-[#756f64]">
                        {item.run_id.slice(0, 12)} · {item.status}
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          ) : null}
          <details className="subtle-details">
            <summary>Synthetic regression corpus</summary>
            <div className="mt-3 grid gap-2">
              <label className="field-label mt-0">
                Seed
                <input
                  className="field-input"
                  min={0}
                  type="number"
                  value={corpusSeed}
                  onChange={(event) =>
                    onCorpusSeedChange(Number(event.target.value || 0))
                  }
                />
              </label>
              <button className="primary-button" type="button" onClick={onRunCorpus}>
                <Activity size={16} />
                Run corpus
              </button>
              {corpusRuns[0] ? <SegmentationCorpusRunPanel corpusRun={corpusRuns[0]} /> : null}
            </div>
          </details>
        </aside>
      </div>
    </section>
  );
}

function SegmentationAnalysisTables({ run }: { run: RunResponse }) {
  return (
    <div className="rounded-md border border-[#d9d4c5] bg-[#fffdf8] p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-semibold text-[#2f413f]">Analysis tables</div>
        <div className="font-mono text-xs text-[#756f64]">
          {run.run_id.slice(0, 12)} · {run.turn_count} turns
        </div>
      </div>
      <RunSummaryStrip run={run} />
      <div className="mt-3 space-y-3">
        {run.results.map((result) => (
          <InlineMetricTable
            key={result.metric_id}
            result={result}
            downloadUrl={
              run.exports.find((item) => item.metric_id === result.metric_id)
                ?.download_url
            }
          />
        ))}
      </div>
    </div>
  );
}

function InlineMetricTable({
  result,
  downloadUrl
}: {
  result: MetricResult;
  downloadUrl?: string;
}) {
  const columns = Array.from(new Set(result.rows.flatMap((row) => Object.keys(row))));
  return (
    <div className="rounded-md border border-[#e3ded2] bg-white/70 p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-semibold text-[#2f413f]">{result.label}</div>
        {downloadUrl ? (
          <a className="export-link" href={apiUrl(downloadUrl)}>
            <Download size={16} />
            CSV
          </a>
        ) : null}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[620px] border-collapse text-left text-sm">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column} className="table-head">
                  {column.replaceAll("_", " ")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, index) => (
              <tr key={index} className="border-t border-[#e4ded0]">
                {columns.map((column) => (
                  <td key={column} className="table-cell">
                    {formatCell(row[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SegmentationCorpusRunPanel({
  corpusRun
}: {
  corpusRun: SegmentationCorpusRun;
}) {
  return (
    <div className="mt-3 space-y-2">
      <div className="flex flex-wrap gap-2 text-xs">
        <span className="casebook-pill">{corpusRun.status}</span>
        <span className="casebook-pill muted">
          {corpusRun.regression_pass_count}/{corpusRun.total_case_count} checks
        </span>
        <span className="casebook-pill muted">
          {corpusRun.rule_coverage.length} rules
        </span>
      </div>
      <div className="space-y-2">
        {corpusRun.results.map((result) => (
          <div
            key={`${corpusRun.corpus_run_id}-${result.case_id}`}
            className="rounded-md border border-[#e3ded2] bg-white/70 p-2"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0 text-sm font-semibold text-[#2f413f]">
                {result.case_id}
              </div>
              <div className="font-mono text-xs text-[#756f64]">
                {result.outcome} · {result.status}/{result.expected_status} ·{" "}
                {result.score}
              </div>
            </div>
            {result.failed_rule_ids.length ? (
              <div className="mt-1 text-xs text-[#756f64]">
                {result.failed_rule_ids.join(", ")}
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function CUnitAdjudicationPanel({
  adjudication
}: {
  adjudication: CUnitAdjudication;
}) {
  const reviewDecisions = adjudication.decisions.filter(
    (decision) => decision.needs_human_review
  );
  const primaryDecisions = [
    ...reviewDecisions,
    ...adjudication.decisions.filter((decision) => !decision.needs_human_review)
  ].slice(0, 12);

  return (
    <div className="space-y-3">
      <div className="adjudication-summary">
        <OutputFact label="Participant turns" value={String(adjudication.participant_turn_count)} />
        <OutputFact label="Examiner turns" value={String(adjudication.examiner_turn_count)} />
        <OutputFact label="Counted C-units" value={String(adjudication.counted_cunit_count)} />
        <OutputFact label="Needs review" value={String(adjudication.needs_review_count)} />
      </div>
      <div className="adjudication-boundaries">
        {Object.entries(adjudication.boundary_type_counts).map(([boundaryType, count]) => (
          <span key={boundaryType} className="casebook-pill muted">
            {boundaryType}: {count}
          </span>
        ))}
      </div>
      <div className="space-y-2">
        {primaryDecisions.map((decision) => (
          <div
            key={`${decision.event_index}-${decision.boundary_type}`}
            className={
              decision.needs_human_review
                ? "adjudication-decision adjudication-review"
                : "adjudication-decision"
            }
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <div className="font-mono text-xs uppercase tracking-wide text-[#756f64]">
                  event {decision.event_index} · {decision.speaker}
                </div>
                <div className="mt-1 text-sm font-semibold text-[#2f413f]">
                  {decision.boundary_type}
                </div>
              </div>
              <div className="flex flex-wrap gap-1">
                <span className="casebook-pill">
                  {decision.cunit_count} C-unit{decision.cunit_count === 1 ? "" : "s"}
                </span>
                {decision.needs_human_review ? (
                  <span className="casebook-pill warning">review</span>
                ) : null}
              </div>
            </div>
            <div className="mt-2 text-sm leading-6 text-[#3e3b35]">
              {decision.cleaned_text || decision.raw_text}
            </div>
            {decision.excluded_maze ? (
              <div className="mt-2 font-mono text-xs text-[#765a24]">
                excluded maze: {decision.excluded_maze}
              </div>
            ) : null}
            <p className="mt-2 text-xs leading-5 text-[#676157]">
              {decision.rationale}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

function SpecialistPacketsPanel({ run }: { run: SegmentationRun }) {
  return (
    <div className="space-y-3">
      <div className="adjudication-summary">
        <OutputFact label="Status" value={run.status} />
        <OutputFact label="Packets" value={String(run.rule_plan.length)} />
        <OutputFact label="Patches" value={String(run.merge_evidence.applied_patch_count)} />
        <OutputFact label="Conflicts" value={String(run.merge_evidence.conflicts.length)} />
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        {run.rule_plan.map((packet) => {
          const output = run.specialist_outputs.find(
            (item) => item.specialist_id === packet.specialist_id
          );
          return (
            <div key={packet.specialist_id} className="adjudication-decision">
              <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
                <div>
                  <div className="text-xs font-semibold uppercase text-[#756f64]">
                    Specialist packet
                  </div>
                  <div className="text-sm font-semibold text-[#2f413f]">
                    {packet.specialist_id}
                  </div>
                </div>
                {output ? (
                  <a
                    className="casebook-pill"
                    href={apiUrl(
                      `/api/segmentation/runs/${run.run_id}/specialists/${output.specialist_id}.html`
                    )}
                  >
                    packet html
                  </a>
                ) : null}
              </div>
              <div className="flex flex-wrap gap-1">
                {packet.rule_ids.map((ruleId) => (
                  <span key={ruleId} className="casebook-pill muted">
                    {ruleId}
                  </span>
                ))}
              </div>
              <div className="mt-3 space-y-2">
                {(output?.patches ?? []).slice(0, 5).map((patch, index) => (
                  <div
                    key={`${packet.specialist_id}-${patch.event_index}-${index}`}
                    className="rounded border border-[#e4ded0] bg-white/80 px-2 py-2 font-mono text-xs text-[#5f594f]"
                  >
                    <div className="font-semibold text-[#2f413f]">
                      {patch.operation}@{patch.event_index}: {patch.text || "(empty)"}
                    </div>
                    <div className="mt-1">{patch.reason}</div>
                  </div>
                ))}
                {output && output.patches.length > 5 ? (
                  <div className="text-xs font-semibold text-[#756f64]">
                    +{output.patches.length - 5} more patches in packet HTML
                  </div>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SegmentationRunPanel({ run }: { run: SegmentationRun }) {
  return (
    <div className="rounded-md border border-[#d9d4c5] bg-[#faf8f1] p-3">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="casebook-pill">{run.status}</span>
        <span className="casebook-pill muted">
          {segmentationSourceLabel(run.source)}
        </span>
        <span className="casebook-pill muted">
          {run.rule_plan.length} specialist packet
          {run.rule_plan.length === 1 ? "" : "s"}
        </span>
        <span className="casebook-pill muted">
          {run.merge_evidence.applied_patch_count} patch
          {run.merge_evidence.applied_patch_count === 1 ? "" : "es"}
        </span>
        <a
          className="casebook-pill"
          href={apiUrl(`/api/segmentation/runs/${run.run_id}/exports/final_transcript.txt`)}
        >
          final transcript
        </a>
        <a
          className="casebook-pill"
          href={apiUrl(`/api/segmentation/runs/${run.run_id}/exports/evidence.json`)}
        >
          evidence json
        </a>
      </div>
      <div className="mt-3">
        <div className="mb-2 text-xs font-semibold uppercase text-[#5f594f]">
          Merged draft
        </div>
        <pre className="transcript-preview">{run.merged_draft}</pre>
      </div>
      <details className="subtle-details">
        <summary>Specialist packets and patch evidence</summary>
        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          <div>
            <div className="mb-2 text-xs font-semibold uppercase text-[#5f594f]">
              Rule plan
            </div>
            <div className="space-y-2">
              {run.rule_plan.map((packet) => (
                <div
                  key={packet.specialist_id}
                  className="rounded-md border border-[#d9d4c5] bg-white/80 p-2"
                >
                  <div className="text-sm font-semibold text-[#2f413f]">
                    {packet.specialist_id}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {packet.rule_ids.map((ruleId) => (
                      <span key={ruleId} className="casebook-pill muted">
                        {ruleId}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div>
            <div className="mb-2 text-xs font-semibold uppercase text-[#5f594f]">
              Specialist patches
            </div>
            <div className="space-y-2">
              {run.specialist_outputs.map((output) => (
                <div
                  key={output.specialist_id}
                  className="rounded-md border border-[#d9d4c5] bg-white/80 p-2"
                >
                  <div className="text-sm font-semibold text-[#2f413f]">
                    {output.specialist_id}
                  </div>
                  <a
                    className="mt-1 inline-block text-xs font-semibold text-[#2f5b50] underline"
                    href={apiUrl(
                      `/api/segmentation/runs/${run.run_id}/specialists/${output.specialist_id}.html`
                    )}
                  >
                    open specialist packet
                  </a>
                  {output.patches.slice(0, 3).map((patch, index) => (
                    <div
                      key={`${output.specialist_id}-${patch.event_index}-${index}`}
                      className="mt-1 font-mono text-xs text-[#5f594f]"
                    >
                      {patch.operation}@{patch.event_index}: {patch.text}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        </div>
      </details>
      {run.failure_routes.length ? (
        <div className="mt-3 space-y-2">
          {run.failure_routes.map((route) => (
            <div key={`${route.rule_id}-${route.specialist_id}`} className="error-row">
              <AlertTriangle size={15} />
              <div>
                <div className="font-semibold">
                  {route.rule_id} {"->"} {route.specialist_id}
                </div>
                <div>{route.message}</div>
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function SegmentationEvaluationPanel({
  evaluation
}: {
  evaluation: SegmentationEvaluation;
}) {
  const metrics = evaluation.metrics;
  return (
    <div className="rounded-md border border-[#d9d4c5] bg-[#fffdf8] p-3">
      <div className="grid gap-3 sm:grid-cols-4">
        <OutputFact label="Score" value={String(evaluation.score)} />
        <OutputFact label="Utterances" value={String(metrics.utterance_count)} />
        <OutputFact label="Times" value={String(metrics.time_marker_count)} />
        <OutputFact label="Pauses" value={String(metrics.pause_marker_count)} />
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <div>
          <div className="mb-2 text-xs font-semibold uppercase text-[#5f594f]">
            Speakers
          </div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(metrics.speaker_counts).map(([speaker, count]) => (
              <span key={speaker} className="casebook-pill">
                {speaker}: {count}
              </span>
            ))}
          </div>
        </div>
        <div>
          <div className="mb-2 text-xs font-semibold uppercase text-[#5f594f]">
            Notation
          </div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(metrics.special_notation_counts).map(([key, count]) => (
              <span key={key} className="casebook-pill muted">
                {key}: {count}
              </span>
            ))}
          </div>
        </div>
      </div>
      {evaluation.failures.length ? (
        <div className="mt-3 space-y-2">
          {evaluation.failures.map((failure, index) => (
            <div key={`${failure.rule_id}-${index}`} className="error-row">
              <AlertTriangle size={15} />
              <div>
                <div className="font-semibold">{failure.rule_id}</div>
                <div>{failure.message}</div>
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function RecentRunsPanel({ runs }: { runs: RunHistoryItem[] }) {
  return (
    <Panel title="7. Recent Local Runs" icon={<Database size={18} />}>
      {runs.length ? (
        <div className="recent-runs-list">
          {runs.slice(0, 5).map((item) => (
            <div key={item.run_id} className="recent-run-row">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-[#171717]">
                  {item.source_filename}
                </div>
                <div className="mt-1 font-mono text-xs text-[#756f64]">
                  {item.run_id.slice(0, 12)} · {item.metric_count} metric
                  {item.metric_count === 1 ? "" : "s"}
                </div>
              </div>
              <div className="text-right text-xs text-[#756f64]">
                {new Date(item.created_at).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit"
                })}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-sm text-[#676157]">No local runs recorded yet.</div>
      )}
    </Panel>
  );
}

function StudyWorkspacePanel({
  studies,
  selectedStudyId,
  studyName,
  studyDescription,
  casebookParticipantCount,
  casebookConditions,
  casebookWeekCount,
  casebookCustomFields,
  casebookOptions,
  casebookWarnings,
  batchTranscriptText,
  batchTranscripts,
  batchParseError,
  batchUploadStatus,
  studyBatches,
  batch,
  batchRuns,
  selectedBatchRun,
  status,
  onStudyNameChange,
  onStudyDescriptionChange,
  onSelectStudy,
  onStartNewStudy,
  onCasebookParticipantCountChange,
  onCasebookConditionsChange,
  onCasebookWeekCountChange,
  onCasebookCustomFieldsChange,
  onApplyCasebookTemplate,
  onBatchTranscriptTextChange,
  onBatchFilesSelected,
  onCasebookCsvExport,
  onCasebookCsvImport,
  onBatchAssignmentChange,
  onLoadBatch,
  onLoadBatchRun,
  onRunBatch
}: {
  studies: StudyWorkspace[];
  selectedStudyId: string;
  studyName: string;
  studyDescription: string;
  casebookParticipantCount: number;
  casebookConditions: string;
  casebookWeekCount: number;
  casebookCustomFields: string;
  casebookOptions: CasebookOptions;
  casebookWarnings: string[];
  batchTranscriptText: string;
  batchTranscripts: BatchTranscript[];
  batchParseError: string;
  batchUploadStatus: string;
  studyBatches: StudyBatchSummary[];
  batch: StudyBatchResponse | null;
  batchRuns: StudyBatchRunSummary[];
  selectedBatchRun: StudyBatchRunDetail | null;
  status: string;
  onStudyNameChange: (value: string) => void;
  onStudyDescriptionChange: (value: string) => void;
  onSelectStudy: (study: StudyWorkspace) => void;
  onStartNewStudy: () => void;
  onCasebookParticipantCountChange: (value: number) => void;
  onCasebookConditionsChange: (value: string) => void;
  onCasebookWeekCountChange: (value: number) => void;
  onCasebookCustomFieldsChange: (value: string) => void;
  onApplyCasebookTemplate: (templateId: string) => void;
  onBatchTranscriptTextChange: (value: string) => void;
  onBatchFilesSelected: (files: FileList | null) => void;
  onCasebookCsvExport: () => void;
  onCasebookCsvImport: (files: FileList | null) => void;
  onBatchAssignmentChange: (index: number, key: string, value: string) => void;
  onLoadBatch: (batchId: string) => void;
  onLoadBatchRun: (runId: string) => void;
  onRunBatch: () => void;
}) {
  return (
    <Panel title="5. Study Workspace" icon={<Database size={18} />}>
      <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr]">
        <div className="space-y-3">
          <label className="field-label mt-0">
            Study name
            <input
              className="field-input"
              value={studyName}
              onChange={(event) => onStudyNameChange(event.target.value)}
            />
          </label>
          <label className="field-label">
            Description
            <textarea
              className="field-input min-h-20 resize-y"
              value={studyDescription}
              onChange={(event) => onStudyDescriptionChange(event.target.value)}
            />
          </label>
          {studies.length ? (
            <div className="recent-runs-list">
              {studies.slice(0, 3).map((study) => (
                <div key={study.id} className="recent-run-row">
                  <div className="min-w-0">
                    <div className="flex min-w-0 items-center gap-2">
                      <div className="truncate text-sm font-semibold text-[#171717]">
                        {study.name}
                      </div>
                      {selectedStudyId === study.id ? (
                        <span className="casebook-pill">active</span>
                      ) : null}
                    </div>
                    <div className="mt-1 truncate font-mono text-xs text-[#756f64]">
                      {study.id}
                    </div>
                  </div>
                  <button
                    className="small-action-button"
                    type="button"
                    onClick={() => onSelectStudy(study)}
                  >
                    Use
                  </button>
                </div>
              ))}
            </div>
          ) : null}
          {selectedStudyId ? (
            <button className="secondary-button" type="button" onClick={onStartNewStudy}>
              <FileCheck2 size={16} />
              Start new study
            </button>
          ) : null}
          {selectedStudyId && studyBatches.length ? (
            <div className="rounded-md border border-[#d9d4c5] bg-white/70">
              <div className="border-b border-[#e4ded0] px-3 py-2 text-sm font-semibold text-[#2f413f]">
                Batch history
              </div>
              <div className="divide-y divide-[#e4ded0]">
                {studyBatches.slice(0, 4).map((historyBatch) => (
                  <div
                    key={historyBatch.batch_id}
                    className="grid grid-cols-[1fr_auto] items-center gap-3 px-3 py-2"
                  >
                    <div className="min-w-0">
                      <div className="truncate font-mono text-xs text-[#2b2925]">
                        {historyBatch.batch_id}
                      </div>
                      <div className="mt-1 text-xs text-[#756f64]">
                        {historyBatch.run_count} run
                        {historyBatch.run_count === 1 ? "" : "s"} ·{" "}
                        {new Date(historyBatch.created_at).toLocaleString()}
                      </div>
                    </div>
                    <button
                      className="small-action-button"
                      type="button"
                      onClick={() => onLoadBatch(historyBatch.batch_id)}
                    >
                      Load
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          <div className="rounded-md border border-[#d9d4c5] bg-[#faf8f1] p-3">
            <div className="text-sm font-semibold text-[#2f413f]">Casebook design</div>
            <div className="mt-3 grid gap-2 sm:grid-cols-3">
              {Object.values(CASEBOOK_TEMPLATES).map((template) => (
                <button
                  key={template.id}
                  className="template-button"
                  type="button"
                  onClick={() => onApplyCasebookTemplate(template.id)}
                >
                  {template.label}
                </button>
              ))}
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              <label className="field-label mt-0">
                Participants
                <input
                  className="field-input"
                  min={1}
                  max={MAX_STUDY_PARTICIPANTS}
                  type="number"
                  value={casebookParticipantCount}
                  onChange={(event) =>
                    onCasebookParticipantCountChange(Number(event.target.value))
                  }
                />
              </label>
              <label className="field-label mt-0 sm:col-span-2">
                Conditions
                <input
                  className="field-input"
                  value={casebookConditions}
                  onChange={(event) => onCasebookConditionsChange(event.target.value)}
                />
              </label>
              <label className="field-label mt-0">
                Weeks
                <input
                  className="field-input"
                  min={1}
                  type="number"
                  value={casebookWeekCount}
                  onChange={(event) => onCasebookWeekCountChange(Number(event.target.value))}
                />
              </label>
              <label className="field-label mt-0 sm:col-span-2">
                Custom fields
                <input
                  className="field-input"
                  value={casebookCustomFields}
                  onChange={(event) => onCasebookCustomFieldsChange(event.target.value)}
                />
              </label>
              <div className="sm:col-span-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-[#756f64]">
                  Active options
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {[...casebookOptions.participants, ...casebookOptions.conditions, ...casebookOptions.weeks].map(
                    (item) => (
                      <span key={item} className="casebook-pill">
                        {item}
                      </span>
                    )
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
        <div className="space-y-3">
          <label className="batch-upload-zone">
            <input
              className="sr-only"
              type="file"
              accept=".txt,.docx,text/plain,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              multiple
              onChange={(event) => {
                void onBatchFilesSelected(event.target.files);
                event.target.value = "";
              }}
            />
            <span className="batch-upload-icon">
              <FileText size={18} />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-semibold text-[#253735]">
                Select transcript TXT or DOCX files
              </span>
              <span className="mt-1 block text-xs leading-5 text-[#756f64]">
                Filenames like `P1_home_week1.docx` auto-fill participant, condition, and week.
              </span>
            </span>
          </label>
          {batchUploadStatus ? <p className="success-text">{batchUploadStatus}</p> : null}
          <label className="field-label mt-0">
            Batch transcripts
            <textarea
              className="field-input min-h-44 resize-y font-mono text-xs"
              value={batchTranscriptText}
              onChange={(event) => onBatchTranscriptTextChange(event.target.value)}
            />
            <span className="field-helper">
              Separate files with `---`; first line can be `filename | participant_id=P1 | condition=home | week=week_1`.
            </span>
          </label>
          {batchParseError ? <p className="error-text">{batchParseError}</p> : null}
          <FileAssignmentGrid
            transcripts={batchTranscripts}
            options={casebookOptions}
            warnings={casebookWarnings}
            onCsvExport={onCasebookCsvExport}
            onCsvImport={onCasebookCsvImport}
            onAssignmentChange={onBatchAssignmentChange}
          />
          <button className="secondary-button" type="button" onClick={onRunBatch}>
            <Play size={16} />
            Run study batch
          </button>
          {status ? <p className="success-text">{status}</p> : null}
          {batch ? (
            <div className="grid gap-3 md:grid-cols-2">
              <OutputFact label="Batch" value={batch.batch.batch_id.slice(0, 18)} />
              <OutputFact label="Runs" value={String(batch.batch.run_count)} />
              <OutputFact label="Aggregate JSON" value={batch.aggregate_results_json} wide />
              <OutputFact
                label="Exports"
                value={batch.exports.map((item) => item.filename).join(", ")}
                wide
              />
              {batch.study_schema ? (
                <OutputFact
                  label="Persisted schema"
                  value={`${batch.study_schema.participants.join(", ")} · ${batch.study_schema.conditions.join(", ")} · ${batch.study_schema.weeks.length} week(s)`}
                  wide
                />
              ) : null}
            </div>
          ) : null}
          {batch && (batch.failures ?? []).length ? (
            <BatchFailurePanel failures={batch.failures ?? []} />
          ) : null}
          {batchRuns.length ? (
            <div className="mt-4 rounded-md border border-[#d9d4c5] bg-white/75">
              <div className="grid gap-1 border-b border-[#e4ded0] px-3 py-2">
                <div className="text-sm font-semibold text-[#2f413f]">
                  Transcript drilldown
                </div>
                <div className="text-xs text-[#756f64]">
                  Inspect each source file behind the aggregate study tables.
                </div>
              </div>
              <div className="divide-y divide-[#e4ded0]">
                {batchRuns.map((item) => (
                  <div
                    key={item.run_id}
                    className="grid gap-3 px-3 py-2 md:grid-cols-[1fr_auto] md:items-center"
                  >
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-[#171717]">
                        {item.source_filename}
                      </div>
                      <div className="mt-1 flex flex-wrap gap-2 text-xs text-[#756f64]">
                        <span className="font-mono">{item.run_id.slice(0, 12)}</span>
                        <span>{item.turn_count} turn{item.turn_count === 1 ? "" : "s"}</span>
                        <span>{item.metric_ids.length} metric{item.metric_ids.length === 1 ? "" : "s"}</span>
                        {batchRunMetadataLine(item.metadata) ? (
                          <span>{batchRunMetadataLine(item.metadata)}</span>
                        ) : null}
                      </div>
                    </div>
                    <button
                      className="small-action-button justify-self-start md:justify-self-end"
                      type="button"
                      onClick={() => onLoadBatchRun(item.run_id)}
                    >
                      Inspect
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          {selectedBatchRun ? (
            <div className="mt-4 space-y-4 border-t border-[#e4ded0] pt-4">
              <div>
                <div className="text-sm font-semibold text-[#2f413f]">
                  Transcript results: {selectedBatchRun.source_filename}
                </div>
                <p className="mt-1 text-xs text-[#756f64]">
                  Source-level tables use the same skill pack as the loaded aggregate batch.
                </p>
              </div>
              <div className="grid gap-3 md:grid-cols-3">
                <OutputFact label="Run ID" value={selectedBatchRun.run_id.slice(0, 12)} />
                <OutputFact label="Turns" value={String(selectedBatchRun.turn_count)} />
                <OutputFact
                  label="Casebook"
                  value={batchRunMetadataLine(selectedBatchRun.metadata) || "No metadata"}
                />
              </div>
              <SourceEvidence turns={selectedBatchRun.turns} />
              {selectedBatchRun.results.map((result) => (
                <BatchMetricTable key={result.metric_id} result={result} />
              ))}
            </div>
          ) : null}
          {batch?.results.length ? (
            <div className="mt-4 space-y-4 border-t border-[#e4ded0] pt-4">
              <div>
                <div className="text-sm font-semibold text-[#2f413f]">
                  Aggregate comparison tables
                </div>
                <p className="mt-1 text-xs text-[#756f64]">
                  Rows carry file-level casebook metadata for participant, condition, week, and custom fields.
                </p>
              </div>
              {batch.results.map((result) => (
                <div key={result.metric_id} className="space-y-3">
                  <BatchMetricTable result={result} />
                  <MetricMatrixPreview result={result} />
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </Panel>
  );
}

function batchRunMetadataLine(metadata: Record<string, string>): string {
  const preferred = ["participant_id", "condition", "week"]
    .map((key) => metadata[key])
    .filter(Boolean);
  const extras = Object.entries(metadata)
    .filter(([key, value]) => value && !["participant_id", "condition", "week"].includes(key))
    .map(([key, value]) => `${key}: ${value}`);
  return [...preferred, ...extras].join(" / ");
}

function SourceEvidence({
  turns
}: {
  turns: StudyBatchRunDetail["turns"];
}) {
  if (!turns.length) {
    return (
      <div className="rounded-md border border-dashed border-[#c7c0af] bg-[#faf8f1] px-3 py-4 text-sm text-[#676157]">
        No parsed turns were stored for this transcript.
      </div>
    );
  }
  return (
    <div className="rounded-md border border-[#d9d4c5] bg-[#faf8f1]">
      <div className="border-b border-[#e4ded0] px-3 py-2">
        <div className="text-sm font-semibold text-[#2f413f]">Source evidence</div>
        <div className="mt-1 text-xs text-[#756f64]">
          First parsed turns from this transcript. Full content remains local.
        </div>
      </div>
      <div className="divide-y divide-[#e4ded0]">
        {turns.slice(0, 6).map((turn) => (
          <div key={turn.turn_index} className="grid gap-2 px-3 py-2 md:grid-cols-[150px_1fr]">
            <div className="min-w-0">
              <div className="truncate text-xs font-semibold uppercase tracking-wide text-[#47615d]">
                {turn.speaker_label}
              </div>
              <div className="mt-1 font-mono text-xs text-[#756f64]">
                {turn.raw_prefix} · turn {turn.turn_index + 1}
              </div>
            </div>
            <div className="text-sm leading-6 text-[#2b2925]">{turn.text}</div>
          </div>
        ))}
      </div>
      {turns.length > 6 ? (
        <div className="border-t border-[#e4ded0] px-3 py-2 text-xs text-[#756f64]">
          Showing 6 of {turns.length} parsed turns.
        </div>
      ) : null}
    </div>
  );
}

function BatchFailurePanel({
  failures
}: {
  failures: StudyBatchResponse["failures"];
}) {
  return (
    <div className="mt-4 rounded-md border border-[#dec4a8] bg-[#fff8ef]">
      <div className="grid gap-1 border-b border-[#ead7bd] px-3 py-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-[#6f3f17]">
          <AlertTriangle size={16} />
          Files needing review
        </div>
        <div className="text-xs text-[#8a6845]">
          Successful files still generated aggregate tables; these files were skipped.
        </div>
      </div>
      <div className="divide-y divide-[#ead7bd]">
        {failures.map((failure) => (
          <div key={failure.source_filename} className="grid gap-1 px-3 py-2">
            <div className="font-mono text-xs font-semibold text-[#3f2a16]">
              {failure.source_filename}
            </div>
            <div className="text-sm leading-6 text-[#6f3f17]">{failure.error}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function FileAssignmentGrid({
  transcripts,
  options,
  warnings,
  onCsvExport,
  onCsvImport,
  onAssignmentChange
}: {
  transcripts: BatchTranscript[];
  options: CasebookOptions;
  warnings: string[];
  onCsvExport: () => void;
  onCsvImport: (files: FileList | null) => void;
  onAssignmentChange: (index: number, key: string, value: string) => void;
}) {
  if (!transcripts.length) {
    return (
      <div className="rounded-md border border-dashed border-[#c7c0af] bg-[#faf8f1] px-3 py-4 text-sm text-[#676157]">
        File assignments will appear after the batch text contains at least one transcript.
      </div>
    );
  }
  return (
    <div className="rounded-md border border-[#d9d4c5] bg-white/70">
      <div className="grid grid-cols-[1fr_auto] gap-3 border-b border-[#e4ded0] px-3 py-2">
        <div>
          <div className="text-sm font-semibold text-[#2f413f]">File assignment</div>
          <div className="mt-1 text-xs text-[#756f64]">
            Map every transcript to the study casebook before analysis.
          </div>
          {warnings.length ? (
            <div className="mt-2 space-y-1">
              {warnings.slice(0, 4).map((warning) => (
                <div key={warning} className="text-xs font-medium text-[#8a4b24]">
                  {warning}
                </div>
              ))}
            </div>
          ) : null}
        </div>
        <div className="flex flex-col items-end gap-2">
          <div className="rounded-full bg-[#eef5ec] px-2 py-1 font-mono text-xs text-[#2f5b50]">
            {transcripts.length} file{transcripts.length === 1 ? "" : "s"}
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <button className="small-action-button" type="button" onClick={onCsvExport}>
              <Download size={13} />
              CSV
            </button>
            <label className="small-action-button cursor-pointer">
              <input
                className="sr-only"
                type="file"
                accept=".csv,text/csv"
                onChange={(event) => {
                  void onCsvImport(event.target.files);
                  event.target.value = "";
                }}
              />
              <FileUp size={13} />
              Import
            </label>
          </div>
        </div>
      </div>
      <datalist id="casebook-participants">
        {options.participants.map((item) => (
          <option key={item} value={item} />
        ))}
      </datalist>
      <datalist id="casebook-conditions">
        {options.conditions.map((item) => (
          <option key={item} value={item} />
        ))}
      </datalist>
      <datalist id="casebook-weeks">
        {options.weeks.map((item) => (
          <option key={item} value={item} />
        ))}
      </datalist>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-left text-sm">
          <thead>
            <tr>
              <th className="table-head">File</th>
              <th className="table-head">Participant</th>
              <th className="table-head">Condition</th>
              <th className="table-head">Week</th>
              <th className="table-head">Turns preview</th>
            </tr>
          </thead>
          <tbody>
            {transcripts.map((transcript, index) => (
              <tr key={`${transcript.source_filename}-${index}`} className="border-t border-[#e4ded0]">
                <td className="table-cell font-mono text-xs">{transcript.source_filename}</td>
                <td className="px-2 py-2">
                  <input
                    className="assignment-input"
                    list="casebook-participants"
                    value={transcript.metadata?.participant_id ?? ""}
                    onChange={(event) =>
                      onAssignmentChange(index, "participant_id", event.target.value)
                    }
                  />
                </td>
                <td className="px-2 py-2">
                  <input
                    className="assignment-input"
                    list="casebook-conditions"
                    value={transcript.metadata?.condition ?? ""}
                    onChange={(event) =>
                      onAssignmentChange(index, "condition", event.target.value)
                    }
                  />
                </td>
                <td className="px-2 py-2">
                  <input
                    className="assignment-input"
                    list="casebook-weeks"
                    value={transcript.metadata?.week ?? ""}
                    onChange={(event) => onAssignmentChange(index, "week", event.target.value)}
                  />
                </td>
                <td className="table-cell text-xs text-[#756f64]">
                  {transcript.content.split("\n").slice(0, 2).join(" / ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PluginCatalog({
  plugins,
  activeMetrics,
  requests,
  jobs,
  requestTitle,
  requestQuestion,
  requestColumns,
  requestExample,
  requestExpected,
  requestStatus,
  jobStatus,
  onRequestTitleChange,
  onRequestQuestionChange,
  onRequestColumnsChange,
  onRequestExampleChange,
  onRequestExpectedChange,
  onSubmitRequest,
  onQueueBuildJob,
  onUpdateJobStatus,
  onRecordEvidence
}: {
  plugins: MetricPlugin[];
  activeMetrics: MetricId[];
  requests: PluginRequest[];
  jobs: AgentJob[];
  requestTitle: string;
  requestQuestion: string;
  requestColumns: string;
  requestExample: string;
  requestExpected: string;
  requestStatus: string;
  jobStatus: string;
  onRequestTitleChange: (value: string) => void;
  onRequestQuestionChange: (value: string) => void;
  onRequestColumnsChange: (value: string) => void;
  onRequestExampleChange: (value: string) => void;
  onRequestExpectedChange: (value: string) => void;
  onSubmitRequest: () => void;
  onQueueBuildJob: (requestId: string) => void;
  onUpdateJobStatus: (jobId: string, status: string) => void;
  onRecordEvidence: (jobId: string) => void;
}) {
  if (!plugins.length) {
    return null;
  }
  return (
    <div className="plugin-catalog" aria-label="Metric plugin registry">
      <div className="mb-2 flex items-center justify-between gap-3">
        <span className="text-xs font-semibold uppercase text-[#47615d]">
          Plugin registry
        </span>
        <span className="font-mono text-xs text-[#756f64]">{plugins.length} registered</span>
      </div>
      <div className="space-y-2">
        {plugins.map((plugin) => (
          <div key={plugin.id} className="plugin-row">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-[#171717]">
                {plugin.label}
              </div>
              <div className="mt-1 truncate text-xs text-[#756f64]">
                {plugin.category}
              </div>
            </div>
            <span className={activeMetrics.includes(plugin.id) ? "plugin-pill" : "plugin-pill-muted"}>
              {activeMetrics.includes(plugin.id) ? "Active" : "Available"}
            </span>
          </div>
        ))}
      </div>
      <div className="plugin-request-form">
        <div className="text-xs font-semibold uppercase text-[#47615d]">
          Request new metric
        </div>
        <label className="field-label">
          Metric title
          <input
            className="field-input"
            value={requestTitle}
            onChange={(event) => onRequestTitleChange(event.target.value)}
          />
        </label>
        <label className="field-label">
          Research question
          <textarea
            className="field-input min-h-20 resize-y"
            value={requestQuestion}
            onChange={(event) => onRequestQuestionChange(event.target.value)}
          />
        </label>
        <label className="field-label">
          Output columns
          <input
            className="field-input"
            value={requestColumns}
            onChange={(event) => onRequestColumnsChange(event.target.value)}
          />
        </label>
        <label className="field-label">
          Synthetic example
          <textarea
            className="field-input min-h-20 resize-y font-mono text-xs"
            value={requestExample}
            onChange={(event) => onRequestExampleChange(event.target.value)}
          />
        </label>
        <label className="field-label">
          Expected behavior
          <textarea
            className="field-input min-h-20 resize-y"
            value={requestExpected}
            onChange={(event) => onRequestExpectedChange(event.target.value)}
          />
        </label>
        <button className="secondary-button" type="button" onClick={onSubmitRequest}>
          <Sparkles size={16} />
          Save plugin request
        </button>
        {requestStatus ? <p className="success-text">{requestStatus}</p> : null}
      </div>
      {requests.length ? (
        <div className="mt-3 space-y-2">
          <div className="text-xs font-semibold uppercase text-[#47615d]">
            Recent requests
          </div>
          {requests.slice(0, 3).map((request) => (
            <div key={request.id} className="plugin-row">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-[#171717]">
                  {request.title}
                </div>
                <div className="mt-1 truncate font-mono text-xs text-[#756f64]">
                  {request.requested_metric_id}
                </div>
              </div>
              <button
                className="json-upload-button"
                type="button"
                onClick={() => onQueueBuildJob(request.id)}
              >
                Queue job
              </button>
            </div>
          ))}
          {jobStatus ? <p className="success-text">{jobStatus}</p> : null}
        </div>
      ) : null}
      {jobs.length ? (
        <div className="mt-3 space-y-2">
          <div className="text-xs font-semibold uppercase text-[#47615d]">
            Agent jobs
          </div>
          {jobs.slice(0, 3).map((job) => (
            <div key={job.id} className="plugin-row">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-[#171717]">
                  {job.id}
                </div>
                <div className="mt-1 truncate font-mono text-xs text-[#756f64]">
                  {job.branch_name}
                </div>
                <div className="mt-1 truncate font-mono text-xs text-[#756f64]">
                  {job.runbook_path}
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {[
                    ["in_progress", "Start"],
                    ["blocked", "Block"],
                    ["verified", "Verify"],
                    ["merged", "Merge"]
                  ].map(([status, label]) => (
                    <button
                      key={status}
                      className="json-upload-button"
                      type="button"
                      onClick={() => onUpdateJobStatus(job.id, status)}
                    >
                      {label}
                    </button>
                  ))}
                  <button
                    className="json-upload-button"
                    type="button"
                    onClick={() => onRecordEvidence(job.id)}
                  >
                    Evidence
                  </button>
                </div>
              </div>
              <span className="plugin-pill">{job.status}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function RunSummaryStrip({ run }: { run: RunResponse }) {
  const summaryMetrics = Array.from(
    new Set([...orderedMetrics, ...run.results.map((item) => item.metric_id)])
  );
  return (
    <div className="run-summary-strip" aria-label="Run skill summary">
      {summaryMetrics.map((metricId) => {
        const result = run.results.find((item) => item.metric_id === metricId);
        return (
          <div key={metricId} className="run-summary-card">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-xs font-semibold uppercase text-[#47615d]">
                  {metricDescription(metricId)}
                </div>
                <div className="mt-1 truncate text-sm font-semibold text-[#171717]">
                  {metricLabel(metricId)}
                </div>
              </div>
              <span className={result ? "summary-status" : "summary-status summary-status-muted"}>
                {result ? "Complete" : "Skipped"}
              </span>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
              <div>
                <div className="text-[#756f64]">Rows</div>
                <div className="mt-1 font-mono text-sm font-semibold">
                  {result ? result.rows.length : 0}
                </div>
              </div>
              <div>
                <div className="text-[#756f64]">Export</div>
                <div className="mt-1 font-semibold">
                  {run.exports.some((item) => item.metric_id === metricId) ? "CSV" : "None"}
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function DiagnosticsPanel({ run }: { run: RunResponse }) {
  const entries = Object.entries(run.diagnostics.turn_counts);
  return (
    <div className="diagnostics-panel">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase text-[#47615d]">
          Transcript QA
        </span>
        {entries.map(([role, count]) => (
          <span key={role} className="diagnostic-pill">
            {role}: {count}
          </span>
        ))}
      </div>
      {run.diagnostics.warnings.length ? (
        <div className="mt-3 space-y-2">
          {run.diagnostics.warnings.map((warning) => (
            <div key={`${warning.code}-${warning.message}`} className="diagnostic-warning">
              <AlertTriangle size={16} />
              <span>{warning.message}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-2 text-sm text-[#4f625c]">No parser warnings detected.</div>
      )}
    </div>
  );
}

function Panel(props: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-md border border-[#d9d4c5] bg-[#fffdf8] p-4 shadow-[0_1px_0_rgba(0,0,0,0.04)]">
      <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-[#2f413f]">
        {props.icon}
        {props.title}
      </div>
      {props.children}
    </section>
  );
}

function StatusTile(props: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="rounded-md border border-[#d9d4c5] bg-[#fffdf8] p-2.5">
      <div className="flex items-center gap-2 text-[#47615d]">{props.icon}</div>
      <div className="mt-1.5 text-xs uppercase text-[#756f64]">{props.label}</div>
      <div className="mt-1 text-sm font-semibold">{props.value}</div>
    </div>
  );
}

function OutputFact(props: { label: string; value: string; wide?: boolean }) {
  return (
    <div className={props.wide ? "md:col-span-3" : ""}>
      <div className="text-xs uppercase text-[#756f64]">{props.label}</div>
      <div className="mt-1 overflow-hidden text-ellipsis whitespace-nowrap rounded bg-[#f3f0e7] px-2 py-1 font-mono text-xs">
        {props.value}
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex min-h-40 items-center justify-center rounded-md border border-dashed border-[#c7c0af] bg-[#faf8f1] text-sm text-[#676157]">
      Analysis results will appear here after a local run.
    </div>
  );
}

function MetricTable({
  result,
  downloadUrl
}: {
  result: MetricResult;
  downloadUrl?: string;
}) {
  const columns = Array.from(new Set(result.rows.flatMap((row) => Object.keys(row))));
  return (
    <Panel title={result.label} icon={<TableProperties size={18} />}>
      {downloadUrl ? (
        <div className="mb-3 flex justify-end">
          <a className="export-link" href={apiUrl(downloadUrl)}>
            <Download size={16} />
            CSV
          </a>
        </div>
      ) : null}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[680px] border-collapse text-left text-sm">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column} className="table-head">
                  {column.replaceAll("_", " ")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, index) => (
              <tr key={index} className="border-t border-[#e4ded0]">
                {columns.map((column) => (
                  <td key={column} className="table-cell">
                    {formatCell(row[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function BatchMetricTable({ result }: { result: MetricResult }) {
  const columns = Array.from(new Set(result.rows.flatMap((row) => Object.keys(row))));
  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="text-sm font-semibold text-[#171717]">{result.label}</div>
        <div className="font-mono text-xs text-[#756f64]">
          {result.rows.length} row{result.rows.length === 1 ? "" : "s"}
        </div>
      </div>
      <div className="overflow-x-auto rounded-md border border-[#e4ded0]">
        <table className="w-full min-w-[760px] border-collapse text-left text-xs">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column} className="table-head">
                  {column.replaceAll("_", " ")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, index) => (
              <tr key={index} className="border-t border-[#e4ded0]">
                {columns.map((column) => (
                  <td key={column} className="table-cell">
                    {formatCell(row[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MetricMatrixPreview({ result }: { result: MetricResult }) {
  const valueKey = firstNumericMetricKey(result);
  if (!valueKey) {
    return null;
  }
  const matrix = buildMetricMatrix(result, valueKey);
  if (!matrix.rows.length || !matrix.weekColumns.length) {
    return null;
  }
  return (
    <div className="rounded-md border border-[#d9d4c5] bg-[#faf8f1]">
      <div className="grid gap-1 border-b border-[#e4ded0] px-3 py-2">
        <div className="text-sm font-semibold text-[#2f413f]">
          Matrix view: {valueKey.replaceAll("_", " ")}
        </div>
        <div className="text-xs text-[#756f64]">
          Participant and condition rows compared across study weeks.
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] border-collapse text-left text-xs">
          <thead>
            <tr>
              <th className="table-head">Participant</th>
              <th className="table-head">Condition</th>
              {matrix.weekColumns.map((week) => (
                <th key={week} className="table-head">
                  {week.replaceAll("_", " ")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.rows.map((row) => (
              <tr
                key={`${row.participant_id}-${row.condition}`}
                className="border-t border-[#e4ded0]"
              >
                <td className="table-cell font-mono text-xs">{row.participant_id}</td>
                <td className="table-cell">{row.condition}</td>
                {matrix.weekColumns.map((week) => (
                  <td key={week} className="table-cell font-mono text-xs">
                    {formatCell(row.cells[week])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function firstNumericMetricKey(result: MetricResult): string | null {
  const excluded = new Set([
    "participant_id",
    "condition",
    "week",
    "source_filename",
    "run_id"
  ]);
  for (const row of result.rows) {
    for (const [key, value] of Object.entries(row)) {
      if (!excluded.has(key) && typeof value === "number" && Number.isFinite(value)) {
        return key;
      }
    }
  }
  return null;
}

function formatCell(value: unknown): string {
  if (Array.isArray(value)) {
    return value.join(", ");
  }
  if (value === null || value === undefined) {
    return "";
  }
  return String(value);
}

function metricLabel(metricId: string): string {
  return metricLabels[metricId] ?? metricId.replaceAll("_", " ");
}

function metricDescription(metricId: string): string {
  return metricDescriptions[metricId] ?? "Dynamic skill";
}
