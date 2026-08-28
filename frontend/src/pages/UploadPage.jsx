import { useEffect, useState } from "react";

import ResumeScoreCard from "../components/ResumeScoreCard";
import { buildApiHeaders } from "../lib/api";
import { roleRequiresDsa } from "../lib/roleFlow";

const API_DEFAULT = "http://127.0.0.1:8000";

const ROUND_LABELS = {
  hr: "HR Interview",
  technical: "Technical Round",
  project_discussion: "Project Discussion",
};

const DOWNSTREAM_WORKFLOW_RESET = {
  activeInterviewRound: "technical",
  interviewRoundsDone: {},
  dsaPreviewViewed: false,
  assessmentStatus: "idle",
  assessmentAnsweredCount: 0,
  assessmentTotalQuestions: 0,
};

// All fresher roles that are always available regardless of resume matching.
const COMMON_FRESHER_ROLES = [
  { role_key: "software_engineer", title: "Software Engineer", description: "Designs and builds software systems across the full stack.", skill_gaps: [] },
  { role_key: "backend_python_developer", title: "Backend Python Developer", description: "Builds APIs and backend services with Python and FastAPI.", skill_gaps: [] },
  { role_key: "backend_java_developer", title: "Backend Java Developer", description: "Builds server-side applications and REST APIs with Java and Spring Boot.", skill_gaps: [] },
  { role_key: "backend_node_developer", title: "Backend Node.js Developer", description: "Builds scalable server-side services and APIs with Node.js.", skill_gaps: [] },
  { role_key: "frontend_react_developer", title: "Frontend React Developer", description: "Builds interactive web UIs with React and JavaScript.", skill_gaps: [] },
  { role_key: "full_stack_developer", title: "Full Stack Developer", description: "Works across both frontend and backend layers to deliver complete features.", skill_gaps: [] },
  { role_key: "mobile_app_developer", title: "Mobile App Developer", description: "Builds Android or iOS applications using Flutter or React Native.", skill_gaps: [] },
  { role_key: "data_analyst", title: "Data Analyst", description: "Analyzes datasets and communicates quantitative insights.", skill_gaps: [] },
  { role_key: "data_engineer", title: "Data Engineer", description: "Builds data pipelines, ETL workflows, and warehouse solutions.", skill_gaps: [] },
  { role_key: "machine_learning_engineer", title: "Machine Learning Engineer", description: "Builds and evaluates ML models and applied AI systems.", skill_gaps: [] },
  { role_key: "ai_engineer", title: "AI Engineer", description: "Builds AI-powered applications and LLM-based intelligent systems.", skill_gaps: [] },
  { role_key: "data_scientist", title: "Data Scientist", description: "Applies statistical modeling and ML to solve business problems.", skill_gaps: [] },
  { role_key: "devops_engineer", title: "DevOps Engineer", description: "Builds CI/CD pipelines and manages cloud infrastructure.", skill_gaps: [] },
  { role_key: "cloud_engineer", title: "Cloud Engineer", description: "Deploys and manages cloud infrastructure across AWS, GCP, or Azure.", skill_gaps: [] },
  { role_key: "qa_automation_engineer", title: "QA Automation Engineer", description: "Designs tests and automates verification flows.", skill_gaps: [] },
  { role_key: "cybersecurity_analyst", title: "Cybersecurity Analyst", description: "Identifies and mitigates security vulnerabilities in systems.", skill_gaps: [] },
  { role_key: "embedded_systems_engineer", title: "Embedded Systems Engineer", description: "Programs microcontrollers and builds firmware for real-time hardware.", skill_gaps: [] },
  { role_key: "iot_engineer", title: "IoT Engineer", description: "Connects physical devices to cloud platforms through sensors and protocols.", skill_gaps: [] },
  { role_key: "robotics_software_engineer", title: "Robotics Software Engineer", description: "Develops software for robotic systems including motion planning and control.", skill_gaps: [] },
  { role_key: "site_reliability_engineer", title: "Site Reliability Engineer", description: "Ensures system reliability through monitoring, alerting, and automation.", skill_gaps: [] },
  { role_key: "vlsi_design_engineer", title: "VLSI Design Engineer", description: "Designs digital circuits and ASIC/FPGA systems for hardware products.", skill_gaps: [] },
  { role_key: "firmware_engineer", title: "Firmware Engineer", description: "Writes low-level firmware for embedded hardware products.", skill_gaps: [] },
];

function trimTrailingSlash(value) {
  return value.replace(/\/+$/, "");
}

function resolveErrorMessage(payload, fallback) {
  if (!payload) {
    return fallback;
  }

  if (typeof payload.detail === "string") {
    return payload.detail;
  }

  if (Array.isArray(payload.detail)) {
    return payload.detail
      .map((item) => item?.msg || item?.type || "Request validation failed.")
      .join(" ");
  }

  return fallback;
}

function formatTitleFromKey(value) {
  return String(value || "")
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function metricValue(value) {
  if (!value) {
    return "0";
  }
  if (Array.isArray(value)) {
    return String(value.length);
  }
  return String(value);
}

const UPLOAD_PHASE_LABELS = {
  upload: "Upload resume",
  parse: "Parser active",
  roles: "Choose role",
  contexts: "Ready for assessment",
};

export default function UploadPage({ authState, workflowState, onWorkflowStateChange, onNavigate }) {
  const [apiBaseUrl, setApiBaseUrl] = useState(workflowState?.apiBaseUrl || API_DEFAULT);
  const [backendStatus, setBackendStatus] = useState("checking");
  const [resumeFile, setResumeFile] = useState(null);
  const [dragActive, setDragActive] = useState(false);
  const [useGroqProfiles, setUseGroqProfiles] = useState(true);
  const [maxRoles, setMaxRoles] = useState(5);
  const [busyState, setBusyState] = useState("idle");
  const [customRoleTitle, setCustomRoleTitle] = useState("");
  const [showAllFresherRoles, setShowAllFresherRoles] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [infoMessage, setInfoMessage] = useState(
    workflowState?.resumeParseResult
      ? workflowState?.resumeSelectionResult
        ? "Resume restored. Role locked."
        : "Resume restored. Choose a role."
      : "Upload a PDF resume.",
  );
  const [parseResult, setParseResult] = useState(workflowState?.resumeParseResult || null);
  const [selectionResult, setSelectionResult] = useState(workflowState?.resumeSelectionResult || null);

  function syncWorkflowState(patch) {
    onWorkflowStateChange?.((current) => ({
      ...current,
      ...patch,
    }));
  }

  useEffect(() => {
    const syncedApiBaseUrl = workflowState?.apiBaseUrl || API_DEFAULT;
    setApiBaseUrl(syncedApiBaseUrl);
  }, [workflowState?.apiBaseUrl]);

  useEffect(() => {
    setParseResult(workflowState?.resumeParseResult || null);
  }, [workflowState?.resumeParseResult]);

  useEffect(() => {
    setSelectionResult(workflowState?.resumeSelectionResult || null);
  }, [workflowState?.resumeSelectionResult]);

  useEffect(() => {
    let cancelled = false;

    async function checkBackend() {
      setBackendStatus("checking");
      try {
        const response = await fetch(`${trimTrailingSlash(apiBaseUrl)}/health`);
        if (!response.ok) {
          throw new Error("Backend health check failed.");
        }
        if (!cancelled) {
          setBackendStatus("online");
        }
      } catch {
        if (!cancelled) {
          setBackendStatus("offline");
        }
      }
    }

    checkBackend();
    return () => {
      cancelled = true;
    };
  }, [apiBaseUrl]);

  const baseUrl = trimTrailingSlash(apiBaseUrl);
  const accessToken = authState?.accessToken || "";
  const parsedResume = parseResult?.resume?.parsed_resume ?? {};
  const roleOptions = parseResult?.related_job_roles ?? parseResult?.role_matches?.matches ?? [];
  const roleSelection = selectionResult?.selected_role ?? null;
  const visibleContexts = selectionResult?.interview_contexts ?? parseResult?.interview_contexts ?? {};
  const selectedRoleKey = roleSelection?.role_key ?? parseResult?.selected_role_key ?? "";
  const currentStep = selectionResult
    ? "contexts"
    : parseResult?.role_selection_required
      ? "roles"
      : parseResult
        ? "parse"
        : busyState === "parsing"
          ? "parse"
          : "upload";

  async function parseResume() {
    if (!resumeFile) {
      setErrorMessage("Choose a PDF resume first.");
      return;
    }

    setBusyState("parsing");
    setErrorMessage("");
    setSelectionResult(null);

    const formData = new FormData();
    formData.append("resume_file", resumeFile);
    formData.append("persist_resume", "true");
    formData.append("run_role_matching", "true");
    formData.append("use_groq_profiles", String(useGroqProfiles));
    formData.append("persist_role_matches", "true");
    formData.append("max_roles", String(maxRoles));
    formData.append("persist_interview_contexts", "true");

    try {
      const response = await fetch(`${baseUrl}/resume/parse-upload`, {
        method: "POST",
        headers: buildApiHeaders(accessToken),
        body: formData,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(resolveErrorMessage(payload, "Resume parsing failed."));
      }

      const autoSelectionResult = !payload.role_selection_required && payload.selected_role_key
        ? {
            session_id: payload.session_id,
            selected_role: {
              role_key: payload.selected_role_key,
              title: formatTitleFromKey(payload.selected_role_key),
              skill_gaps: [],
            },
            interview_contexts: payload.interview_contexts,
          }
        : null;

      setParseResult(payload);
      syncWorkflowState({
        apiBaseUrl: baseUrl,
        sessionId: payload.session_id || "",
        selectedRoleKey: payload.role_selection_required ? "" : payload.selected_role_key || "",
        selectedRoleTitle:
          payload.role_selection_required || !payload.selected_role_key
            ? ""
            : formatTitleFromKey(payload.selected_role_key),
        resumeReady: true,
        roleReady: !payload.role_selection_required && Boolean(payload.selected_role_key),
        contextsReady: Boolean(payload.interview_contexts && Object.keys(payload.interview_contexts).length),
        resumeParseResult: payload,
        resumeSelectionResult: autoSelectionResult,
        resumeFileName: resumeFile?.name || workflowState?.resumeFileName || "",
        ...DOWNSTREAM_WORKFLOW_RESET,
      });
      setSelectionResult(autoSelectionResult);
      setInfoMessage(
        payload.role_selection_required
          ? "Parsing done. Choose a role."
          : "Resume parsed. Ready for assessment.",
      );
    } catch (error) {
      setParseResult(null);
      syncWorkflowState({
        sessionId: "",
        selectedRoleKey: "",
        selectedRoleTitle: "",
        resumeParseResult: null,
        resumeSelectionResult: null,
        resumeReady: false,
        roleReady: false,
        contextsReady: false,
        resumeFileName: resumeFile?.name || workflowState?.resumeFileName || "",
        ...DOWNSTREAM_WORKFLOW_RESET,
      });
      setErrorMessage(error.message);
    } finally {
      setBusyState("idle");
    }
  }

  async function selectRole(role) {
    setBusyState("selecting");
    setErrorMessage("");
    try {
      const response = await fetch(`${baseUrl}/resume/select-role`, {
        method: "POST",
        headers: buildApiHeaders(accessToken, {
          "Content-Type": "application/json",
        }),
        body: JSON.stringify({
          session_id: parseResult.session_id,
          role_key: role.role_key,
          role_title: role.title,
          skill_gaps: role.skill_gaps,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(resolveErrorMessage(payload, "Role selection failed."));
      }

      setSelectionResult(payload);
      syncWorkflowState({
        apiBaseUrl: baseUrl,
        sessionId: parseResult.session_id,
        selectedRoleKey: payload.selected_role?.role_key || role.role_key,
        selectedRoleTitle: payload.selected_role?.title || role.title,
        resumeParseResult: parseResult,
        resumeSelectionResult: payload,
        resumeReady: true,
        roleReady: true,
        contextsReady: Boolean(payload.interview_contexts && Object.keys(payload.interview_contexts).length),
      });
      setInfoMessage(`${role.title} locked.`);
    } catch (error) {
      setErrorMessage(error.message);
    } finally {
      setBusyState("idle");
    }
  }

  function titleToRoleKey(title) {
    return title
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "");
  }

  function selectCustomRole() {
    const title = customRoleTitle.trim();
    if (!title || !parseResult) return;
    const role_key = titleToRoleKey(title);
    selectRole({ role_key, title, description: `Custom role: ${title}`, skill_gaps: [], match_percent: null });
  }

  function handleFileSelection(file) {
    if (!file) {
      return;
    }
    setResumeFile(file);
    setParseResult(null);
    setSelectionResult(null);
    setErrorMessage("");
    syncWorkflowState({
      sessionId: "",
      selectedRoleKey: "",
      selectedRoleTitle: "",
      resumeParseResult: null,
      resumeSelectionResult: null,
      resumeFileName: file.name,
      resumeReady: false,
      roleReady: false,
      contextsReady: false,
      ...DOWNSTREAM_WORKFLOW_RESET,
    });
    setInfoMessage(`${file.name} ready.`);
  }

  function handleDrop(event) {
    event.preventDefault();
    setDragActive(false);
    handleFileSelection(event.dataTransfer.files?.[0] ?? null);
  }

  const skillChips = parsedResume.skills ?? [];
  const interests = parsedResume.interests ?? [];
  const projects = parsedResume.projects ?? [];
  const visibleSkills = skillChips.slice(0, 8);
  const visibleProjects = projects.slice(0, 2);
  const hiddenProjectCount = Math.max(projects.length - visibleProjects.length, 0);
  const contextEntries = Object.entries(visibleContexts);
  const topRoleTitle = roleSelection?.title ?? roleOptions[0]?.title ?? "Awaiting role selection";
  const resumeFileLabel = resumeFile?.name || workflowState?.resumeFileName || "Drop your resume here or click to browse.";
  const canSubmitResume = Boolean(resumeFile) && busyState === "idle";
  const backendStatusHint = backendStatus === "checking"
    ? "Health check still running."
    : backendStatus === "offline"
      ? "Backend looks offline. Parse will still try once."
      : "";
  const uploadHint = parseResult
    ? "Upload another PDF to replace this session."
    : "PDF only.";
  const currentPhaseLabel = UPLOAD_PHASE_LABELS[currentStep] || UPLOAD_PHASE_LABELS.upload;
  const selectedPathLabel = selectedRoleKey
    ? roleRequiresDsa(selectedRoleKey)
      ? "Includes DSA"
      : "Skips DSA"
    : "Path pending";
  const sessionLabel = parseResult?.session_id ? "Session live" : "No session";

  return (
    <div className="page-shell upload-shell">
      <section className="upload-hero">
        <div className="glass-panel upload-hero__primary">
          <div className="eyebrow-row">
            <span className="eyebrow">Resume Studio</span>
            <span className={`status-pill status-pill--${backendStatus}`}>
              {backendStatus === "online" ? "Backend live" : backendStatus === "checking" ? "Checking backend" : "Backend offline"}
            </span>
          </div>
          <h1>Resume Intake</h1>
          <p className="upload-hero__summary">Upload the resume, extract the candidate profile, then lock the interview path.</p>
          <div className="hero-tags upload-hero__tags">
            <span>{currentPhaseLabel}</span>
            <span>{selectedPathLabel}</span>
            <span>{sessionLabel}</span>
          </div>

          <div className="upload-hero__stats">
            <article className="upload-stat-card">
              <span>Resume</span>
              <strong>{resumeFile?.name || workflowState?.resumeFileName || "No PDF yet"}</strong>
              <p>{uploadHint}</p>
            </article>
            <article className="upload-stat-card">
              <span>Role lock</span>
              <strong>{roleSelection?.title || topRoleTitle}</strong>
              <p>{selectedRoleKey ? "Ready for assessment." : roleOptions.length ? `${roleOptions.length} matches waiting.` : "Awaiting parser output."}</p>
            </article>
            <article className="upload-stat-card">
              <span>Project data</span>
              <strong>{projects.length}</strong>
              <p>{projects.length ? "Project discussion context available." : "Projects will appear after parsing."}</p>
            </article>
          </div>
        </div>

        <aside className="upload-hero__rail">
          <section className="glass-panel upload-rail-card">
            <p className="section-kicker">Flow</p>
            <h2>{currentPhaseLabel}</h2>
            <div className="upload-phase-list">
              {Object.entries(UPLOAD_PHASE_LABELS).map(([phaseKey, label]) => (
                <div
                  key={phaseKey}
                  className={`upload-phase-list__item ${currentStep === phaseKey ? "upload-phase-list__item--active" : ""} ${currentStep === "contexts" || (currentStep === "roles" && phaseKey === "upload") || (parseResult && phaseKey === "parse") ? "upload-phase-list__item--complete" : ""}`}
                >
                  <span>{label}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="glass-panel upload-rail-card">
            <p className="section-kicker">Session feed</p>
            <div className="upload-feed">
              <article>
                <span>Session</span>
                <strong>{parseResult?.session_id ?? "Not created"}</strong>
              </article>
              <article>
                <span>Contexts</span>
                <strong>{Object.keys(visibleContexts).length || 0}</strong>
              </article>
              <article>
                <span>Role path</span>
                <strong>{selectedPathLabel}</strong>
              </article>
            </div>
          </section>
        </aside>
      </section>

      <section className="upload-studio">
        <form
          className="glass-panel upload-drop-panel"
          onSubmit={(event) => {
            event.preventDefault();
            parseResume();
          }}
        >
          <div className="panel-head">
            <div>
              <p className="section-kicker">Upload deck</p>
              <h2>Start with one resume.</h2>
            </div>
            <span className="role-count">{resumeFile ? "PDF ready" : "Waiting for file"}</span>
          </div>

          <label
            className={`dropzone ${dragActive ? "dropzone--active" : ""}`}
            onDragEnter={(event) => {
              event.preventDefault();
              setDragActive(true);
            }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={(event) => {
              event.preventDefault();
              setDragActive(false);
            }}
            onDrop={handleDrop}
          >
            <input
              type="file"
              accept="application/pdf"
              onChange={(event) => handleFileSelection(event.target.files?.[0] ?? null)}
            />
            <div className="dropzone__icon">PDF</div>
            <div>
              <strong>{resumeFileLabel}</strong>
              <p>{uploadHint}</p>
            </div>
          </label>

          <div className="action-row upload-actions">
            <button className="primary-button action-row__button" type="submit" disabled={!canSubmitResume}>
              {busyState === "parsing" ? "Parsing resume..." : "Parse resume"}
            </button>

            {selectedRoleKey && parseResult?.session_id ? (
              <button
                className="secondary-button action-row__button"
                type="button"
                onClick={() => onNavigate?.("assessment")}
              >
                Open assessment
              </button>
            ) : null}
          </div>

          <div className="upload-drop-panel__footer">
            <div className="upload-control-chip-row">
              <span className="upload-control-chip">PDF only</span>
              <span className="upload-control-chip">Role matching included</span>
              <span className="upload-control-chip">Session-safe parsing</span>
            </div>
            {backendStatusHint ? <p className="stack-note">{backendStatusHint}</p> : null}
          </div>

          <details className="raw-block intake-settings">
            <summary>Parser options</summary>
            <label className="field-label" htmlFor="api-base-url">
              Backend URL
            </label>
            <input
              id="api-base-url"
              className="text-input"
              value={apiBaseUrl}
              onChange={(event) => {
                const nextValue = event.target.value;
                setApiBaseUrl(nextValue);
                syncWorkflowState({ apiBaseUrl: nextValue });
              }}
              placeholder="http://127.0.0.1:8000"
            />

            <div className="control-grid">
              <label className="toggle-card">
                <input
                  type="checkbox"
                  checked={useGroqProfiles}
                  onChange={(event) => setUseGroqProfiles(event.target.checked)}
                />
                <div>
                  <strong>LLM role generation</strong>
                  <p>Groq first, curated fallback second.</p>
                </div>
              </label>

              <label className="toggle-card toggle-card--compact">
                <span>
                  <strong>Role suggestions</strong>
                  <p>1 to 10</p>
                </span>
                <input
                  type="number"
                  min="1"
                  max="10"
                  value={maxRoles}
                  onChange={(event) => setMaxRoles(Number(event.target.value) || 1)}
                />
              </label>
            </div>

            <a className="docs-link" href={`${baseUrl}/docs`} target="_blank" rel="noreferrer">
              Open backend docs
            </a>
          </details>

          <div className="message-stack">
            <p className="info-banner">{infoMessage}</p>
            {errorMessage ? <p className="error-banner">{errorMessage}</p> : null}
          </div>
        </form>

        <div className="upload-side-stack">
          <section className="glass-panel upload-readiness-panel">
            <div className="panel-head panel-head--tight">
              <div>
                <p className="section-kicker">Readiness</p>
                <h2>Current state.</h2>
              </div>
            </div>
            <div className="upload-readiness-grid">
              <article className="upload-readiness-card">
                <span>Profile</span>
                <strong>{parsedResume.name || "Waiting for resume"}</strong>
                <p>{parseResult ? "Resume extracted." : "Parse not started."}</p>
              </article>
              <article className="upload-readiness-card">
                <span>Roles</span>
                <strong>{roleSelection?.title || (roleOptions.length ? `${roleOptions.length} options` : "No roles yet")}</strong>
                <p>{roleSelection ? "Locked for assessment." : "Awaiting selection."}</p>
              </article>
              <article className="upload-readiness-card">
                <span>Contexts</span>
                <strong>{Object.keys(visibleContexts).length || 0}</strong>
                <p>{Object.keys(visibleContexts).length ? "Saved for later rounds." : "No context payloads yet."}</p>
              </article>
            </div>
          </section>

          <section className="glass-panel upload-spotlight-panel">
            <div className="panel-head panel-head--tight">
              <div>
                <p className="section-kicker">Spotlight</p>
                <h2>{roleSelection?.title || "Role not locked"}</h2>
              </div>
            </div>
            <p className="hero-text">
              {roleSelection
                ? `${roleSelection.title} is locked. Continue to assessment when you are ready.`
                : parseResult
                  ? "Parser output is ready. Pick the role that should drive the interview flow."
                  : "Upload and parse a resume to unlock role matching."}
            </p>
            <div className="hero-tags upload-spotlight-panel__tags">
              <span>{selectedPathLabel}</span>
              <span>{parseResult ? `${roleOptions.length} role matches` : "No matches yet"}</span>
            </div>
          </section>
        </div>
      </section>

      {parseResult ? (
        <>
          <section className="upload-insight-grid">
            <section className="glass-panel upload-profile-card">
              <div className="panel-head panel-head--tight">
                <div>
                  <p className="section-kicker">Profile</p>
                  <h2>Candidate snapshot</h2>
                </div>
              </div>
              <div className="metric-strip">
                <div>
                  <span>Skills</span>
                  <strong>{metricValue(skillChips)}</strong>
                </div>
                <div>
                  <span>Projects</span>
                  <strong>{metricValue(projects)}</strong>
                </div>
                <div>
                  <span>Interests</span>
                  <strong>{metricValue(interests)}</strong>
                </div>
              </div>
              <h3 className="candidate-name">{parsedResume.name || "Unnamed candidate"}</h3>
              {parsedResume.summary ? <p className="candidate-summary">{parsedResume.summary}</p> : null}
              <div className="chip-row">
                {visibleSkills.map((skill) => (
                  <span key={skill} className="chip">{skill}</span>
                ))}
              </div>
              {skillChips.length > visibleSkills.length ? (
                <p className="stack-note">+{skillChips.length - visibleSkills.length} more skills</p>
              ) : null}
              {interests.length ? (
                <div className="subsection">
                  <p className="subsection-title">Interests</p>
                  <p>{interests.join(" • ")}</p>
                </div>
              ) : null}
            </section>

            <section className="glass-panel upload-project-card">
              <div className="panel-head panel-head--tight">
                <div>
                  <p className="section-kicker">Projects</p>
                  <h2>Discussion anchors</h2>
                </div>
              </div>
              <div className="project-stack">
                {visibleProjects.length ? (
                  visibleProjects.map((project) => (
                    <article key={`${project.title}-${project.role}`} className="project-card">
                      <div className="project-card__header">
                        <h3>{project.title}</h3>
                        <span>{project.role || "Contribution not extracted"}</span>
                      </div>
                      <p>{project.description}</p>
                      <div className="chip-row chip-row--tight">
                        {(project.tech_stack || []).map((item) => (
                          <span key={item} className="chip chip--ghost">{item}</span>
                        ))}
                      </div>
                    </article>
                  ))
                ) : (
                  <p className="empty-state">No projects were extracted from this resume.</p>
                )}
                {hiddenProjectCount > 0 ? <p className="stack-note">+{hiddenProjectCount} more project{hiddenProjectCount === 1 ? "" : "s"}</p> : null}
              </div>
            </section>
          </section>

          <ResumeScoreCard score={parseResult?.resume_quality} />

          <section className="glass-panel upload-role-board">
            <div className="panel-head">
              <div>
                <p className="section-kicker">Roles</p>
                <h2>Lock the interview track.</h2>
              </div>
              <span className="role-count">{roleSelection?.title || `${roleOptions.length} matches`}</span>
            </div>

            {roleOptions.length > 0 && (
              <>
                <div className="role-card-grid">
                  {roleOptions.map((role) => {
                    const isSelected = selectedRoleKey === role.role_key;
                    const requiresDsa = roleRequiresDsa(role.role_key);
                    return (
                      <article key={role.role_key} className={`role-card ${isSelected ? "role-card--selected" : ""}`}>
                        <div className="role-card__topline">
                          <span>{role.title}</span>
                          <strong>{role.match_percent}%</strong>
                        </div>
                        <div className="chip-row chip-row--tight role-card__chips">
                          <span className="chip chip--ghost">{requiresDsa ? "DSA round" : "No DSA"}</span>
                          {isSelected ? <span className="chip">Locked</span> : null}
                        </div>
                        <p>{role.description}</p>
                        {role.skill_gaps?.length ? (
                          <div className="subsection">
                            <p className="subsection-title">Skill gaps</p>
                            <div className="chip-row chip-row--tight">
                              {role.skill_gaps.map((gap) => (
                                <span key={gap} className="chip chip--warning">{gap}</span>
                              ))}
                            </div>
                          </div>
                        ) : null}
                        <div className="role-card__actions">
                          <button
                            type="button"
                            className="secondary-button"
                            onClick={() => selectRole(role)}
                            disabled={busyState === "selecting"}
                          >
                            {isSelected ? "Role Locked" : busyState === "selecting" ? "Saving role..." : "Select role"}
                          </button>
                        </div>
                      </article>
                    );
                  })}
                </div>
              </>
            )}

            <div className="all-roles-section">
              <button
                type="button"
                className="all-roles-toggle"
                onClick={() => setShowAllFresherRoles((v) => !v)}
              >
                <span>{showAllFresherRoles ? "Hide" : "Browse"} all fresher roles ({COMMON_FRESHER_ROLES.length})</span>
                <span className="all-roles-toggle__arrow">{showAllFresherRoles ? "▲" : "▼"}</span>
              </button>

              {showAllFresherRoles && (
                <div className="fresher-role-grid">
                  {COMMON_FRESHER_ROLES.map((role) => {
                    const isSelected = selectedRoleKey === role.role_key;
                    const alreadyInMatches = roleOptions.some((r) => r.role_key === role.role_key);
                    const requiresDsa = roleRequiresDsa(role.role_key);
                    return (
                      <article key={role.role_key} className={`fresher-role-card ${isSelected ? "fresher-role-card--selected" : ""}`}>
                        <div className="fresher-role-card__head">
                          <strong>{role.title}</strong>
                          {alreadyInMatches && <span className="badge badge--sea">In your matches</span>}
                        </div>
                        <div className="chip-row chip-row--tight role-card__chips">
                          <span className="chip chip--ghost">{requiresDsa ? "DSA round" : "No DSA"}</span>
                          {isSelected ? <span className="chip">Locked</span> : null}
                        </div>
                        <p>{role.description}</p>
                        <button
                          type="button"
                          className="secondary-button"
                          onClick={() => selectRole(role)}
                          disabled={busyState === "selecting"}
                        >
                          {isSelected ? "Role Locked" : busyState === "selecting" ? "Saving role..." : "Select role"}
                        </button>
                      </article>
                    );
                  })}
                </div>
              )}
            </div>

            <div className="custom-role-section">
              <p className="section-kicker" style={{ margin: 0 }}>Custom role</p>
              <div className="custom-role-input-row">
                <input
                  className="text-input"
                  type="text"
                  placeholder="Type a role title"
                  value={customRoleTitle}
                  onChange={(e) => setCustomRoleTitle(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") selectCustomRole(); }}
                  disabled={!parseResult || busyState === "selecting"}
                />
                <button
                  type="button"
                  className="primary-button custom-role-btn"
                  onClick={selectCustomRole}
                  disabled={!customRoleTitle.trim() || !parseResult || busyState === "selecting"}
                >
                  {busyState === "selecting" ? "Saving..." : "Use this role"}
                </button>
              </div>
              {customRoleTitle.trim() && (
                <p className="custom-role-preview">Will save as: <code>{titleToRoleKey(customRoleTitle)}</code></p>
              )}
            </div>
          </section>

          <section className="glass-panel advanced-panel">
            <div className="panel-head panel-head--tight">
              <div>
                <p className="section-kicker">Developer Data</p>
                <h2>Raw payloads.</h2>
              </div>
            </div>

            {contextEntries.length ? (
              <details className="raw-block">
                <summary>Stored round contexts ({contextEntries.length})</summary>
                <div className="context-stack">
                  {contextEntries.map(([roundKey, context]) => (
                    <article key={roundKey} className="context-card">
                      <div className="context-card__header">
                        <h3>{ROUND_LABELS[roundKey] || formatTitleFromKey(roundKey)}</h3>
                        <span>{context.selected_role_title || formatTitleFromKey(context.selected_role_key || "") || "No role selected"}</span>
                      </div>
                      <pre className="json-preview">{JSON.stringify(context, null, 2)}</pre>
                    </article>
                  ))}
                </div>
              </details>
            ) : null}

            <details className="raw-block">
              <summary>Parsed resume JSON</summary>
              <pre className="json-preview">{JSON.stringify(parsedResume, null, 2)}</pre>
            </details>

            <details className="raw-block">
              <summary>Role matcher JSON</summary>
              <pre className="json-preview">{JSON.stringify(roleOptions, null, 2)}</pre>
            </details>
          </section>
        </>
      ) : null}
    </div>
  );
}
