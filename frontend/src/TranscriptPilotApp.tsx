import {
  ArrowRight,
  Ban,
  Check,
  CircleAlert,
  CircleDollarSign,
  Cloud,
  Database,
  Download,
  FileCheck2,
  FileText,
  FileUp,
  LoaderCircle,
  LockKeyhole,
  RefreshCcw,
  RotateCcw,
  ShieldCheck,
  Square,
  Upload,
  UserRound,
  X
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
  type RefObject
} from "react";
import { PROFESSOR_DEMO_SAMPLE } from "./professorDemo";
import {
  PILOT_CANCEL_CONFIRMATION,
  PILOT_COMMIT_CONFIRMATION,
  PILOT_JOB_CONFIRMATION,
  PILOT_RESTORE_CONFIRMATION,
  PILOT_SPECIALISTS,
  canCommitPilotJob,
  cancelPilotJob,
  commitPilotJob,
  createPilotStudy,
  formatPilotCost,
  formatPilotNumber,
  getPilotJob,
  getPilotSource,
  isPilotJobActive,
  isPilotJobCommitted,
  isPilotJobReviewable,
  listPilotSources,
  listPilotStudies,
  pilotExportUrl,
  pilotJobCreatesRevision,
  pilotReviewCounts,
  pilotSourceClassification,
  pilotSourceId,
  pilotSourceInputRevisionId,
  pilotStudyId,
  proposalAction,
  restorePilotSourceOriginal,
  savePilotProposalDecision,
  startPilotJob,
  uploadPilotSource,
  type PilotAuditEvent,
  type PilotClassification,
  type PilotJob,
  type PilotProposal,
  type PilotProposalAction,
  type PilotSource,
  type PilotSpecialistProgress,
  type PilotStudy
} from "./transcriptPilot";
import "./professor-demo.css";
import "./transcript-pilot.css";

const SYNTHETIC_AUTHORIZATION_BASIS = "Synthetic classroom demonstration";
const DEIDENTIFIED_AUTHORIZATION_PLACEHOLDER =
  "Protocol, consent, or supervisor approval covering remote processing";

type LoadState = "loading" | "ready" | "error";
type RequestState = "idle" | "working";

export function TranscriptPilotApp() {
  const initialQuery = useRef(readPilotQuery());
  const [contextState, setContextState] = useState<LoadState>("loading");
  const [sourceState, setSourceState] = useState<LoadState>("ready");
  const [sourceReload, setSourceReload] = useState(0);
  const [studies, setStudies] = useState<PilotStudy[]>([]);
  const [studyId, setStudyId] = useState(initialQuery.current.studyId);
  const [sources, setSources] = useState<PilotSource[]>([]);
  const [sourceId, setSourceId] = useState(initialQuery.current.sourceId);
  const [source, setSource] = useState<PilotSource | null>(null);
  const [job, setJob] = useState<PilotJob | null>(null);
  const [showStudyForm, setShowStudyForm] = useState(false);
  const [studyName, setStudyName] = useState("Professor transcript pilot");
  const [studyDescription, setStudyDescription] = useState(
    "Researcher-supervised transcript revision pilot"
  );
  const [newResearcherId, setNewResearcherId] = useState("researcher_01");
  const [newResearcherName, setNewResearcherName] = useState("");
  const [researcherId, setResearcherId] = useState("");
  const [researcherName, setResearcherName] = useState("");
  const [createStudyState, setCreateStudyState] = useState<RequestState>("idle");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [classification, setClassification] =
    useState<PilotClassification>("synthetic");
  const [authorizationBasis, setAuthorizationBasis] = useState(
    SYNTHETIC_AUTHORIZATION_BASIS
  );
  const [egressAuthorized, setEgressAuthorized] = useState(false);
  const [noDirectIdentifiers, setNoDirectIdentifiers] = useState(false);
  const [uploadState, setUploadState] = useState<RequestState>("idle");
  const [authorizedCost, setAuthorizedCost] = useState("0.25");
  const [jobAuthorized, setJobAuthorized] = useState(false);
  const [jobRequestState, setJobRequestState] = useState<RequestState>("idle");
  const [cancelState, setCancelState] = useState<RequestState>("idle");
  const [commitState, setCommitState] = useState<RequestState>("idle");
  const [restoreState, setRestoreState] = useState<RequestState>("idle");
  const [savingProposals, setSavingProposals] = useState<Set<string>>(new Set());
  const [proposalErrors, setProposalErrors] = useState<Record<string, string>>({});
  const [pollError, setPollError] = useState("");
  const [pollRetry, setPollRetry] = useState(0);
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const reviewHeadingRef = useRef<HTMLHeadingElement>(null);
  const revisionHeadingRef = useRef<HTMLHeadingElement>(null);
  const previousJobStatus = useRef<string | null>(null);
  const runLock = useRef(false);
  const mutationLock = useRef(false);
  const idempotencyKey = useRef<string | null>(null);

  const selectedStudy = useMemo(
    () => studies.find((item) => pilotStudyId(item) === studyId) ?? null,
    [studies, studyId]
  );
  const reviewCounts = pilotReviewCounts(job);
  const createsRevision = pilotJobCreatesRevision(job);
  const expectedActiveRevisionId = jobActiveRevisionId(job, source);
  const canCommit =
    canCommitPilotJob(job, savingProposals.size) && Boolean(expectedActiveRevisionId);
  const activeJob = isPilotJobActive(job);
  const reviewableJob = isPilotJobReviewable(job);
  const committedJob = isPilotJobCommitted(job);
  const originalIsActive = sourceOriginalIsActive(job, source);
  const currentStep = committedJob ? 4 : reviewableJob ? 3 : source ? 2 : 1;
  const selectedSourceRevision = source ? pilotSourceInputRevisionId(source) : "";

  useEffect(() => {
    void loadStudies();
  }, []);

  useEffect(() => {
    if (!selectedStudy) {
      setResearcherId("");
      setResearcherName("");
      return;
    }
    if (sourceId) return;
    const researcher = studyResearcher(selectedStudy);
    setResearcherId(researcher.id);
    setResearcherName(researcher.name);
  }, [selectedStudy, sourceId]);

  useEffect(() => {
    if (!source?.researcher_id) return;
    const owner = selectedStudy?.researchers?.find(
      (item) => item.researcher_id === source.researcher_id
    );
    setResearcherId(source.researcher_id);
    setResearcherName(
      source.researcher_name || owner?.display_name || source.researcher_id
    );
  }, [selectedStudy, source?.researcher_id, source?.researcher_name]);

  useEffect(() => {
    if (!studyId) {
      setSources([]);
      setSource(null);
      setSourceId("");
      return;
    }
    let ignored = false;
    setSourceState("loading");
    setError("");
    void listPilotSources(studyId)
      .then((nextSources) => {
        if (ignored) return;
        setSources(nextSources);
        setSourceState("ready");
        setSourceId((current) => {
          if (current && nextSources.some((item) => pilotSourceId(item) === current)) {
            return current;
          }
          const requested = initialQuery.current.sourceId;
          if (requested && nextSources.some((item) => pilotSourceId(item) === requested)) {
            return requested;
          }
          return "";
        });
      })
      .catch((caught) => {
        if (ignored) return;
        setSources([]);
        setSourceState("error");
        setError(errorMessage(caught));
      });
    return () => {
      ignored = true;
    };
  }, [studyId, sourceReload]);

  useEffect(() => {
    if (!sourceId) {
      setSource(null);
      return;
    }
    let ignored = false;
    setSourceState("loading");
    void getPilotSource(sourceId)
      .then((nextSource) => {
        if (ignored) return;
        setSource(nextSource);
        setSourceState("ready");
      })
      .catch((caught) => {
        if (ignored) return;
        setSourceState("error");
        setError(errorMessage(caught));
      });
    return () => {
      ignored = true;
    };
  }, [sourceId, sourceReload]);

  useEffect(() => {
    const requestedJobId = initialQuery.current.jobId;
    if (!requestedJobId) return;
    let ignored = false;
    void getPilotJob(requestedJobId)
      .then((nextJob) => {
        if (ignored) return;
        setJob(nextJob);
        setSourceId(nextJob.source_id);
        if (nextJob.study_id) setStudyId(nextJob.study_id);
      })
      .catch((caught) => {
        if (!ignored) setError(errorMessage(caught));
      });
    return () => {
      ignored = true;
    };
  }, []);

  useEffect(() => {
    if (!job || !isPilotJobActive(job)) return;
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const nextJob = await getPilotJob(job.job_id);
        if (stopped) return;
        setJob(nextJob);
        setPollError("");
        if (isPilotJobActive(nextJob)) {
          timer = window.setTimeout(poll, 1000);
        }
      } catch (caught) {
        if (!stopped) setPollError(errorMessage(caught));
      }
    };
    timer = window.setTimeout(poll, 850);
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [job?.job_id, pollRetry]);

  useEffect(() => {
    if (!error) return;
    errorRef.current?.focus();
  }, [error]);

  useEffect(() => {
    const nextStatus = job?.status ?? null;
    const priorStatus = previousJobStatus.current;
    if (nextStatus === "review_ready" && priorStatus !== "review_ready") {
      reviewHeadingRef.current?.focus();
    }
    if (
      nextStatus &&
      ["committed", "accepted", "restored"].includes(nextStatus) &&
      !priorStatus?.match(/committed|accepted|restored/)
    ) {
      revisionHeadingRef.current?.focus();
    }
    previousJobStatus.current = nextStatus;
  }, [job?.status]);

  async function loadStudies() {
    setContextState("loading");
    setError("");
    try {
      const nextStudies = await listPilotStudies();
      setStudies(nextStudies);
      setContextState("ready");
      setStudyId((current) => {
        if (current && nextStudies.some((item) => pilotStudyId(item) === current)) {
          return current;
        }
        const requested = initialQuery.current.studyId;
        if (requested && nextStudies.some((item) => pilotStudyId(item) === requested)) {
          return requested;
        }
        return nextStudies.length === 1 ? pilotStudyId(nextStudies[0]) : "";
      });
      if (nextStudies.length === 0) setShowStudyForm(true);
    } catch (caught) {
      setContextState("error");
      setError(errorMessage(caught));
    }
  }

  async function handleCreateStudy(event: FormEvent) {
    event.preventDefault();
    if (
      !studyName.trim() ||
      !studyDescription.trim() ||
      !newResearcherId.trim() ||
      !newResearcherName.trim() ||
      mutationLock.current
    ) {
      return;
    }
    mutationLock.current = true;
    setCreateStudyState("working");
    setError("");
    try {
      const nextStudy = await createPilotStudy({
        name: studyName.trim(),
        description: studyDescription.trim(),
        researcher_id: newResearcherId.trim(),
        researcher_name: newResearcherName.trim()
      });
      const nextStudyId = pilotStudyId(nextStudy);
      setStudies((current) => [nextStudy, ...current.filter((item) => pilotStudyId(item) !== nextStudyId)]);
      setStudyId(nextStudyId);
      setShowStudyForm(false);
      updatePilotQuery({ studyId: nextStudyId, sourceId: "", jobId: "" });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setCreateStudyState("idle");
      mutationLock.current = false;
    }
  }

  function handleStudyChange(nextStudyId: string) {
    setStudyId(nextStudyId);
    setSourceId("");
    setSource(null);
    setJob(null);
    setJobAuthorized(false);
    idempotencyKey.current = null;
    updatePilotQuery({ studyId: nextStudyId, sourceId: "", jobId: "" });
  }

  function handleFile(file: File | null) {
    setError("");
    if (!file) {
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    const extension = file.name.toLowerCase().split(".").pop();
    if (extension !== "txt" && extension !== "docx") {
      setSelectedFile(null);
      setError("Choose one TXT or DOCX transcript file.");
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    setSelectedFile(file);
  }

  function handleLoadSample() {
    const sample = new File([PROFESSOR_DEMO_SAMPLE], "professor-sample.txt", {
      type: "text/plain"
    });
    setSelectedFile(sample);
    setClassification("synthetic");
    setAuthorizationBasis(SYNTHETIC_AUTHORIZATION_BASIS);
    setEgressAuthorized(false);
    setNoDirectIdentifiers(false);
    setError("");
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handleClassification(next: PilotClassification) {
    setClassification(next);
    setAuthorizationBasis(
      next === "synthetic" ? SYNTHETIC_AUTHORIZATION_BASIS : ""
    );
    setEgressAuthorized(false);
    setNoDirectIdentifiers(false);
  }

  async function handleUpload(event: FormEvent) {
    event.preventDefault();
    const deidentifiedReady =
      classification === "synthetic" || noDirectIdentifiers;
    if (
      !studyId ||
      !researcherId ||
      !selectedFile ||
      !authorizationBasis.trim() ||
      !egressAuthorized ||
      !deidentifiedReady ||
      mutationLock.current
    ) {
      return;
    }
    mutationLock.current = true;
    setUploadState("working");
    setError("");
    try {
      const nextSource = await uploadPilotSource(studyId, {
        file: selectedFile,
        researcher_id: researcherId,
        data_classification: classification,
        authorization_basis: authorizationBasis.trim()
      });
      const nextSourceId = pilotSourceId(nextSource);
      setSources((current) => [nextSource, ...current.filter((item) => pilotSourceId(item) !== nextSourceId)]);
      setSource(nextSource);
      setSourceId(nextSourceId);
      setSelectedFile(null);
      setEgressAuthorized(false);
      setNoDirectIdentifiers(false);
      setJob(null);
      idempotencyKey.current = null;
      if (fileInputRef.current) fileInputRef.current.value = "";
      updatePilotQuery({ studyId, sourceId: nextSourceId, jobId: "" });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setUploadState("idle");
      mutationLock.current = false;
    }
  }

  async function handleSourceChange(nextSourceId: string) {
    setSourceId(nextSourceId);
    setSource(null);
    setJob(null);
    setJobAuthorized(false);
    idempotencyKey.current = null;
    updatePilotQuery({ studyId, sourceId: nextSourceId, jobId: "" });
  }

  async function handleStartJob() {
    if (
      !source ||
      !researcherId ||
      !selectedSourceRevision ||
      !jobAuthorized ||
      !validCost(authorizedCost) ||
      runLock.current
    ) {
      return;
    }
    runLock.current = true;
    setJobRequestState("working");
    setError("");
    setPollError("");
    const requestKey = idempotencyKey.current ?? createIdempotencyKey();
    idempotencyKey.current = requestKey;
    try {
      const nextJob = await startPilotJob(pilotSourceId(source), {
        researcher_id: researcherId,
        input_revision_id: selectedSourceRevision,
        idempotency_key: requestKey,
        authorized_cost_usd: authorizedCost,
        confirmation: PILOT_JOB_CONFIRMATION
      });
      setJob(nextJob);
      updatePilotQuery({
        studyId,
        sourceId: pilotSourceId(source),
        jobId: nextJob.job_id
      });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setJobRequestState("idle");
      runLock.current = false;
    }
  }

  async function handleCancelJob() {
    if (!job || !isPilotJobActive(job) || mutationLock.current) return;
    mutationLock.current = true;
    setCancelState("working");
    setError("");
    try {
      setJob(
        await cancelPilotJob(job.job_id, {
          researcher_id: researcherId || job.researcher_id,
          confirmation: PILOT_CANCEL_CONFIRMATION
        })
      );
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setCancelState("idle");
      mutationLock.current = false;
    }
  }

  async function handleDecision(
    proposal: PilotProposal,
    action: PilotProposalAction,
    editedText: string | null
  ): Promise<boolean> {
    if (!job || savingProposals.has(proposal.proposal_id)) return false;
    setSavingProposals((current) => new Set(current).add(proposal.proposal_id));
    setProposalErrors((current) => ({ ...current, [proposal.proposal_id]: "" }));
    try {
      const response = await savePilotProposalDecision(job.job_id, proposal.proposal_id, {
        researcher_id: researcherId,
        action,
        edited_text: action === "edit" ? editedText ?? "" : "",
        expected_decision_version: proposal.decision_version
      });
      if (response.job) {
        setJob(response.job);
      } else if (response.proposal) {
        setJob((current) =>
          current
            ? {
                ...current,
                proposals: current.proposals.map((item) =>
                  item.proposal_id === response.proposal?.proposal_id
                    ? response.proposal
                    : item
                ) as PilotProposal[]
              }
            : current
        );
      }
      return true;
    } catch (caught) {
      setProposalErrors((current) => ({
        ...current,
        [proposal.proposal_id]: errorMessage(caught)
      }));
      return false;
    } finally {
      setSavingProposals((current) => {
        const next = new Set(current);
        next.delete(proposal.proposal_id);
        return next;
      });
    }
  }

  async function handleCommit() {
    if (!job || !canCommit || mutationLock.current) return;
    mutationLock.current = true;
    setCommitState("working");
    setError("");
    try {
      const nextJob = await commitPilotJob(job.job_id, {
        researcher_id: researcherId || job.researcher_id,
        expected_active_revision_id: expectedActiveRevisionId,
        confirmation: PILOT_COMMIT_CONFIRMATION
      });
      setJob(nextJob);
      const committedActiveRevisionId =
        nextJob.source?.active_revision_id ||
        nextJob.active_revision_id ||
        nextJob.committed_revision_id ||
        "";
      if (committedActiveRevisionId) {
        setSource((current) =>
          current
            ? { ...current, active_revision_id: committedActiveRevisionId }
            : current
        );
      }
      if (source) {
        try {
          setSource(await getPilotSource(pilotSourceId(source)));
        } catch {
          setPollError("The revision was created, but the source summary could not be refreshed.");
        }
      }
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setCommitState("idle");
      mutationLock.current = false;
    }
  }

  async function handleRestoreOriginal() {
    const restoreResearcherId = researcherId || job?.researcher_id || "";
    const restoreExpectedRevisionId = jobActiveRevisionId(job, source);
    if (
      !source ||
      !restoreResearcherId ||
      !restoreExpectedRevisionId ||
      restoreState === "working" ||
      mutationLock.current
    ) return;
    mutationLock.current = true;
    setRestoreState("working");
    setError("");
    try {
      const nextSource = await restorePilotSourceOriginal(pilotSourceId(source), {
        researcher_id: restoreResearcherId,
        expected_active_revision_id: restoreExpectedRevisionId,
        confirmation: PILOT_RESTORE_CONFIRMATION
      });
      setSource(nextSource);
      if (job) {
        try {
          setJob(await getPilotJob(job.job_id));
        } catch {
          setPollError("The original was restored, but the audit timeline could not be refreshed.");
        }
      }
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setRestoreState("idle");
      mutationLock.current = false;
    }
  }

  function handlePrepareAnotherRun() {
    setJob(null);
    setJobAuthorized(false);
    setPollError("");
    setProposalErrors({});
    idempotencyKey.current = null;
    updatePilotQuery({ studyId, sourceId, jobId: "" });
  }

  return (
    <main className="professor-demo-shell transcript-pilot-shell">
      <header className="professor-demo-header">
        <div className="professor-demo-eyebrow">
          <span className="professor-demo-live-dot" aria-hidden="true" />
          Researcher-supervised pilot
        </div>
        <div className="professor-demo-heading-row">
          <div>
            <h1>Review every line before agents change a transcript.</h1>
            <p>
              Four bounded Luna specialists propose a revision. A named researcher accepts,
              keeps, or edits every line before one reversible commit.
            </p>
          </div>
          <div className="transcript-pilot-header-badges" aria-label="Pilot boundaries">
            <span className="professor-demo-route-badge">
              <Database size={16} /> Local study
            </span>
            <span className="professor-demo-route-badge transcript-pilot-remote-badge">
              <Cloud size={16} /> Remote Luna
            </span>
            <span className="professor-demo-route-badge">
              <ShieldCheck size={16} /> Human controlled
            </span>
          </div>
        </div>
      </header>

      <PilotSteps currentStep={currentStep} />

      {error ? (
        <div className="professor-demo-error" role="alert" tabIndex={-1} ref={errorRef}>
          <CircleAlert size={18} aria-hidden="true" />
          <div>
            <strong>Pilot stopped visibly.</strong>
            <span>{error}</span>
          </div>
          <button
            className="transcript-pilot-icon-button"
            type="button"
            aria-label="Dismiss error"
            onClick={() => setError("")}
          >
            <X size={16} />
          </button>
        </div>
      ) : null}

      <div className="transcript-pilot-stack">
        <section className="professor-demo-card transcript-pilot-card" aria-labelledby="pilot-context-title">
          <div className="professor-demo-section-heading">
            <div>
              <span className="professor-demo-kicker">Context</span>
              <h2 id="pilot-context-title">Named study and researcher</h2>
            </div>
            <UserRound size={20} aria-hidden="true" />
          </div>

          {contextState === "loading" ? (
            <LoadingLine label="Loading local studies" />
          ) : contextState === "error" ? (
            <InlineProblem message="Local study context could not be loaded.">
              <button
                className="professor-demo-button professor-demo-button-quiet"
                type="button"
                onClick={() => void loadStudies()}
              >
                <RefreshCcw size={15} /> Retry
              </button>
            </InlineProblem>
          ) : (
            <>
              <div className="transcript-pilot-context-grid">
                <label className="transcript-pilot-field">
                  <span>Study</span>
                  <select
                    value={studyId}
                    onChange={(event) => handleStudyChange(event.target.value)}
                    disabled={activeJob || createStudyState === "working"}
                  >
                    <option value="">Select a study</option>
                    {studies.map((study) => (
                      <option key={pilotStudyId(study)} value={pilotStudyId(study)}>
                        {study.name}
                      </option>
                    ))}
                  </select>
                </label>
                {sourceId ? (
                  <div className="transcript-pilot-researcher-fact">
                    <span>Source owner</span>
                    <strong>{researcherName || researcherId || "Loading owner"}</strong>
                    <small>{researcherId || "Verifying source ownership…"}</small>
                  </div>
                ) : selectedStudy?.researchers?.some((item) => item.active !== false) ? (
                  <label className="transcript-pilot-field">
                    <span>Researcher</span>
                    <select
                      value={researcherId}
                      onChange={(event) => {
                        const nextResearcher = selectedStudy.researchers?.find(
                          (item) => item.researcher_id === event.target.value
                        );
                        setResearcherId(event.target.value);
                        setResearcherName(nextResearcher?.display_name ?? event.target.value);
                        idempotencyKey.current = null;
                      }}
                      disabled={activeJob || Boolean(sourceId)}
                    >
                      {(selectedStudy.researchers ?? [])
                        .filter((item) => item.active !== false)
                        .map((researcher) => (
                          <option key={researcher.researcher_id} value={researcher.researcher_id}>
                            {researcher.display_name}
                          </option>
                        ))}
                    </select>
                  </label>
                ) : (
                  <div className="transcript-pilot-researcher-fact">
                    <span>Researcher</span>
                    <strong>{researcherName || "Not assigned"}</strong>
                    <small>{researcherId || "Create or select a named study."}</small>
                  </div>
                )}
                <button
                  className="professor-demo-button professor-demo-button-quiet transcript-pilot-new-study-button"
                  type="button"
                  aria-expanded={showStudyForm}
                  onClick={() => setShowStudyForm((current) => !current)}
                  disabled={activeJob}
                >
                  {showStudyForm ? "Close form" : "New study"}
                </button>
              </div>

              {showStudyForm ? (
                <form className="transcript-pilot-study-form" onSubmit={handleCreateStudy}>
                  <label className="transcript-pilot-field">
                    <span>Study name</span>
                    <input
                      value={studyName}
                      onChange={(event) => setStudyName(event.target.value)}
                      required
                    />
                  </label>
                  <label className="transcript-pilot-field transcript-pilot-wide-field">
                    <span>Description</span>
                    <input
                      value={studyDescription}
                      onChange={(event) => setStudyDescription(event.target.value)}
                      required
                    />
                  </label>
                  <label className="transcript-pilot-field">
                    <span>Researcher ID</span>
                    <input
                      value={newResearcherId}
                      onChange={(event) => setNewResearcherId(event.target.value)}
                      pattern="^[a-z][a-z0-9_]{2,95}$"
                      title="Start with a lowercase letter and use lowercase letters, numbers, or underscores."
                      aria-describedby="pilot-researcher-id-help"
                      required
                    />
                    <small id="pilot-researcher-id-help">
                      Lowercase letters, numbers, and underscores; start with a letter.
                    </small>
                  </label>
                  <label className="transcript-pilot-field">
                    <span>Researcher name</span>
                    <input
                      value={newResearcherName}
                      onChange={(event) => setNewResearcherName(event.target.value)}
                      placeholder="Dr. Researcher"
                      required
                    />
                  </label>
                  <button
                    className="professor-demo-button professor-demo-button-secondary"
                    type="submit"
                    disabled={createStudyState === "working"}
                  >
                    {createStudyState === "working" ? (
                      <><LoaderCircle className="professor-demo-spinner" size={16} /> Creating</>
                    ) : (
                      <><Check size={16} /> Create named study</>
                    )}
                  </button>
                </form>
              ) : null}

              {selectedStudy && !researcherId ? (
                <InlineProblem message="This study has no named researcher. Create a named pilot study before registering a source." />
              ) : null}
            </>
          )}
        </section>

        <div className="transcript-pilot-intake-grid">
          <section className="professor-demo-card transcript-pilot-card" aria-labelledby="pilot-source-title">
            <div className="professor-demo-section-heading">
              <div>
                <span className="professor-demo-kicker">01 · Source</span>
                <h2 id="pilot-source-title">One bounded transcript</h2>
              </div>
              <FileText size={20} aria-hidden="true" />
            </div>

            {sources.length > 0 ? (
              <label className="transcript-pilot-field transcript-pilot-existing-source">
                <span>Existing source</span>
                <select
                  value={sourceId}
                  onChange={(event) => void handleSourceChange(event.target.value)}
                  disabled={activeJob || sourceState === "loading"}
                >
                  <option value="">Upload a new source</option>
                  {sources.map((item) => (
                    <option key={pilotSourceId(item)} value={pilotSourceId(item)}>
                      {item.filename}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}

            {sourceState === "loading" && sourceId ? <LoadingLine label="Loading source lineage" /> : null}

            {sourceState === "error" ? (
              <InlineProblem message="The study source list or selected source could not be loaded.">
                <button
                  className="professor-demo-button professor-demo-button-quiet"
                  type="button"
                  onClick={() => {
                    setError("");
                    setSourceReload((current) => current + 1);
                  }}
                >
                  <RefreshCcw size={15} /> Retry sources
                </button>
              </InlineProblem>
            ) : null}

            {source ? (
              <SourceSummary source={source} />
            ) : (
              <form className="transcript-pilot-source-form" onSubmit={handleUpload}>
                <div className="transcript-pilot-warning" role="note">
                  <ShieldCheck size={17} aria-hidden="true" />
                  <p>
                    All four specialists receive the extracted text remotely. Redaction is an
                    output task; it does not protect identifiers before sending.
                  </p>
                </div>

                <div className="transcript-pilot-file-row">
                  <label className="transcript-pilot-file-input">
                    <span>TXT or DOCX file</span>
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept=".txt,.docx,text/plain,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                      onChange={(event) => handleFile(event.target.files?.[0] ?? null)}
                    />
                  </label>
                  <button
                    className="professor-demo-button professor-demo-button-quiet"
                    type="button"
                    onClick={handleLoadSample}
                  >
                    <FileCheck2 size={15} /> Load professor sample
                  </button>
                </div>

                {selectedFile ? (
                  <div className="transcript-pilot-selected-file" aria-live="polite">
                    <FileUp size={17} aria-hidden="true" />
                    <div>
                      <strong>{selectedFile.name}</strong>
                      <span>{formatBytes(selectedFile.size)} · held locally until upload</span>
                    </div>
                    <button
                      type="button"
                      aria-label="Remove selected file"
                      onClick={() => handleFile(null)}
                    >
                      <X size={15} />
                    </button>
                  </div>
                ) : null}

                <fieldset className="transcript-pilot-classification">
                  <legend>Data classification</legend>
                  <label>
                    <input
                      type="radio"
                      name="data-classification"
                      value="synthetic"
                      checked={classification === "synthetic"}
                      onChange={() => handleClassification("synthetic")}
                    />
                    <span><strong>Synthetic</strong><small>Fully invented classroom material</small></span>
                  </label>
                  <label>
                    <input
                      type="radio"
                      name="data-classification"
                      value="authorized-deidentified"
                      checked={classification === "authorized-deidentified"}
                      onChange={() => handleClassification("authorized-deidentified")}
                    />
                    <span><strong>Authorized + deidentified</strong><small>Approved excerpt with no direct identifiers</small></span>
                  </label>
                </fieldset>

                <label className="transcript-pilot-field">
                  <span>Authorization basis</span>
                  <input
                    value={authorizationBasis}
                    onChange={(event) => setAuthorizationBasis(event.target.value)}
                    placeholder={DEIDENTIFIED_AUTHORIZATION_PLACEHOLDER}
                    required
                  />
                </label>

                {classification === "authorized-deidentified" ? (
                  <label className="transcript-pilot-check">
                    <input
                      type="checkbox"
                      checked={noDirectIdentifiers}
                      onChange={(event) => setNoDirectIdentifiers(event.target.checked)}
                    />
                    <span>I confirm this excerpt contains no direct identifiers.</span>
                  </label>
                ) : null}

                <label className="transcript-pilot-check transcript-pilot-egress-check">
                  <input
                    type="checkbox"
                    checked={egressAuthorized}
                    onChange={(event) => setEgressAuthorized(event.target.checked)}
                  />
                  <span>
                    I authorize this source for remote Luna processing under the stated basis.
                  </span>
                </label>

                <button
                  className="professor-demo-button professor-demo-button-primary"
                  type="submit"
                  disabled={
                    !studyId ||
                    !researcherId ||
                    !selectedFile ||
                    !authorizationBasis.trim() ||
                    !egressAuthorized ||
                    (classification === "authorized-deidentified" && !noDirectIdentifiers) ||
                    uploadState === "working"
                  }
                >
                  {uploadState === "working" ? (
                    <><LoaderCircle className="professor-demo-spinner" size={16} /> Registering source</>
                  ) : (
                    <><Upload size={16} /> Register source locally</>
                  )}
                </button>
              </form>
            )}
          </section>

          <section className="professor-demo-card transcript-pilot-card" aria-labelledby="pilot-job-title">
            <div className="professor-demo-section-heading">
              <div>
                <span className="professor-demo-kicker">02 · Agents</span>
                <h2 id="pilot-job-title">Four specialists per chunk</h2>
              </div>
              <span className={`status-pill transcript-pilot-status-${job?.status ?? "idle"}`}>
                {job ? jobStatusLabel(job.status) : "Not started"}
              </span>
            </div>

            {!source ? (
              <EmptyState icon={<LockKeyhole size={18} />} text="Register or select one source first." />
            ) : job ? (
              <JobProgress
                job={job}
                source={source}
                pollError={pollError}
                onRetryPoll={() => {
                  setPollError("");
                  setPollRetry((current) => current + 1);
                }}
              />
            ) : (
              <div className="transcript-pilot-job-authorization">
                <div className="transcript-pilot-run-facts">
                  <span><strong>{source.chunk_count ?? 1}</strong> source chunk{(source.chunk_count ?? 1) === 1 ? "" : "s"}</span>
                  <span><strong>{(source.chunk_count ?? 1) * 4}</strong> maximum model calls</span>
                  <span><strong>0</strong> accepted transcript changes</span>
                </div>
                <label className="transcript-pilot-field transcript-pilot-cost-field">
                  <span>Maximum authorized cost (USD)</span>
                  <div className="transcript-pilot-cost-input">
                    <CircleDollarSign size={17} aria-hidden="true" />
                    <input
                      inputMode="decimal"
                      value={authorizedCost}
                      onChange={(event) => {
                        setAuthorizedCost(event.target.value);
                        idempotencyKey.current = null;
                      }}
                      aria-invalid={!validCost(authorizedCost)}
                    />
                  </div>
                </label>
                <label className="transcript-pilot-check transcript-pilot-job-check">
                  <input
                    type="checkbox"
                    checked={jobAuthorized}
                    onChange={(event) => setJobAuthorized(event.target.checked)}
                  />
                  <span>
                    I authorize exactly four specialist calls per chunk, up to {formatAuthorizedCost(authorizedCost)}.
                  </span>
                </label>
                <button
                  className="professor-demo-button professor-demo-button-primary"
                  type="button"
                  onClick={() => void handleStartJob()}
                  disabled={
                    !researcherId ||
                    !selectedSourceRevision ||
                    !jobAuthorized ||
                    !validCost(authorizedCost) ||
                    jobRequestState === "working"
                  }
                >
                  {jobRequestState === "working" ? (
                    <><LoaderCircle className="professor-demo-spinner" size={16} /> Starting bounded job</>
                  ) : (
                    <>Run four specialists per chunk <ArrowRight size={16} /></>
                  )}
                </button>
              </div>
            )}

            <div className="transcript-pilot-specialist-grid" aria-label="Specialist status">
              {PILOT_SPECIALISTS.map((definition) => (
                <SpecialistCard
                  key={definition.id}
                  definition={definition}
                  progress={job?.specialists?.find((item) => item.specialist_id === definition.id)}
                  active={activeJob}
                  terminal={Boolean(job && !activeJob)}
                  strictReceipt={jobHasStrictReceipt(job)}
                />
              ))}
            </div>

            {activeJob ? (
              <button
                className="professor-demo-button professor-demo-button-quiet transcript-pilot-cancel-button"
                type="button"
                onClick={() => void handleCancelJob()}
                disabled={cancelState === "working" || job?.status === "cancelling"}
              >
                {cancelState === "working" || job?.status === "cancelling" ? (
                  <><LoaderCircle className="professor-demo-spinner" size={15} /> Cancelling safely</>
                ) : (
                  <><Square size={14} /> Cancel job</>
                )}
              </button>
            ) : null}

            {job && ["failed", "cancelled", "interrupted"].includes(job.status) ? (
              <button
                className="professor-demo-button professor-demo-button-quiet transcript-pilot-cancel-button"
                type="button"
                onClick={handlePrepareAnotherRun}
              >
                <RefreshCcw size={15} /> Prepare a new bounded run
              </button>
            ) : null}
          </section>
        </div>

        {reviewableJob && job ? (
          <section className="transcript-pilot-review-section" aria-labelledby="pilot-review-title">
            <div className="transcript-pilot-review-heading">
              <div>
                <span className="professor-demo-kicker">03 · Review</span>
                <h2 id="pilot-review-title" ref={reviewHeadingRef} tabIndex={-1}>
                  Decide every proposed line
                </h2>
                <p>
                  Agent output remains a candidate. Each decision is saved with your researcher ID.
                </p>
              </div>
              <ReviewCountSummary counts={reviewCounts} pending={savingProposals.size} />
            </div>

            <ol className="transcript-pilot-review-list">
              {[...job.proposals]
                .sort((left, right) => left.line_index - right.line_index)
                .map((proposal) => (
                  <li key={proposal.proposal_id}>
                    <ReviewLineCard
                      proposal={proposal}
                      saving={savingProposals.has(proposal.proposal_id)}
                      error={proposalErrors[proposal.proposal_id] ?? ""}
                      onDecide={handleDecision}
                    />
                  </li>
                ))}
            </ol>

            <CommitBar
              counts={reviewCounts}
              researcherName={researcherName || researcherId}
              pending={savingProposals.size}
              createsRevision={createsRevision}
              canCommit={canCommit}
              commitState={commitState}
              onCommit={() => void handleCommit()}
            />
          </section>
        ) : null}

        {job && committedJob ? (
          <RevisionSummary
            job={job}
            source={source}
            restored={originalIsActive}
            restoreState={restoreState}
            headingRef={revisionHeadingRef}
            onRestore={() => void handleRestoreOriginal()}
            onPrepareAnother={handlePrepareAnotherRun}
          />
        ) : null}

        {job && !activeJob ? <AuditExportPanel job={job} committed={committedJob} /> : null}
      </div>

      <footer className="professor-demo-footer transcript-pilot-footer">
        <span>Local source + audit · remote Luna proposals · researcher-controlled revision</span>
        <a href="/professor-demo">Open the four-call proof</a>
      </footer>
    </main>
  );
}

function PilotSteps({ currentStep }: { currentStep: number }) {
  const steps = ["Source", "Agents", "Review", "Revision"];
  return (
    <nav className="transcript-pilot-steps" aria-label="Pilot steps">
      <ol>
        {steps.map((label, index) => {
          const number = index + 1;
          const state = number < currentStep ? "complete" : number === currentStep ? "current" : "upcoming";
          return (
            <li key={label} className={`transcript-pilot-step ${state}`} aria-current={state === "current" ? "step" : undefined}>
              <span>{state === "complete" ? <Check size={13} /> : number.toString().padStart(2, "0")}</span>
              <strong>{label}</strong>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

function SourceSummary({ source }: { source: PilotSource }) {
  return (
    <div className="transcript-pilot-source-summary">
      <div className="transcript-pilot-source-name">
        <FileCheck2 size={19} aria-hidden="true" />
        <div>
          <strong>{source.filename}</strong>
          <span>{classificationLabel(pilotSourceClassification(source))}</span>
        </div>
      </div>
      <dl>
        <div><dt>Chunks</dt><dd>{source.chunk_count ?? "Pending"}</dd></div>
        <div><dt>Lines</dt><dd>{source.line_count ?? "Pending"}</dd></div>
        <div><dt>Active revision</dt><dd>{shortId(pilotSourceInputRevisionId(source))}</dd></div>
        <div><dt>Source hash</dt><dd>{shortId(source.source_sha256 ?? "Unavailable")}</dd></div>
      </dl>
      {source.preview_text ? <pre>{source.preview_text}</pre> : null}
      <p>
        This source is registered locally. No accepted transcript changes occur until the supervised commit.
      </p>
    </div>
  );
}

function JobProgress({
  job,
  source,
  pollError,
  onRetryPoll
}: {
  job: PilotJob;
  source: PilotSource;
  pollError: string;
  onRetryPoll: () => void;
}) {
  const expectedCalls =
    job.receipt?.expected_call_count ?? Math.max(1, job.total_chunks ?? source.chunk_count ?? 1) * 4;
  const completedCalls = job.receipt?.completed_call_count ?? 0;
  const active = isPilotJobActive(job);
  const stopped = ["failed", "cancelled", "interrupted"].includes(job.status);
  return (
    <div className="transcript-pilot-progress" aria-live="polite" aria-busy={active}>
      <div className="transcript-pilot-progress-copy">
        <div>
          {active ? (
            <LoaderCircle className="professor-demo-spinner" size={18} />
          ) : stopped ? (
            <Ban size={18} />
          ) : (
            <Check size={18} />
          )}
          <strong>{job.stage || jobStatusLabel(job.status)}</strong>
        </div>
        <span>{completedCalls} / {expectedCalls} calls completed</span>
      </div>
      <progress
        aria-label="Specialist calls completed"
        max={Math.max(1, expectedCalls)}
        value={Math.min(completedCalls, expectedCalls)}
      >
        {completedCalls} of {expectedCalls}
      </progress>
      <div className="transcript-pilot-run-meta">
        <span>Job <code>{shortId(job.job_id)}</code></span>
        <span>{job.completed_chunks ?? 0} / {job.total_chunks ?? source.chunk_count ?? 1} chunks</span>
      </div>
      {job.failure_message ? (
        <InlineProblem message={job.failure_message} />
      ) : null}
      {pollError ? (
        <InlineProblem message={`Status connection paused: ${pollError}`}>
          <button className="professor-demo-button professor-demo-button-quiet" type="button" onClick={onRetryPoll}>
            <RefreshCcw size={15} /> Retry status only
          </button>
        </InlineProblem>
      ) : null}
    </div>
  );
}

function SpecialistCard({
  definition,
  progress,
  active,
  terminal,
  strictReceipt
}: {
  definition: (typeof PILOT_SPECIALISTS)[number];
  progress?: PilotSpecialistProgress;
  active: boolean;
  terminal: boolean;
  strictReceipt: boolean;
}) {
  const status = progress?.status ?? (active ? "running" : strictReceipt ? "valid" : terminal ? "pending" : "pending");
  const statusText = specialistStatusLabel(status, progress?.schema_valid);
  return (
    <article className={`transcript-pilot-specialist transcript-pilot-specialist-${status}`}>
      <div>
        <span aria-hidden="true">{status === "valid" ? <Check size={14} /> : status === "error" ? <Ban size={14} /> : <span className="transcript-pilot-agent-dot" />}</span>
        <strong>{definition.label}</strong>
      </div>
      <p>{definition.description}</p>
      <small>{statusText}</small>
      {progress?.error_message ? <em>{progress.error_message}</em> : null}
    </article>
  );
}

function ReviewLineCard({
  proposal,
  saving,
  error,
  onDecide
}: {
  proposal: PilotProposal;
  saving: boolean;
  error: string;
  onDecide: (
    proposal: PilotProposal,
    action: PilotProposalAction,
    editedText: string | null
  ) => Promise<boolean>;
}) {
  const savedAction = proposalAction(proposal);
  const [editing, setEditing] = useState(savedAction === "edit");
  const [localAction, setLocalAction] = useState<PilotProposalAction | null>(savedAction);
  const [draft, setDraft] = useState(proposal.edited_text ?? proposal.proposed_text);

  useEffect(() => {
    if (!editing) setDraft(proposal.edited_text ?? proposal.proposed_text);
  }, [proposal.edited_text, proposal.proposed_text, editing]);

  useEffect(() => {
    if (!saving) setLocalAction(savedAction);
  }, [savedAction, saving]);

  const evidence = proposal.specialist_contributions ?? proposal.evidence ?? {};
  const decisionName = `proposal-${proposal.proposal_id}`;
  return (
    <fieldset className="transcript-pilot-review-line" disabled={saving}>
      <legend>
        <span>Line {(proposal.line_index + 1).toString().padStart(2, "0")}</span>
        <small>{!proposal.changed ? "Unchanged · no decision required" : saving ? "Saving decision" : editing && savedAction !== "edit" ? "Unsaved researcher edit" : savedAction ? `${decisionLabel(savedAction)} · v${proposal.decision_version}` : "Decision required"}</small>
      </legend>

      <div className="transcript-pilot-line-comparison">
        <div>
          <span>Original</span>
          <p>{proposal.original_text}</p>
        </div>
        <div className="candidate">
          <span>Agent proposal</span>
          <p>{proposal.proposed_text}</p>
        </div>
      </div>

      {proposal.changed ? (
        <div className="transcript-pilot-decision-group" role="group" aria-label={`Decision for line ${proposal.line_index + 1}`}>
          <label>
            <input
              type="radio"
              name={decisionName}
              checked={!editing && localAction === "accept"}
              onChange={() => {
                setLocalAction("accept");
                setEditing(false);
                void onDecide(proposal, "accept", null).then((saved) => {
                  if (!saved) setLocalAction(savedAction);
                });
              }}
            />
            <span><Check size={14} /> Accept proposal</span>
          </label>
          <label>
            <input
              type="radio"
              name={decisionName}
              checked={!editing && localAction === "keep_original"}
              onChange={() => {
                setLocalAction("keep_original");
                setEditing(false);
                void onDecide(proposal, "keep_original", null).then((saved) => {
                  if (!saved) setLocalAction(savedAction);
                });
              }}
            />
            <span><RotateCcw size={14} /> Keep original</span>
          </label>
          <label>
            <input
              type="radio"
              name={decisionName}
              checked={editing}
              onChange={() => {
                setLocalAction("edit");
                setEditing(true);
              }}
            />
            <span><FileText size={14} /> Edit</span>
          </label>
        </div>
      ) : (
        <div className="transcript-pilot-unchanged-line">
          <Check size={14} aria-hidden="true" /> Kept unchanged automatically; no researcher decision is required.
        </div>
      )}

      {proposal.changed && editing ? (
        <div className="transcript-pilot-edit-panel">
          <label htmlFor={`edit-${proposal.proposal_id}`}>Researcher edit for line {proposal.line_index + 1}</label>
          <textarea
            id={`edit-${proposal.proposal_id}`}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            aria-invalid={draft.trim().length === 0}
          />
          <div>
            <button
              className="professor-demo-button professor-demo-button-secondary"
              type="button"
              disabled={!draft.trim() || saving}
              onClick={() => {
                void onDecide(proposal, "edit", draft.trim()).then((saved) => {
                  if (saved) {
                    setLocalAction("edit");
                    setEditing(false);
                  }
                });
              }}
            >
              {saving ? <LoaderCircle className="professor-demo-spinner" size={15} /> : <Check size={15} />}
              Save edit
            </button>
            <button
              className="professor-demo-button professor-demo-button-quiet"
              type="button"
              onClick={() => {
                setDraft(proposal.edited_text ?? proposal.proposed_text);
                setLocalAction(savedAction);
                setEditing(false);
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {proposal.changed && error ? <div className="transcript-pilot-line-error" role="alert">{error} Your local edit is still here; reload this line before retrying if another reviewer changed it.</div> : null}

      <details className="transcript-pilot-evidence">
        <summary>Agent evidence</summary>
        {Object.keys(evidence).length > 0 ? (
          <dl>
            {Object.entries(evidence).map(([key, value]) => (
              <div key={key}>
                <dt>{readableKey(key)}</dt>
                <dd>{readableEvidence(value)}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p>No line-level evidence was returned.</p>
        )}
      </details>
    </fieldset>
  );
}

function ReviewCountSummary({
  counts,
  pending
}: {
  counts: ReturnType<typeof pilotReviewCounts>;
  pending: number;
}) {
  return (
    <div className="transcript-pilot-review-counts" aria-live="polite">
      <span><strong>{counts.accepted}</strong> accepted</span>
      <span><strong>{counts.kept}</strong> kept</span>
      <span><strong>{counts.edited}</strong> edited</span>
      <span><strong>{counts.unchanged}</strong> unchanged</span>
      <span className={counts.unresolved > 0 ? "unresolved" : "resolved"}><strong>{counts.unresolved}</strong> unresolved</span>
      {pending > 0 ? <span><LoaderCircle className="professor-demo-spinner" size={13} /> {pending} saving</span> : null}
    </div>
  );
}

function CommitBar({
  counts,
  researcherName,
  pending,
  createsRevision,
  canCommit,
  commitState,
  onCommit
}: {
  counts: ReturnType<typeof pilotReviewCounts>;
  researcherName: string;
  pending: number;
  createsRevision: boolean;
  canCommit: boolean;
  commitState: RequestState;
  onCommit: () => void;
}) {
  const reason = pending > 0
    ? "Wait for every decision to finish saving."
    : counts.unresolved > 0
      ? `Resolve ${counts.unresolved} remaining line${counts.unresolved === 1 ? "" : "s"}.`
      : !createsRevision
        ? "Accept a proposed change or save an edit that differs from the original."
      : !canCommit
        ? "The strict call and usage receipt must validate before commit."
        : "Creates one child revision; the original remains preserved.";
  return (
    <div className="transcript-pilot-commit-bar">
      <div>
        <span className="professor-demo-kicker">Accepted-data gate</span>
        <strong>{canCommit ? `Ready to commit as ${researcherName}` : "Review is not complete"}</strong>
        <small>{reason}</small>
      </div>
      <button
        className="professor-demo-button professor-demo-button-primary"
        type="button"
        disabled={!canCommit || commitState === "working"}
        onClick={onCommit}
      >
        {commitState === "working" ? (
          <><LoaderCircle className="professor-demo-spinner" size={16} /> Creating revision</>
        ) : (
          <><LockKeyhole size={16} /> Create supervised revision</>
        )}
      </button>
    </div>
  );
}

function RevisionSummary({
  job,
  source,
  restored,
  restoreState,
  headingRef,
  onRestore,
  onPrepareAnother
}: {
  job: PilotJob;
  source: PilotSource | null;
  restored: boolean;
  restoreState: RequestState;
  headingRef: RefObject<HTMLHeadingElement | null>;
  onRestore: () => void;
  onPrepareAnother: () => void;
}) {
  const revision = job.committed_revision;
  return (
    <section className="professor-demo-card transcript-pilot-card transcript-pilot-revision" aria-labelledby="pilot-revision-title">
      <div className="transcript-pilot-revision-copy">
        <div className={`professor-demo-revision-icon ${restored ? "" : "accepted"}`}>
          {restored ? <RotateCcw size={19} /> : <Check size={20} />}
        </div>
        <div>
          <span className="professor-demo-kicker">04 · Revision</span>
          <h2 id="pilot-revision-title" ref={headingRef} tabIndex={-1}>
            {restored ? "Original transcript restored" : "Supervised revision is active"}
          </h2>
          <p>
            {restored
              ? "The original revision is active again. The candidate, decisions, and audit receipt remain preserved."
              : "A child transcript revision was created from the researcher’s saved line decisions. The original is unchanged."}
          </p>
        </div>
      </div>
      <dl className="transcript-pilot-revision-facts">
        <div><dt>Active revision</dt><dd>{shortId(jobActiveRevisionId(job, source) || "Unavailable")}</dd></div>
        <div><dt>Child revision</dt><dd>{shortId(revision?.revision_id ?? "Unavailable")}</dd></div>
        <div><dt>Parent revision</dt><dd>{shortId(revision?.parent_revision_id ?? job.input_revision_id)}</dd></div>
        <div><dt>Researcher</dt><dd>{job.researcher_id}</dd></div>
      </dl>
      <div className="transcript-pilot-revision-actions">
        {!restored ? (
          <button
            className="professor-demo-button professor-demo-button-secondary"
            type="button"
            onClick={onRestore}
            disabled={restoreState === "working"}
          >
            {restoreState === "working" ? (
              <><LoaderCircle className="professor-demo-spinner" size={15} /> Restoring original</>
            ) : (
              <><RotateCcw size={15} /> Restore original</>
            )}
          </button>
        ) : null}
        <button className="professor-demo-button professor-demo-button-quiet" type="button" onClick={onPrepareAnother}>
          <RefreshCcw size={15} /> Prepare another run
        </button>
      </div>
    </section>
  );
}

function AuditExportPanel({ job, committed }: { job: PilotJob; committed: boolean }) {
  const events = job.audit_events ?? [];
  const receipt = job.receipt;
  return (
    <section className="professor-demo-card transcript-pilot-card transcript-pilot-audit" aria-labelledby="pilot-audit-title">
      <div className="professor-demo-section-heading">
        <div>
          <span className="professor-demo-kicker">Export + audit</span>
          <h2 id="pilot-audit-title">One accountable record</h2>
        </div>
        <Database size={20} aria-hidden="true" />
      </div>
      <div className="transcript-pilot-audit-grid">
        <div className="transcript-pilot-receipt">
          <dl>
            <div><dt>Attempted calls</dt><dd>{receipt?.attempted_call_count ?? "Unknown"}</dd></div>
            <div><dt>Strict results</dt><dd>{receipt?.valid_result_count ?? "Unknown"}</dd></div>
            <div><dt>Total tokens</dt><dd>{formatPilotNumber(receipt?.total_tokens)}</dd></div>
            <div><dt>Actual cost</dt><dd>{formatPilotCost(receipt?.total_cost_usd)}</dd></div>
            <div className="wide"><dt>Model</dt><dd>{receipt?.model ?? "Unavailable"}</dd></div>
            <div className="wide"><dt>Job ID</dt><dd><code>{job.job_id}</code></dd></div>
          </dl>
          <div className="transcript-pilot-export-actions">
            <a
              className="professor-demo-button professor-demo-button-secondary"
              href={pilotExportUrl(job.job_id, "receipt.json")}
              download
            >
              <Download size={15} /> Receipt JSON
            </a>
            {committed ? (
              <a
                className="professor-demo-button professor-demo-button-primary"
                href={pilotExportUrl(job.job_id, "transcript.txt")}
                download
              >
                <Download size={15} /> Transcript TXT
              </a>
            ) : null}
          </div>
        </div>
        <div className="transcript-pilot-timeline">
          <h3>Server audit timeline</h3>
          {events.length > 0 ? (
            <ol>
              {events.map((event, index) => (
                <li key={event.event_id ?? `${event.event_type}-${index}`}>
                  <span aria-hidden="true" />
                  <div>
                    <strong>{event.summary || readableKey(event.event_type)}</strong>
                    <small>{formatAuditEvent(event)}</small>
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <p>No server audit events were returned yet.</p>
          )}
        </div>
      </div>
    </section>
  );
}

function LoadingLine({ label }: { label: string }) {
  return (
    <div className="transcript-pilot-loading" aria-live="polite">
      <LoaderCircle className="professor-demo-spinner" size={16} /> {label}
    </div>
  );
}

function InlineProblem({
  message,
  children
}: {
  message: string;
  children?: ReactNode;
}) {
  return (
    <div className="transcript-pilot-inline-problem" role="alert">
      <CircleAlert size={17} aria-hidden="true" />
      <span>{message}</span>
      {children}
    </div>
  );
}

function EmptyState({ icon, text }: { icon: ReactNode; text: string }) {
  return (
    <div className="transcript-pilot-empty-state">
      {icon}
      <span>{text}</span>
    </div>
  );
}

function studyResearcher(study: PilotStudy): { id: string; name: string } {
  const flexibleStudy = study as PilotStudy & {
    researcher?: { researcher_id?: string; display_name?: string; researcher_name?: string };
  };
  return {
    id: study.researcher_id || flexibleStudy.researcher?.researcher_id || "",
    name:
      study.researcher_name ||
      flexibleStudy.researcher?.display_name ||
      flexibleStudy.researcher?.researcher_name ||
      ""
  };
}

function readPilotQuery(): { studyId: string; sourceId: string; jobId: string } {
  const query = new URLSearchParams(window.location.search);
  return {
    studyId: query.get("study") ?? "",
    sourceId: query.get("source") ?? "",
    jobId: query.get("job") ?? ""
  };
}

function updatePilotQuery(params: { studyId: string; sourceId: string; jobId: string }) {
  const url = new URL(window.location.href);
  setQueryValue(url, "study", params.studyId);
  setQueryValue(url, "source", params.sourceId);
  setQueryValue(url, "job", params.jobId);
  window.history.replaceState({}, "", url.toString());
}

function setQueryValue(url: URL, key: string, value: string) {
  if (value) url.searchParams.set(key, value);
  else url.searchParams.delete(key);
}

function createIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `pilot-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function validCost(value: string): boolean {
  const amount = Number(value);
  return Number.isFinite(amount) && amount > 0;
}

function jobActiveRevisionId(job: PilotJob | null, source: PilotSource | null): string {
  return (
    (source ? pilotSourceInputRevisionId(source) : "") ||
    job?.source?.active_revision_id ||
    job?.active_revision_id ||
    ""
  );
}

function sourceOriginalIsActive(job: PilotJob | null, source: PilotSource | null): boolean {
  const originalRevisionId =
    source?.original_revision_id || job?.source?.original_revision_id || "";
  const activeRevisionId = jobActiveRevisionId(job, source);
  return Boolean(originalRevisionId && activeRevisionId === originalRevisionId);
}

function jobHasStrictReceipt(job: PilotJob | null): boolean {
  if (!job?.receipt?.accounting_complete) return false;
  const expected =
    job.receipt.expected_call_count ?? Math.max(1, job.total_chunks ?? 1) * PILOT_SPECIALISTS.length;
  return (
    expected > 0 &&
    job.receipt.attempted_call_count === expected &&
    job.receipt.completed_call_count === expected &&
    job.receipt.valid_result_count === expected
  );
}

function formatAuthorizedCost(value: string): string {
  return validCost(value) ? `$${Number(value).toFixed(2)}` : "a valid cost ceiling";
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} bytes`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function shortId(value: string): string {
  if (!value || value === "Unavailable") return value || "Unavailable";
  return value.length > 18 ? `${value.slice(0, 9)}…${value.slice(-6)}` : value;
}

function classificationLabel(value: PilotClassification): string {
  return value === "synthetic" ? "Synthetic" : "Authorized + deidentified";
}

function jobStatusLabel(status: PilotJob["status"]): string {
  const labels: Record<PilotJob["status"], string> = {
    queued: "Queued locally",
    preflighting: "Checking route + cost",
    running: "Specialists running",
    cancelling: "Cancelling",
    cancelled: "Cancelled",
    review_ready: "Ready for review",
    failed: "Stopped",
    committing: "Creating revision",
    committed: "Revision active",
    accepted: "Revision active",
    restored: "Original restored",
    interrupted: "Interrupted"
  };
  return labels[status] ?? readableKey(status);
}

function specialistStatusLabel(
  status: PilotSpecialistProgress["status"],
  schemaValid?: boolean
): string {
  if (status === "valid") return schemaValid === false ? "Validation incomplete" : "Strict JSON valid";
  if (status === "running") return "Calls in progress";
  if (status === "error") return "Stopped visibly";
  if (status === "cancelled") return "Cancelled";
  return "Awaiting job";
}

function decisionLabel(action: PilotProposalAction): string {
  if (action === "accept") return "Proposal accepted";
  if (action === "keep_original") return "Original kept";
  return "Researcher edit saved";
}

function readableKey(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function readableEvidence(value: unknown): string {
  if (value === null || value === undefined) return "None";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return "Structured evidence unavailable";
  }
}

function formatAuditEvent(event: PilotAuditEvent): string {
  const timestamp = event.occurred_at ?? event.created_at;
  const time = timestamp ? new Date(timestamp).toLocaleString() : "Time unavailable";
  return event.actor_id ? `${time} · ${event.actor_id}` : time;
}

function errorMessage(caught: unknown): string {
  return caught instanceof Error && caught.message.trim()
    ? caught.message
    : "The local transcript pilot request failed.";
}
