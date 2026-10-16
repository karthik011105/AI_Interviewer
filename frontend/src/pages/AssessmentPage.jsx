import { useEffect, useState } from "react";

import WorkflowResetControl from "../components/WorkflowResetControl";
import { buildApiHeaders } from "../lib/api";
import { formatOptionIds, languageLabel, splitQuestion } from "../lib/assessmentQuestion";
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
  const [busyState, setBusyState] = useState("idle");
  const [errorMessage, setErrorMessage] = useState("");
  const [assessmentSession, setAssessmentSession] = useState(null);
  const [selectedIds, setSelectedIds] = useState([]);
  const [lastSubmission, setLastSubmission] = useState(null);

  const syncedSessionId = workflowState?.sessionId || "";
  const syncedRoleKey = workflowState?.selectedRoleKey || "";
  const syncedRoleTitle = workflowState?.selectedRoleTitle || "";
  const accessToken = authState?.accessToken || "";

  useEffect(() => {
    setApiBaseUrl(workflowState?.apiBaseUrl || API_DEFAULT);
  }, [workflowState?.apiBaseUrl]);

  // While the next section is being written, check for it every few seconds.
  const waitingForSection =
    Boolean(assessmentSession) &&
    assessmentSession.status !== "completed" &&
    !assessmentSession.questions?.[assessmentSession.current_question_index ?? 0];
  useEffect(() => {
    if (!waitingForSection || !syncedSessionId) return undefined;
    const base = trimTrailingSlash(apiBaseUrl);
    const timer = setInterval(async () => {
      try {
        const response = await fetch(`${base}/assessment/session/${encodeURIComponent(syncedSessionId)}`, {
          headers: buildApiHeaders(authState?.accessToken || ""),
        });
        if (response.ok) setAssessmentSession(await response.json());
      } catch {
        // Keep waiting; the next tick tries again.
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [waitingForSection, syncedSessionId, apiBaseUrl, authState?.accessToken]);

  function syncWorkflowState(patch) {
    onWorkflowStateChange?.((current) => ({
      ...current,
      ...patch,
    }));
  }

  function handleWorkflowReset(payload) {
    setAssessmentSession(null);
    setSelectedIds([]);
    setLastSubmission(null);
    setErrorMessage("");
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

  function syncProgress(payload) {
    syncWorkflowState({
      assessmentStatus: payload.status === "completed" ? "complete" : "active",
      assessmentAnsweredCount: payload.answered_count ?? 0,
      assessmentTotalQuestions: payload.total_questions ?? 0,
    });
  }

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
        // No question count: each attempt picks its own size (15-25).
        body: JSON.stringify({
          session_id: sessionId,
          role_key: roleKey || undefined,
          force_restart: forceRestart,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(resolveErrorMessage(payload, "Assessment start failed."));
      }

      setAssessmentSession(payload);
      setSelectedIds([]);
      syncWorkflowState({
        apiBaseUrl: baseUrl,
        sessionId,
        selectedRoleKey: payload.role_key,
        selectedRoleTitle: syncedRoleTitle || formatTitleFromKey(payload.role_key),
        resumeReady: true,
        roleReady: true,
      });
      syncProgress(payload);
    } catch (error) {
      setAssessmentSession(null);
      setErrorMessage(error.message);
    } finally {
      setBusyState("idle");
    }
  }

  async function submitCurrentAnswer() {
    if (!currentQuestion || !selectedIds.length) {
      setErrorMessage("Choose an answer before submitting.");
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
          selected_option_id: selectedIds[0],
          selected_option_ids: selectedIds,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(resolveErrorMessage(payload, "Answer submission failed."));
      }

      setAssessmentSession(payload);
      setLastSubmission(payload.submitted_result || null);
      setSelectedIds([]);
      syncProgress(payload);
    } catch (error) {
      setErrorMessage(error.message);
    } finally {
      setBusyState("idle");
    }
  }

  function toggleOption(optionId) {
    if (currentQuestion?.multi_select) {
      setSelectedIds((current) =>
        current.includes(optionId) ? current.filter((id) => id !== optionId) : [...current, optionId].sort(),
      );
    } else {
      setSelectedIds([optionId]);
    }
  }

  const answered = assessmentSession?.answered_count ?? 0;
  const total = assessmentSession?.total_questions ?? 0;
  const finished = assessmentSession?.status === "completed";
  // Caught up with the sections still being written: wait for the next one.
  const waiting = Boolean(assessmentSession) && !currentQuestion && !finished;
  const progressPct = total ? Math.round((answered / total) * 100) : 0;
  const sections = assessmentSession?.sections ?? [];

  if (!sessionId || !roleKey) {
    return (
      <div className="pp-page">
        <section className="interview-launch-card">
          <h2>Upload your resume first</h2>
          <p>The assessment is built for the role you choose, so start there.</p>
          <button className="primary-button" type="button" onClick={() => onNavigate?.("upload")}>
            Go to resume
          </button>
        </section>
      </div>
    );
  }

  const parts = currentQuestion ? splitQuestion(currentQuestion) : null;
  const multi = Boolean(currentQuestion?.multi_select);
  // Output-style answers ("1 2 3", multi-line prints) read best in monospace.
  const codeOptions = ["code_output", "pseudocode"].includes(currentQuestion?.question_type);

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
            {busyState === "starting" ? "Preparing a new test…" : "New test"}
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
              : `✗ Not quite - the answer was ${formatOptionIds(lastSubmission.correct_option_ids || [lastSubmission.correct_option_id])}`}
          </strong>
          {lastSubmission.explanation ? <p>{lastSubmission.explanation}</p> : null}
        </section>
      ) : null}

      {!assessmentSession ? (
        <section className="interview-launch-card">
          <h2>Online assessment</h2>
          <p>
            Between 15 and 25 questions, like a real company online test for {roleTitle || "your role"}: aptitude and
            reasoning, fundamentals, {roleTitle || "role"} technical questions and reading code. Mostly medium and
            hard, with single-answer, select-all, code-output and numerical questions.
          </p>
          <button className="primary-button" type="button" onClick={() => startAssessment()} disabled={busyState !== "idle"}>
            {busyState === "starting" ? "Preparing your test…" : "Start assessment"}
          </button>
          {busyState === "starting" ? (
            <p className="pp-assessment__wait">
              Writing and checking the first section for your role. This usually takes a few seconds; the rest is
              prepared while you answer.
            </p>
          ) : null}
        </section>
      ) : waiting ? (
        <section className="pp-card pp-assessment__done" aria-busy="true">
          <h2>Preparing the next section…</h2>
          <p>
            Your answers so far are saved. The rest of the test is being written and checked, and it will appear here in a
            moment.
          </p>
        </section>
      ) : finished ? (
        <section className="pp-card pp-assessment__done">
          <h2>
            {assessmentSession.correct_count ?? 0} of {total} correct
          </h2>
          <p>Next up is the live technical interview with Maya.</p>
          {sections.length > 1 ? (
            <table className="pp-assessment__sections">
              <thead>
                <tr>
                  <th scope="col">Section</th>
                  <th scope="col">Score</th>
                </tr>
              </thead>
              <tbody>
                {sections.map((section) => (
                  <tr key={section.title}>
                    <td>{section.title}</td>
                    <td>
                      {section.correct} / {section.total}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          <button className="primary-button" type="button" onClick={() => onNavigate?.("technical")}>
            Continue to technical interview →
          </button>
        </section>
      ) : (
        <section className="pp-card pp-question">
          <div className="pp-question__progress">
            {currentQuestion.section_title ? <span className="pp-question__section">{currentQuestion.section_title}</span> : null}
            <span>
              Question {currentQuestionIndex + 1} of {total}
            </span>
            <span className="pp-question__bar" aria-hidden="true">
              <i style={{ width: `${progressPct}%` }} />
            </span>
          </div>

          <p className="pp-question__prompt">{parts.text}</p>

          {parts.code ? (
            <figure className="pp-question__code">
              <figcaption>{languageLabel(parts.language)}</figcaption>
              <pre>
                <code>{parts.code}</code>
              </pre>
            </figure>
          ) : null}

          {multi ? <p className="pp-question__hint">Select all that apply</p> : null}

          <div className="assessment-option-list" role={multi ? "group" : "radiogroup"} aria-label="Answer options">
            {currentQuestion.options.map((option) => {
              const checked = selectedIds.includes(option.id);
              return (
                <label key={option.id} className={`assessment-option ${checked ? "assessment-option--selected" : ""}`}>
                  <input
                    className="assessment-option__input"
                    type={multi ? "checkbox" : "radio"}
                    name="assessment-option"
                    value={option.id}
                    checked={checked}
                    onChange={() => toggleOption(option.id)}
                  />
                  <span className="assessment-option__marker">{option.id.toUpperCase()}</span>
                  <span className={`assessment-option__text${codeOptions ? " assessment-option__text--code" : ""}`}>{option.text}</span>
                </label>
              );
            })}
          </div>

          <button
            className="primary-button pp-question__submit"
            type="button"
            onClick={submitCurrentAnswer}
            disabled={busyState !== "idle" || !selectedIds.length}
          >
            {busyState === "submitting" ? "Saving…" : "Submit answer"}
          </button>
        </section>
      )}
    </div>
  );
}
