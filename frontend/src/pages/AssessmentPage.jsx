import { useEffect, useState } from "react";

import WorkflowResetControl from "../components/WorkflowResetControl";
import { buildApiHeaders } from "../lib/api";
import { buildWorkflowResetPatch } from "../lib/workflowReset";

const API_DEFAULT = "http://127.0.0.1:8000";

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

export default function AssessmentPage({ authState, workflowState, onWorkflowStateChange, onNavigate }) {
  const [apiBaseUrl, setApiBaseUrl] = useState(workflowState?.apiBaseUrl || API_DEFAULT);
  const [questionCount, setQuestionCount] = useState(8);
  const [busyState, setBusyState] = useState("idle");
  const [errorMessage, setErrorMessage] = useState("");
  const [infoMessage, setInfoMessage] = useState("Start or resume the MCQ round.");
  const [assessmentSession, setAssessmentSession] = useState(null);
  const [selectedOptionId, setSelectedOptionId] = useState("");
  const [lastSubmission, setLastSubmission] = useState(null);

  const syncedSessionId = workflowState?.sessionId || "";
  const syncedRoleKey = workflowState?.selectedRoleKey || "";
  const syncedRoleTitle = workflowState?.selectedRoleTitle || "";
  const accessToken = authState?.accessToken || "";

  useEffect(() => {
    setApiBaseUrl(workflowState?.apiBaseUrl || API_DEFAULT);
  }, [workflowState?.apiBaseUrl]);

  function syncWorkflowState(patch) {
    onWorkflowStateChange?.((current) => ({
      ...current,
      ...patch,
    }));
  }

  function handleWorkflowReset(payload, { successMessage } = {}) {
    setAssessmentSession(null);
    setSelectedOptionId("");
    setLastSubmission(null);
    setErrorMessage("");
    setInfoMessage(successMessage || "Assessment reset.");
    onWorkflowStateChange?.((current) => ({
      ...current,
      ...buildWorkflowResetPatch(current, payload?.cleared_targets),
    }));
  }

  const baseUrl = trimTrailingSlash(apiBaseUrl);
  const sessionId = syncedSessionId.trim();
  const roleKey = syncedRoleKey.trim();
  const roleTitle = syncedRoleTitle || formatTitleFromKey(roleKey);
  const currentQuestionIndex = assessmentSession?.current_question_index ?? 0;
  const currentQuestion = assessmentSession?.questions?.[currentQuestionIndex] ?? null;
  const questionCards = assessmentSession?.questions ?? [];
  async function startAssessment({ forceRestart = false } = {}) {
    if (!sessionId) {
      setErrorMessage("Start from Resume Intake first.");
      return;
    }

    setBusyState("starting");
    setErrorMessage("");
    setLastSubmission(null);

    try {
      const response = await fetch(`${baseUrl}/assessment/start`, {
        method: "POST",
        headers: buildApiHeaders(accessToken, {
          "Content-Type": "application/json",
        }),
        body: JSON.stringify({
          session_id: sessionId,
          role_key: roleKey || undefined,
          total_questions: questionCount,
          force_restart: forceRestart,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(resolveErrorMessage(payload, "Assessment start failed."));
      }

      setAssessmentSession(payload);
      setSelectedOptionId("");
      syncWorkflowState({
        apiBaseUrl: baseUrl,
        sessionId,
        selectedRoleKey: payload.role_key,
        selectedRoleTitle: formatTitleFromKey(payload.role_key),
        resumeReady: true,
        roleReady: true,
        assessmentStatus: payload.status === "completed" ? "complete" : "active",
        assessmentAnsweredCount: payload.answered_count ?? 0,
        assessmentTotalQuestions: payload.total_questions ?? 0,
      });
      setInfoMessage(
        payload.created_assessment_session
          ? "Assessment ready."
          : "Assessment restored.",
      );
    } catch (error) {
      setAssessmentSession(null);
      setErrorMessage(error.message);
    } finally {
      setBusyState("idle");
    }
  }

  async function loadAssessmentSession() {
    if (!sessionId) {
      setErrorMessage("Start from Resume Intake first.");
      return;
    }

    setBusyState("loading");
    setErrorMessage("");

    try {
      const response = await fetch(`${baseUrl}/assessment/session/${encodeURIComponent(sessionId)}`, {
        headers: buildApiHeaders(accessToken),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(resolveErrorMessage(payload, "Could not load assessment progress."));
      }

      setAssessmentSession(payload);
      setSelectedOptionId("");
      setLastSubmission(null);
      syncWorkflowState({
        apiBaseUrl: baseUrl,
        sessionId,
        selectedRoleKey: payload.role_key,
        selectedRoleTitle: formatTitleFromKey(payload.role_key),
        resumeReady: true,
        roleReady: true,
        assessmentStatus: payload.status === "completed" ? "complete" : "active",
        assessmentAnsweredCount: payload.answered_count ?? 0,
        assessmentTotalQuestions: payload.total_questions ?? 0,
      });
      setInfoMessage(
        payload.status === "completed"
          ? "Assessment complete."
          : "Assessment restored.",
      );
    } catch (error) {
      setAssessmentSession(null);
      setErrorMessage(error.message);
    } finally {
      setBusyState("idle");
    }
  }

  async function submitCurrentAnswer() {
    if (!currentQuestion) {
      setErrorMessage("There is no active question to submit.");
      return;
    }
    if (!selectedOptionId) {
      setErrorMessage("Choose one option before submitting.");
      return;
    }

    setBusyState("submitting");
    setErrorMessage("");

    try {
      const response = await fetch(`${baseUrl}/assessment/submit`, {
        method: "POST",
        headers: buildApiHeaders(accessToken, {
          "Content-Type": "application/json",
        }),
        body: JSON.stringify({
          session_id: sessionId,
          question_id: currentQuestion.question_id,
          selected_option_id: selectedOptionId,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(resolveErrorMessage(payload, "Answer submission failed."));
      }

      setAssessmentSession(payload);
      setLastSubmission(payload.submitted_result || null);
      setSelectedOptionId("");
      syncWorkflowState({
        assessmentStatus: payload.status === "completed" ? "complete" : "active",
        assessmentAnsweredCount: payload.answered_count ?? 0,
        assessmentTotalQuestions: payload.total_questions ?? 0,
      });
      setInfoMessage(
        payload.status === "completed"
          ? "Assessment complete."
          : "Answer saved.",
      );
    } catch (error) {
      setErrorMessage(error.message);
    } finally {
      setBusyState("idle");
    }
  }

  const answered = assessmentSession?.answered_count ?? 0;
  const total = assessmentSession?.total_questions ?? 0;
  const finished = Boolean(assessmentSession) && !currentQuestion;
  const progressPct = total ? Math.round((answered / total) * 100) : 0;

  if (!sessionId || !roleKey) {
    return (
      <div className="pp-page">
        <section className="interview-launch-card">
          <p className="section-kicker">Assessment</p>
          <h2>Upload your resume first</h2>
          <p>The assessment is built for the role you choose, so start there.</p>
          <button className="primary-button" type="button" onClick={() => onNavigate?.("upload")}>
            Go to resume
          </button>
        </section>
      </div>
    );
  }

  return (
    <div className="pp-page pp-assessment">
      <div className="action-row">
        {assessmentSession ? (
          <button
            className="secondary-button action-row__button"
            type="button"
            onClick={() => startAssessment({ forceRestart: true })}
            disabled={busyState !== "idle"}
          >
            {busyState === "starting" ? "Restarting…" : "Start over"}
          </button>
        ) : null}
        <WorkflowResetControl
          accessToken={accessToken}
          apiBaseUrl={baseUrl}
          sessionId={sessionId}
          currentTarget="assessment"
          currentLabel="Assessment"
          onResetApplied={handleWorkflowReset}
          triggerClassName="secondary-button action-row__button"
          triggerLabel="Reset"
          disabled={busyState !== "idle"}
        />
      </div>

      {errorMessage ? <p className="error-banner">{errorMessage}</p> : null}

      {lastSubmission && !finished ? (
        <section className={`pp-feedback pp-feedback--${lastSubmission.is_correct ? "right" : "wrong"}`}>
          <strong>
            <span className="pp-feedback__label">Last question</span>
            {lastSubmission.is_correct
              ? "✓ Correct"
              : `✗ Not quite - the answer was ${lastSubmission.correct_option_id.toUpperCase()}`}
          </strong>
          {lastSubmission.explanation ? <p>{lastSubmission.explanation}</p> : null}
        </section>
      ) : null}

      {!assessmentSession ? (
        <section className="interview-launch-card">
          <p className="section-kicker">Assessment</p>
          <h2>Quick skills check</h2>
          <p>A short set of multiple-choice questions on the skills your role needs. Answer at your own pace.</p>
          <button className="primary-button" type="button" onClick={() => startAssessment()} disabled={busyState !== "idle"}>
            {busyState === "starting" ? "Preparing questions…" : "Start assessment"}
          </button>
        </section>
      ) : finished ? (
        <section className="interview-launch-card">
          <p className="section-kicker">Assessment complete</p>
          <h2>Nice work - all {total} answered</h2>
          <p>Next up is the live technical interview with Maya.</p>
          <button className="primary-button" type="button" onClick={() => onNavigate?.("technical")}>
            Continue to technical interview →
          </button>
        </section>
      ) : (
        <section className="pp-card pp-question">
          <div className="pp-question__progress">
            <span>Question {currentQuestionIndex + 1} of {total}</span>
            <span className="pp-question__bar" aria-hidden="true">
              <i style={{ width: `${progressPct}%` }} />
            </span>
          </div>

          <p className="pp-question__prompt">{currentQuestion.prompt}</p>

          <div className="assessment-option-list" role="radiogroup" aria-label="Answer options">
            {currentQuestion.options.map((option) => {
              const checked = selectedOptionId === option.id;
              return (
                <label key={option.id} className={`assessment-option ${checked ? "assessment-option--selected" : ""}`}>
                  <input
                    className="assessment-option__input"
                    type="radio"
                    name="assessment-option"
                    value={option.id}
                    checked={checked}
                    onChange={() => setSelectedOptionId(option.id)}
                  />
                  <span className="assessment-option__marker">{option.id.toUpperCase()}</span>
                  <span className="assessment-option__text">{option.text}</span>
                </label>
              );
            })}
          </div>

          <button
            className="primary-button pp-question__submit"
            type="button"
            onClick={submitCurrentAnswer}
            disabled={busyState !== "idle" || !selectedOptionId}
          >
            {busyState === "submitting" ? "Saving…" : "Submit answer"}
          </button>
        </section>
      )}

    </div>
  );
}
