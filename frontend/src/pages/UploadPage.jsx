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

  // Two steps and nothing else: upload the resume, then choose the role.
  // The old status panels (flow, session feed, readiness, spotlight) and the
  // raw JSON payloads repeated the same state several times over.
  const parsed = Boolean(parseResult?.session_id);
  const skillCount = (parsedResume.skills?.length || 0) + (parsedResume.technologies?.length || 0);
  const projectCount = parsedResume.projects?.length || 0;

  return (
    <div className="pp-page pp-resume">
      <header className="pp-page__head">
        <h1>{parsed ? "Choose your role" : "Upload your resume"}</h1>
        <p>
          {parsed
            ? "Pick the role you're preparing for. Your interview questions are built around it."
            : "We read your resume to tailor every interview question to you."}
        </p>
      </header>

      <form
        className="pp-card pp-upload"
        onSubmit={(event) => {
          event.preventDefault();
          parseResume();
        }}
      >
        <label
          className={`pp-drop ${dragActive ? "pp-drop--active" : ""} ${resumeFile ? "pp-drop--ready" : ""}`}
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
          <span className="pp-drop__icon" aria-hidden="true">PDF</span>
          <span className="pp-drop__text">
            <strong>{resumeFile ? resumeFileLabel : "Drop your resume here, or click to browse"}</strong>
            <small>{resumeFile ? "Click to choose a different file" : "PDF only"}</small>
          </span>
        </label>

        <button className="primary-button pp-upload__submit" type="submit" disabled={!canSubmitResume}>
          {busyState === "parsing" ? "Reading your resume…" : parsed ? "Re-read resume" : "Read my resume"}
        </button>

        {errorMessage ? <p className="error-banner">{errorMessage}</p> : null}

        {parsed ? (
          <p className="pp-upload__found">
            <span aria-hidden="true">✓</span>
            Found {parsedResume.name ? <strong>{parsedResume.name}</strong> : "your profile"}
            {` · ${skillCount} skills · ${projectCount} ${projectCount === 1 ? "project" : "projects"}`}
          </p>
        ) : null}
      </form>

      {parsed ? <ResumeScoreCard score={parseResult?.resume_quality} /> : null}

      {parsed ? (
        <section className="pp-roles" aria-labelledby="pp-roles-title">
          <h2 id="pp-roles-title">Best matches for you</h2>

          {roleOptions.length > 0 ? (
            <div className="pp-role-grid">
              {roleOptions.map((role) => {
                const isSelected = selectedRoleKey === role.role_key;
                return (
                  <article key={role.role_key} className={`pp-role ${isSelected ? "pp-role--selected" : ""}`}>
                    <div className="pp-role__top">
                      <h3>{role.title}</h3>
                      <span className="pp-role__match">{Math.round(role.match_percent)}%</span>
                    </div>
                    <p>{role.description}</p>
                    {role.skill_gaps?.length ? (
                      <p className="pp-role__gaps">
                        <span>To brush up:</span> {role.skill_gaps.slice(0, 4).join(", ")}
                      </p>
                    ) : null}
                    <button
                      type="button"
                      className={isSelected ? "primary-button" : "secondary-button"}
                      onClick={() => selectRole(role)}
                      disabled={busyState === "selecting"}
                    >
                      {isSelected ? "✓ Selected" : busyState === "selecting" ? "Saving…" : "Choose this role"}
                    </button>
                  </article>
                );
              })}
            </div>
          ) : null}

          <details className="pp-more" open={showAllFresherRoles} onToggle={(event) => setShowAllFresherRoles(event.currentTarget.open)}>
            <summary>Browse all fresher roles ({COMMON_FRESHER_ROLES.length})</summary>
            <div className="pp-role-grid pp-role-grid--compact">
              {COMMON_FRESHER_ROLES.map((role) => {
                const isSelected = selectedRoleKey === role.role_key;
                return (
                  <button
                    key={role.role_key}
                    type="button"
                    className={`pp-role-chip ${isSelected ? "pp-role-chip--selected" : ""}`}
                    onClick={() => selectRole(role)}
                    disabled={busyState === "selecting"}
                  >
                    {isSelected ? "✓ " : ""}{role.title}
                  </button>
                );
              })}
            </div>
          </details>

          <div className="pp-custom-role">
            <input
              className="text-input"
              type="text"
              aria-label="Custom role"
              placeholder="Or type any role, e.g. Junior Android Developer"
              value={customRoleTitle}
              onChange={(e) => setCustomRoleTitle(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") selectCustomRole(); }}
              disabled={busyState === "selecting"}
            />
            <button
              type="button"
              className="secondary-button"
              onClick={selectCustomRole}
              disabled={!customRoleTitle.trim() || busyState === "selecting"}
            >
              Use this role
            </button>
          </div>
        </section>
      ) : null}

      {selectedRoleKey && parsed ? (
        <div className="pp-continue" role="status">
          <span>
            Preparing for <strong>{roleSelection?.title || selectedRoleTitleFallback(roleOptions, selectedRoleKey)}</strong>
          </span>
          <button type="button" className="primary-button" onClick={() => onNavigate?.("assessment")}>
            Continue to assessment →
          </button>
        </div>
      ) : null}
    </div>
  );
}

function selectedRoleTitleFallback(roleOptions, selectedRoleKey) {
  const match = roleOptions.find((role) => role.role_key === selectedRoleKey)
    || COMMON_FRESHER_ROLES.find((role) => role.role_key === selectedRoleKey);
  return match?.title || "your chosen role";
}
