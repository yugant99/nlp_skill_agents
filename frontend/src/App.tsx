import {
  Activity,
  AlertTriangle,
  Braces,
  Database,
  FileCheck2,
  FileText,
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
  createAnalysisRun,
  createPluginBuildJob,
  createPluginRequest,
  createStudy,
  createStudyTextBatch,
  createTextAnalysisRun,
  draftSkillPack,
  listAgentJobs,
  listMetricPlugins,
  listPluginRequests,
  listRuns,
  listStudies,
  loadSkillPack,
  refineSkillPack,
  updateAgentJobStatus,
  validateSkillPack,
  validateSkillPackText
} from "./api";
import type {
  AgentJob,
  MetricId,
  MetricResult,
  PluginRequest,
  RunHistoryItem,
  RunResponse,
  SkillPack,
  StudyBatchResponse,
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
  const [studyName, setStudyName] = useState("Demo Healthcare Batch");
  const [studyDescription, setStudyDescription] = useState(
    "Three-transcript demo workspace for aggregate prompting and care-plan metrics."
  );
  const [batchTranscriptText, setBatchTranscriptText] = useState(
    "one.txt\nCG: How did walking feel today?\nP: It hurt after lunch.\n---\n" +
      "two.txt\nCG: Did medication help last night?\nP: Yes, I slept better.\n---\n" +
      "three.txt\nCG: I will call the clinic tomorrow.\nP: Thank you."
  );
  const [studyBatch, setStudyBatch] = useState<StudyBatchResponse | null>(null);
  const [studyWorkspaceStatus, setStudyWorkspaceStatus] = useState("");
  const [error, setError] = useState("");
  const [isRunning, setIsRunning] = useState(false);

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

  async function runStudyWorkspaceBatch() {
    if (!skillPackPayload) {
      setError("Activate a skill pack before running a study batch.");
      return;
    }
    try {
      setError("");
      const transcripts = parseBatchTranscripts(batchTranscriptText);
      const study = await createStudy({
        name: studyName,
        description: studyDescription
      });
      const version = await addStudySkillPackVersion(study.id, skillPackPayload);
      const batch = await createStudyTextBatch({
        studyId: study.id,
        skillPackVersionId: version.version_id,
        transcripts
      });
      setStudyBatch(batch);
      setStudyWorkspaceStatus(
        `Batch complete: ${batch.batch.run_count} run(s), ${batch.batch.failure_count} failure(s)`
      );
      setStudies(await listStudies());
    } catch (err) {
      setStudyWorkspaceStatus("");
      setError(err instanceof Error ? err.message : "Could not run study batch");
    }
  }

  return (
    <main className="app-shell min-h-screen text-[#171717]">
      <div className="mx-auto flex max-w-7xl flex-col gap-5 px-5 py-5 lg:px-8">
        <header className="grid gap-5 border-b border-[#d9d4c5] pb-5 lg:grid-cols-[1fr_360px]">
          <div>
            <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[#47615d]">
              <FlaskConical size={18} />
              Local transcript skill runner
            </div>
            <h1 className="max-w-4xl text-4xl font-semibold tracking-normal text-[#111] md:text-5xl">
              NLP Skill Agents
            </h1>
            <p className="mt-3 max-w-3xl text-base leading-7 text-[#4c4a44]">
              Upload one transcript, choose deterministic research skills, and
              generate local tables ready for dashboard composition.
            </p>
          </div>
          <div className="grid grid-cols-3 gap-2 self-end">
            <StatusTile icon={<ShieldCheck size={18} />} label="Local" value="No cloud I/O" />
            <StatusTile icon={<Database size={18} />} label="Store" value="SQLite + files" />
            <StatusTile icon={<Activity size={18} />} label="Skills" value="Dynamic packs" />
          </div>
        </header>

        <section className="grid gap-5 lg:grid-cols-[380px_1fr]">
          <aside className="space-y-4">
            <Panel title="1. Intake" icon={<FileText size={18} />}>
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

            <Panel title="2. Skill Pack Studio" icon={<Sparkles size={18} />}>
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

            <Panel title="3. Skills" icon={<TableProperties size={18} />}>
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
              />
              {error ? <p className="error-text">{error}</p> : null}
            </Panel>
          </aside>

          <section className="space-y-4">
            <Panel title="4. Run Output" icon={<Database size={18} />}>
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
              studyName={studyName}
              studyDescription={studyDescription}
              batchTranscriptText={batchTranscriptText}
              batch={studyBatch}
              status={studyWorkspaceStatus}
              onStudyNameChange={setStudyName}
              onStudyDescriptionChange={setStudyDescription}
              onBatchTranscriptTextChange={setBatchTranscriptText}
              onRunBatch={runStudyWorkspaceBatch}
            />
            <RecentRunsPanel runs={runHistory} />
          </section>
        </section>
      </div>
    </main>
  );
}

function RecentRunsPanel({ runs }: { runs: RunHistoryItem[] }) {
  return (
    <Panel title="5. Recent Local Runs" icon={<Database size={18} />}>
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
  studyName,
  studyDescription,
  batchTranscriptText,
  batch,
  status,
  onStudyNameChange,
  onStudyDescriptionChange,
  onBatchTranscriptTextChange,
  onRunBatch
}: {
  studies: StudyWorkspace[];
  studyName: string;
  studyDescription: string;
  batchTranscriptText: string;
  batch: StudyBatchResponse | null;
  status: string;
  onStudyNameChange: (value: string) => void;
  onStudyDescriptionChange: (value: string) => void;
  onBatchTranscriptTextChange: (value: string) => void;
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
                    <div className="truncate text-sm font-semibold text-[#171717]">
                      {study.name}
                    </div>
                    <div className="mt-1 truncate font-mono text-xs text-[#756f64]">
                      {study.id}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </div>
        <div className="space-y-3">
          <label className="field-label mt-0">
            Batch transcripts
            <textarea
              className="field-input min-h-44 resize-y font-mono text-xs"
              value={batchTranscriptText}
              onChange={(event) => onBatchTranscriptTextChange(event.target.value)}
            />
            <span className="field-helper">
              Separate files with `---`; first line of each block is the filename.
            </span>
          </label>
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
            </div>
          ) : null}
        </div>
      </div>
    </Panel>
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
  onUpdateJobStatus
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
    <div className="rounded-md border border-[#d9d4c5] bg-[#fffdf8] p-3">
      <div className="flex items-center gap-2 text-[#47615d]">{props.icon}</div>
      <div className="mt-2 text-xs uppercase text-[#756f64]">{props.label}</div>
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

function parseBatchTranscripts(
  value: string
): { source_filename: string; content: string }[] {
  const transcripts = value
    .split(/\n---+\n/g)
    .map((block) => block.trim())
    .filter(Boolean)
    .map((block, index) => {
      const [filenameLine, ...contentLines] = block.split("\n");
      const sourceFilename = filenameLine.trim() || `transcript_${index + 1}.txt`;
      return {
        source_filename: sourceFilename,
        content: contentLines.join("\n").trim()
      };
    })
    .filter((item) => item.content);
  if (!transcripts.length) {
    throw new Error("Add at least one transcript block with a filename and content.");
  }
  return transcripts;
}

function metricDescription(metricId: string): string {
  return metricDescriptions[metricId] ?? "Dynamic skill";
}
