import {
  Activity,
  AlertTriangle,
  Database,
  FileCheck2,
  FileText,
  FlaskConical,
  Loader2,
  Play,
  Download,
  ShieldCheck,
  TableProperties
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  apiUrl,
  createAnalysisRun,
  createTextAnalysisRun,
  listRuns,
  loadSkillPack
} from "./api";
import type { MetricId, MetricResult, RunHistoryItem, RunResponse, SkillPack } from "./types";

const metricLabels: Record<MetricId, string> = {
  base_metrics: "Base metrics",
  lexical_metrics: "Lexical metrics",
  disfluency_metrics: "Disfluencies"
};

const metricDescriptions: Record<MetricId, string> = {
  base_metrics: "Turn structure",
  lexical_metrics: "Lexical profile",
  disfluency_metrics: "Speech markers"
};

const orderedMetrics: MetricId[] = [
  "base_metrics",
  "lexical_metrics",
  "disfluency_metrics"
];

export function App() {
  const [skillPack, setSkillPack] = useState<SkillPack | null>(null);
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
  const [error, setError] = useState("");
  const [isRunning, setIsRunning] = useState(false);

  useEffect(() => {
    loadSkillPack()
      .then((pack) => {
        setSkillPack(pack);
        setDisfluencyText(pack.disfluency_tokens.join(", "));
      })
      .catch((err: Error) => setError(err.message));
    listRuns()
      .then(setRunHistory)
      .catch(() => setRunHistory([]));
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
              disfluencyTokens
            })
          : await createTextAnalysisRun({
              content: pastedTranscript,
              sourceFilename: `${participantId || "pasted"}_pasted_transcript.txt`,
              participantId,
              speakerPrefixes,
              selectedMetrics,
              disfluencyTokens
            });
      setRun(response);
      setRunHistory(await listRuns());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analysis failed");
    } finally {
      setIsRunning(false);
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
    setParticipantId(nextParticipantId);
    setCaregiverPrefix(`${nextParticipantId}_c`);
    setParticipantPrefix(`${nextParticipantId}_p`);
  }

  return (
    <main className="min-h-screen bg-[#f6f5ef] text-[#171717]">
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
            <StatusTile icon={<Activity size={18} />} label="Skills" value="3 demo metrics" />
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

            <Panel title="2. Skills" icon={<TableProperties size={18} />}>
              <div className="space-y-2">
                {(skillPack?.metrics ?? selectedMetrics).map((metric) => (
                  <label key={metric} className="metric-toggle">
                    <input
                      type="checkbox"
                      checked={selectedMetrics.includes(metric)}
                      onChange={() => toggleMetric(metric)}
                    />
                    <span>{metricLabels[metric]}</span>
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
              {error ? <p className="error-text">{error}</p> : null}
            </Panel>
          </aside>

          <section className="space-y-4">
            <Panel title="3. Run Output" icon={<Database size={18} />}>
              {run ? (
                <div className="space-y-4">
                  <RunSummaryStrip run={run} />
                  <DiagnosticsPanel run={run} />
                  <div className="grid gap-3 md:grid-cols-3">
                    <OutputFact label="Run ID" value={run.run_id.slice(0, 12)} />
                    <OutputFact label="Transcript" value={run.source_filename} />
                    <OutputFact label="Turns" value={String(run.turn_count)} />
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
            <RecentRunsPanel runs={runHistory} />
          </section>
        </section>
      </div>
    </main>
  );
}

function RecentRunsPanel({ runs }: { runs: RunHistoryItem[] }) {
  return (
    <Panel title="4. Recent Local Runs" icon={<Database size={18} />}>
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

function RunSummaryStrip({ run }: { run: RunResponse }) {
  return (
    <div className="run-summary-strip" aria-label="Run skill summary">
      {orderedMetrics.map((metricId) => {
        const result = run.results.find((item) => item.metric_id === metricId);
        return (
          <div key={metricId} className="run-summary-card">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-xs font-semibold uppercase text-[#47615d]">
                  {metricDescriptions[metricId]}
                </div>
                <div className="mt-1 truncate text-sm font-semibold text-[#171717]">
                  {metricLabels[metricId]}
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
