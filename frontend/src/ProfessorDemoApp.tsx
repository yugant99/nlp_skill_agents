import { useRef, useState, type ReactNode } from "react";
import {
  ArrowRight,
  Check,
  CircleDollarSign,
  Cloud,
  Database,
  FileCheck2,
  LoaderCircle,
  LockKeyhole,
  RefreshCcw,
  RotateCcw,
  ShieldCheck
} from "lucide-react";
import {
  PROFESSOR_DEMO_SAMPLE,
  PROFESSOR_DEMO_SPECIALISTS,
  acceptProfessorDemoRun,
  canAcceptProfessorDemoRun,
  formatProfessorDemoCost,
  loadLatestProfessorDemoRun,
  revertProfessorDemoRun,
  runProfessorDemo,
  type ProfessorDemoRun,
  type ProfessorDemoSpecialistId,
  type ProfessorDemoSpecialistRun
} from "./professorDemo";
import "./professor-demo.css";

type RunState = "idle" | "running" | "done";
type MutationState = "idle" | "accepting" | "reverting" | "loading";

export function ProfessorDemoApp() {
  const [transcript, setTranscript] = useState(PROFESSOR_DEMO_SAMPLE);
  const [run, setRun] = useState<ProfessorDemoRun | null>(null);
  const [runState, setRunState] = useState<RunState>("idle");
  const [mutationState, setMutationState] = useState<MutationState>("idle");
  const [error, setError] = useState("");
  const runLock = useRef(false);
  const mutationLock = useRef(false);

  const canAccept = canAcceptProfessorDemoRun(run);
  const activeRevision = run?.revision_state.active_revision_number ?? 0;

  async function handleRun() {
    if (runLock.current) {
      return;
    }
    runLock.current = true;
    setRunState("running");
    setRun(null);
    setError("");
    try {
      const nextRun = await runProfessorDemo(transcript);
      setRun(nextRun);
      if (nextRun.status === "failed") {
        setError(nextRun.failure_message ?? "The four-specialist run did not complete.");
      }
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setRunState("done");
      runLock.current = false;
    }
  }

  async function handleAccept() {
    if (!run || !canAccept || mutationLock.current) {
      return;
    }
    mutationLock.current = true;
    setMutationState("accepting");
    setError("");
    try {
      setRun(await acceptProfessorDemoRun(run.run_id));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setMutationState("idle");
      mutationLock.current = false;
    }
  }

  async function handleRevert() {
    if (!run || activeRevision !== 1 || mutationLock.current) {
      return;
    }
    mutationLock.current = true;
    setMutationState("reverting");
    setError("");
    try {
      setRun(await revertProfessorDemoRun(run.run_id));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setMutationState("idle");
      mutationLock.current = false;
    }
  }

  async function handleLoadAccepted() {
    if (mutationLock.current || runLock.current) {
      return;
    }
    mutationLock.current = true;
    setMutationState("loading");
    setError("");
    try {
      const latest = await loadLatestProfessorDemoRun();
      if (!latest) {
        setError("No accepted local demo revision exists yet.");
        return;
      }
      setRun(latest);
      setTranscript(latest.original_transcript);
      setRunState("done");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setMutationState("idle");
      mutationLock.current = false;
    }
  }

  function handleLoadSample() {
    if (runState === "running") {
      return;
    }
    setTranscript(PROFESSOR_DEMO_SAMPLE);
    setRun(null);
    setRunState("idle");
    setError("");
  }

  function handleClearView() {
    if (runState === "running") {
      return;
    }
    setRun(null);
    setRunState("idle");
    setError("");
  }

  return (
    <main className="professor-demo-shell">
      <header className="professor-demo-header">
        <div className="professor-demo-eyebrow">
          <span className="professor-demo-live-dot" aria-hidden="true" />
          Synthetic professor demo
        </div>
        <div className="professor-demo-heading-row">
          <div>
            <h1>Four specialists. One accountable transcript.</h1>
            <p>
              A bounded proof that specialist agents can work independently, return strict
              results, and be combined locally under human control.
            </p>
          </div>
          <div className="professor-demo-route-badge">
            <ShieldCheck size={17} strokeWidth={1.8} />
            Local orchestrator
          </div>
        </div>
      </header>

      <section className="professor-demo-proof-strip" aria-label="Demo contract">
        <ProofStep icon={<Cloud size={16} />} number="01" label="4 Luna calls" />
        <ArrowRight size={15} aria-hidden="true" />
        <ProofStep icon={<FileCheck2 size={16} />} number="02" label="Strict JSON" />
        <ArrowRight size={15} aria-hidden="true" />
        <ProofStep icon={<Database size={16} />} number="03" label="Local merge" />
        <ArrowRight size={15} aria-hidden="true" />
        <ProofStep icon={<LockKeyhole size={16} />} number="04" label="Human accept" />
      </section>

      <div className="professor-demo-layout">
        <section className="professor-demo-card professor-demo-input-card">
          <div className="professor-demo-section-heading">
            <div>
              <span className="professor-demo-kicker">Input</span>
              <h2>Short synthetic transcript</h2>
            </div>
            <span className="professor-demo-line-count">
              {transcript.split("\n").filter(Boolean).length} lines
            </span>
          </div>

          <div className="professor-demo-warning">
            <ShieldCheck size={16} aria-hidden="true" />
            <span>
              Synthetic input only. Each run sends this text to OpenRouter in exactly four
              remote specialist calls. Do not paste research data.
            </span>
          </div>

          <label className="professor-demo-label" htmlFor="synthetic-transcript">
            Transcript
          </label>
          <textarea
            id="synthetic-transcript"
            value={transcript}
            onChange={(event) => setTranscript(event.target.value)}
            disabled={runState === "running"}
            spellCheck={false}
          />

          <div className="professor-demo-input-actions">
            <button
              className="professor-demo-button professor-demo-button-primary"
              type="button"
              onClick={handleRun}
              disabled={runState === "running" || transcript.trim().length === 0}
            >
              {runState === "running" ? (
                <>
                  <LoaderCircle className="professor-demo-spinner" size={17} />
                  Running four specialists
                </>
              ) : (
                <>
                  Run exactly four Luna calls
                  <ArrowRight size={17} />
                </>
              )}
            </button>
            <button
              className="professor-demo-button professor-demo-button-quiet"
              type="button"
              onClick={handleLoadSample}
              disabled={runState === "running"}
            >
              <RefreshCcw size={15} />
              Load sample
            </button>
          </div>
          <p className="professor-demo-cost-note">
            Hard preflight ceiling: $0.25. No retry, fallback, repair, or fifth model call.
          </p>
        </section>

        <section className="professor-demo-card professor-demo-specialists-card">
          <div className="professor-demo-section-heading">
            <div>
              <span className="professor-demo-kicker">Execution</span>
              <h2>Four bounded specialists</h2>
            </div>
            <span className={run?.status === "completed" ? "status-pill success" : "status-pill"}>
              {runState === "running"
                ? "4 calls in progress"
                : run?.status === "completed"
                  ? "4 / 4 valid"
                  : run?.status === "failed"
                    ? `${run.receipt.valid_result_count} / 4 valid`
                    : "Ready"}
            </span>
          </div>

          <div className="professor-demo-specialist-grid">
            {PROFESSOR_DEMO_SPECIALISTS.map((specialist, index) => (
              <SpecialistCard
                key={specialist.id}
                index={index + 1}
                id={specialist.id}
                label={specialist.label}
                description={specialist.description}
                runState={runState}
                result={run?.specialists.find(
                  (item) => item.specialist_id === specialist.id
                )}
              />
            ))}
          </div>
        </section>
      </div>

      <section className="professor-demo-comparison" aria-label="Transcript comparison">
        <TranscriptPane
          eyebrow="Revision 0 · immutable"
          title="Original synthetic input"
          transcript={run?.original_transcript ?? transcript}
        />
        <TranscriptPane
          eyebrow="Candidate · locally composed"
          title="Merged output transcript"
          transcript={
            run?.merged_transcript ??
            (runState === "running"
              ? "Waiting for four strict specialist results…"
              : "Run the four specialists to create a candidate transcript.")
          }
          candidate
        />
      </section>

      {run ? (
        <section className="professor-demo-card professor-demo-receipt-card">
          <div className="professor-demo-section-heading">
            <div>
              <span className="professor-demo-kicker">Receipt</span>
              <h2>Provider usage and local lineage</h2>
            </div>
            <CircleDollarSign size={21} strokeWidth={1.7} aria-hidden="true" />
          </div>
          <div className="professor-demo-receipt-grid">
            <ReceiptFact label="Calls attempted" value={`${run.receipt.attempted_call_count} exactly`} />
            <ReceiptFact label="Strict results" value={`${run.receipt.valid_result_count} / 4`} />
            <ReceiptFact label="Total tokens" value={formatNumber(run.receipt.total_tokens)} />
            <ReceiptFact label="Actual cost" value={formatProfessorDemoCost(run.receipt.total_cost_usd)} />
            <ReceiptFact label="Model" value={run.receipt.model} wide />
            <ReceiptFact
              label="Pinned route"
              value={`${run.receipt.provider} · ${run.receipt.endpoint} · ZDR`}
              wide
            />
            <ReceiptFact label="Run ID" value={run.run_id} wide />
            <ReceiptFact
              label="Candidate SHA-256"
              value={run.revision_state.candidate?.sha256 ?? "Unavailable"}
              wide
            />
            <ReceiptFact
              label="Worst-case preflight"
              value={`${formatProfessorDemoCost(run.receipt.preflight.estimated_max_cost_usd)} < $0.25`}
              wide
            />
            <ReceiptFact
              label="Reasoning tokens"
              value={formatNumber(run.receipt.reasoning_tokens)}
              wide
            />
          </div>
        </section>
      ) : null}

      <section className="professor-demo-card professor-demo-acceptance-card">
        <div className="professor-demo-acceptance-copy" aria-live="polite">
          <div className={`professor-demo-revision-icon ${activeRevision === 1 ? "accepted" : ""}`}>
            {activeRevision === 1 ? <Check size={20} /> : <LockKeyhole size={19} />}
          </div>
          <div>
            <span className="professor-demo-kicker">Human gate</span>
            <h2>
              {activeRevision === 1
                ? "Generated demo revision accepted locally"
                : run?.revision_state.reverted_at
                  ? "Original revision restored"
                  : "No accepted data has changed"}
            </h2>
            <p>
              {activeRevision === 1
                ? "Revision 1 is active. Revision 0 and both SHA-256 digests remain preserved."
                : run?.revision_state.reverted_at
                  ? "Revision 0 is active again; the generated candidate remains available."
                  : "Model output remains a candidate until you explicitly accept it."}
            </p>
          </div>
        </div>
        <div className="professor-demo-acceptance-actions">
          {activeRevision === 1 ? (
            <button
              className="professor-demo-button professor-demo-button-secondary"
              type="button"
              onClick={handleRevert}
              disabled={mutationState !== "idle"}
            >
              <RotateCcw size={16} />
              {mutationState === "reverting" ? "Restoring original" : "Restore original"}
            </button>
          ) : (
            <button
              className="professor-demo-button professor-demo-button-secondary"
              type="button"
              onClick={handleAccept}
              disabled={!canAccept || mutationState !== "idle"}
            >
              <Check size={16} />
              {mutationState === "accepting" ? "Accepting revision" : "Accept as local revision"}
            </button>
          )}
          <button
            className="professor-demo-button professor-demo-button-quiet"
            type="button"
            onClick={handleLoadAccepted}
            disabled={mutationState !== "idle" || runState === "running"}
          >
            <Database size={15} />
            {mutationState === "loading" ? "Loading" : "Reload saved"}
          </button>
          <button
            className="professor-demo-button professor-demo-button-quiet"
            type="button"
            onClick={handleClearView}
            disabled={runState === "running"}
          >
            Clear view
          </button>
        </div>
      </section>

      {error ? (
        <div className="professor-demo-error" role="alert">
          <strong>Demo stopped visibly.</strong>
          <span>{error}</span>
        </div>
      ) : null}

      <footer className="professor-demo-footer">
        <span>Local UI · remote Luna inference · local deterministic merge</span>
        <a href="/">Open the full research workbench</a>
      </footer>
    </main>
  );
}

function ProofStep({
  icon,
  number,
  label
}: {
  icon: ReactNode;
  number: string;
  label: string;
}) {
  return (
    <div className="professor-demo-proof-step">
      {icon}
      <span>{number}</span>
      <strong>{label}</strong>
    </div>
  );
}

function SpecialistCard({
  index,
  label,
  description,
  runState,
  result
}: {
  index: number;
  id: ProfessorDemoSpecialistId;
  label: string;
  description: string;
  runState: RunState;
  result?: ProfessorDemoSpecialistRun;
}) {
  const state = result?.status === "valid" ? "valid" : result ? "error" : runState;
  return (
    <article className={`professor-demo-specialist ${state}`}>
      <div className="professor-demo-specialist-topline">
        <span className="professor-demo-specialist-index">0{index}</span>
        <span className="professor-demo-specialist-status">
          {state === "valid" ? (
            <>
              <Check size={13} /> Strict JSON
            </>
          ) : state === "running" ? (
            <>
              <LoaderCircle className="professor-demo-spinner" size={13} /> Calling Luna
            </>
          ) : state === "error" ? (
            "Failed"
          ) : (
            "Ready"
          )}
        </span>
      </div>
      <h3>{label}</h3>
      <p>{description}</p>
      <div className="professor-demo-specialist-meta">
        <span>{result ? formatNumber(result.receipt.total_tokens) : "—"} tokens</span>
        <span>{result ? formatProfessorDemoCost(result.receipt.cost_usd) : "—"}</span>
      </div>
      {result?.error_message ? (
        <p className="professor-demo-specialist-error">{result.error_message}</p>
      ) : null}
    </article>
  );
}

function TranscriptPane({
  eyebrow,
  title,
  transcript,
  candidate = false
}: {
  eyebrow: string;
  title: string;
  transcript: string;
  candidate?: boolean;
}) {
  return (
    <article className={`professor-demo-transcript-pane ${candidate ? "candidate" : ""}`}>
      <span className="professor-demo-kicker">{eyebrow}</span>
      <h2>{title}</h2>
      <pre>{transcript}</pre>
    </article>
  );
}

function ReceiptFact({
  label,
  value,
  wide = false
}: {
  label: string;
  value: string;
  wide?: boolean;
}) {
  return (
    <div className={`professor-demo-receipt-fact ${wide ? "wide" : ""}`}>
      <span>{label}</span>
      <strong title={value}>{value}</strong>
    </div>
  );
}

function formatNumber(value: number | null): string {
  return value === null ? "Unknown" : value.toLocaleString();
}

function errorMessage(caught: unknown): string {
  return caught instanceof Error ? caught.message : "The professor demo request failed";
}
