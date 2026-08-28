import { useEffect, useMemo, useState } from "react";

import CodeEditor from "../components/CodeEditor";
import TestResults from "../components/TestResults";
import TimerBar from "../components/TimerBar";
import WorkflowResetControl from "../components/WorkflowResetControl";
import { buildApiHeaders } from "../lib/api";
import { buildWorkflowResetPatch } from "../lib/workflowReset";

const API_DEFAULT = "http://127.0.0.1:8000";

const DSA_STEPS = [
  { key: "problem_setup", label: "Problem Setup", meta: "Clarify the prompt." },
  { key: "approach_discussion", label: "Approach", meta: "Persist your reasoning." },
  { key: "coding", label: "Coding", meta: "Run and submit through Judge0." },
  { key: "optimization", label: "Optimization", meta: "One improvement pass." },
  { key: "debrief", label: "Debrief", meta: "Explain what happened." },
  { key: "complete", label: "Complete", meta: "Scoring and reporting." },
];

const TOTAL_DSA_QUESTIONS = 2;

const DEFAULT_DSA_LANGUAGES = [
  { key: "python", label: "Python 3", file_name: "solve.py" },
];

function normalizeQuestionNumber(value) {
  return Number(value) === 2 ? 2 : 1;
}

function normalizeLanguageKey(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (["cpp", "c++", "cplusplus", "cxx"].includes(normalized)) {
    return "cpp";
  }
  if (["java", "jav"].includes(normalized)) {
    return "java";
  }
  return "python";
}

function normalizeSupportedLanguages(value) {
  if (!Array.isArray(value) || !value.length) {
    return DEFAULT_DSA_LANGUAGES;
  }

  const normalized = value
    .map((item) => {
      const key = normalizeLanguageKey(item?.key);
      const fallback = DEFAULT_DSA_LANGUAGES.find((language) => language.key === key);
      return {
        key,
        label: String(item?.label || fallback?.label || key),
        file_name: String(item?.file_name || fallback?.file_name || "solve.py"),
      };
    })
    .filter((item, index, collection) => collection.findIndex((candidate) => candidate.key === item.key) === index);

  return normalized.length ? normalized : DEFAULT_DSA_LANGUAGES;
}

function buildLanguageDrafts(sessionPayload, supportedLanguages) {
  const fallbackLanguage = supportedLanguages[0]?.key || "python";
  const currentLanguage = normalizeLanguageKey(sessionPayload?.language || sessionPayload?.state_json?.current_language || fallbackLanguage);
  const rawStarterCodes = sessionPayload?.problem?.starter_codes;
  const starterCodes = rawStarterCodes && typeof rawStarterCodes === "object" ? rawStarterCodes : {};
  const rawDrafts = sessionPayload?.state_json?.code_drafts;
  const persistedDrafts = rawDrafts && typeof rawDrafts === "object" ? rawDrafts : {};
  const activeFallbackDraft = String(sessionPayload?.current_code_draft || sessionPayload?.problem?.starter_code || "");
  const languageDrafts = {};

  supportedLanguages.forEach(({ key }) => {
    languageDrafts[key] = String(persistedDrafts[key] || starterCodes[key] || (key === currentLanguage ? activeFallbackDraft : ""));
  });

  if (!languageDrafts[currentLanguage]) {
    languageDrafts[currentLanguage] = activeFallbackDraft;
  }

  return { currentLanguage, languageDrafts };
}

function hasDebriefMessages(sessionPayload) {
  const debriefMessages = sessionPayload?.state_json?.debrief_messages;
  return Array.isArray(debriefMessages) && debriefMessages.length > 0;
}

function trimTrailingSlash(value) {
  return String(value || "").replace(/\/+$/, "");
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

function formatRoleTitle(value) {
  return String(value || "")
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatStageTitle(stage) {
  const matching = DSA_STEPS.find((step) => step.key === stage);
  return matching?.label || formatRoleTitle(stage);
}

function getQuestionProgress(progressSummary, questionNumber) {
  const questions = Array.isArray(progressSummary?.questions) ? progressSummary.questions : [];
  return questions.find((question) => Number(question?.question_number) === questionNumber) || null;
}

function buildMessageOptions(stage) {
  if (stage === "problem_setup") {
    return [
      { value: "clarification", label: "Clarification" },
      { value: "approach", label: "Approach note" },
    ];
  }
  if (stage === "debrief" || stage === "complete") {
    return [{ value: "debrief", label: "Debrief note" }];
  }
  return [{ value: "approach", label: "Approach note" }];
}

export default function DSAPage({ authState, workflowState, onNavigate, onWorkflowStateChange, roleRequiresDsa = true }) {
  const [apiBaseUrl, setApiBaseUrl] = useState(workflowState?.apiBaseUrl || API_DEFAULT);
  const [busyState, setBusyState] = useState("idle");
  const [errorMessage, setErrorMessage] = useState("");
  const [infoMessage, setInfoMessage] = useState("Open the DSA round and load the current coding session.");
  const [dsaSession, setDsaSession] = useState(null);
  const [selectedLanguage, setSelectedLanguage] = useState("python");
  const [languageDrafts, setLanguageDrafts] = useState({});
  const [codeDraft, setCodeDraft] = useState("");
  const [messageDraft, setMessageDraft] = useState("");
  const [messageKind, setMessageKind] = useState("approach");
  const [latestExecution, setLatestExecution] = useState(null);

  const sessionId = String(workflowState?.sessionId || "").trim();
  const roleKey = String(workflowState?.selectedRoleKey || "").trim();
  const accessToken = String(authState?.accessToken || "").trim();
  const dsaViewed = Boolean(workflowState?.dsaPreviewViewed);
  const currentQuestionNumber = normalizeQuestionNumber(workflowState?.dsaCurrentQuestionNumber || dsaSession?.question_number);
  const baseUrl = trimTrailingSlash(apiBaseUrl || API_DEFAULT);
  const stageKey = dsaSession?.stage || "problem_setup";
  const supportedLanguages = useMemo(() => normalizeSupportedLanguages(dsaSession?.supported_languages), [dsaSession?.supported_languages]);
  const currentLanguageMeta = supportedLanguages.find((language) => language.key === selectedLanguage) || supportedLanguages[0] || DEFAULT_DSA_LANGUAGES[0];
  const progressSummary = dsaSession?.progress_summary || null;
  const questionOneProgress = getQuestionProgress(progressSummary, 1);
  const dsaRoundCompleted = Boolean(progressSummary?.round_completed ?? workflowState?.dsaRoundCompleted);
  const questionLabel = `Question ${currentQuestionNumber} of ${TOTAL_DSA_QUESTIONS}`;
  const questionStartedAt = dsaSession?.state_json?.question_started_at || dsaSession?.state_json?.stage_started_at || null;
  const questionTimingStopped = stageKey === "debrief" || stageKey === "complete";
  const currentQuestionHasDebrief = hasDebriefMessages(dsaSession);
  const currentQuestionReadyForNext = currentQuestionNumber === 1 && (stageKey === "debrief" || stageKey === "complete" || currentQuestionHasDebrief);
  const questionTwoUnlocked = currentQuestionNumber === 2
    || dsaRoundCompleted
    || Boolean(questionOneProgress?.hidden_tests_passed)
    || Boolean(questionOneProgress?.completed)
    || currentQuestionReadyForNext;
  const messageOptions = useMemo(() => buildMessageOptions(stageKey), [stageKey]);
  const canStart = Boolean(roleRequiresDsa && sessionId && accessToken && !dsaSession);
  const canRunOrSubmit = Boolean(dsaSession && codeDraft.trim() && accessToken && sessionId);
  const canSendMessage = Boolean(dsaSession && stageKey === "debrief" && messageDraft.trim() && accessToken && sessionId);

  useEffect(() => {
    setApiBaseUrl(workflowState?.apiBaseUrl || API_DEFAULT);
  }, [workflowState?.apiBaseUrl]);

  useEffect(() => {
    if (!roleRequiresDsa || dsaViewed) {
      return;
    }
    onWorkflowStateChange?.((current) => ({
      ...current,
      dsaPreviewViewed: true,
    }));
  }, [dsaViewed, onWorkflowStateChange, roleRequiresDsa]);

  useEffect(() => {
    if (!messageOptions.some((option) => option.value === messageKind)) {
      setMessageKind(messageOptions[0]?.value || "approach");
    }
  }, [messageKind, messageOptions]);

  useEffect(() => {
    if (!roleRequiresDsa || !sessionId || !accessToken) {
      setDsaSession(null);
      setSelectedLanguage("python");
      setLanguageDrafts({});
      setCodeDraft("");
      setLatestExecution(null);
      return;
    }

    let active = true;

    async function loadSession() {
      setBusyState((current) => (current === "idle" ? "loading" : current));
      try {
        const primaryResponse = await fetch(`${baseUrl}/dsa/session/${encodeURIComponent(sessionId)}/${currentQuestionNumber}`, {
          headers: buildApiHeaders(accessToken),
        });
        let response = primaryResponse;
        let payload = await response.json().catch(() => ({}));
        if (!active) {
          return;
        }
        if (response.status === 404 && currentQuestionNumber === 2) {
          const fallbackResponse = await fetch(`${baseUrl}/dsa/session/${encodeURIComponent(sessionId)}/1`, {
            headers: buildApiHeaders(accessToken),
          });
          response = fallbackResponse;
          payload = await response.json().catch(() => ({}));
        }
        if (response.status === 404) {
          setDsaSession(null);
          setCodeDraft("");
          setLatestExecution(null);
          setInfoMessage(`Start question ${currentQuestionNumber} to load the coding workspace.`);
          return;
        }
        if (!response.ok) {
          throw new Error(resolveErrorMessage(payload, "Could not load the DSA session."));
        }
        applyDsaSessionPayload(payload, {
          infoMessage: payload.stage === "complete"
            ? `Question ${normalizeQuestionNumber(payload.question_number)} finalized.`
            : payload.stage === "debrief"
            ? currentQuestionNumber === TOTAL_DSA_QUESTIONS && hasDebriefMessages(payload)
              ? "Question 2 debrief saved. DSA round complete."
              : normalizeQuestionNumber(payload.question_number) === 1
              ? "Question 1 hidden tests passed. You can open question 2 now or save the debrief first."
              : `Question ${normalizeQuestionNumber(payload.question_number)} is in debrief. Finish the note to complete the round.`
            : `Question ${normalizeQuestionNumber(payload.question_number)} restored.`,
        });
      } catch (error) {
        if (active) {
          setErrorMessage(error.message);
        }
      } finally {
        if (active) {
          setBusyState("idle");
        }
      }
    }

    loadSession();
    return () => {
      active = false;
    };
  }, [accessToken, baseUrl, currentQuestionNumber, roleRequiresDsa, sessionId]);

  function syncWorkflowState(patch) {
    onWorkflowStateChange?.((current) => ({
      ...current,
      ...patch,
    }));
  }

  function syncDsaWorkflow(patch) {
    syncWorkflowState({
      apiBaseUrl: baseUrl,
      dsaPreviewViewed: patch?.dsaPreviewViewed ?? true,
      dsaCurrentQuestionNumber: normalizeQuestionNumber(patch?.dsaCurrentQuestionNumber ?? currentQuestionNumber),
      dsaRoundCompleted: Boolean(patch?.dsaRoundCompleted ?? workflowState?.dsaRoundCompleted),
    });
  }

  function handleWorkflowReset(payload, { successMessage } = {}) {
    setDsaSession(null);
    setSelectedLanguage("python");
    setLanguageDrafts({});
    setCodeDraft("");
    setMessageDraft("");
    setLatestExecution(null);
    setErrorMessage("");
    setInfoMessage(successMessage || "DSA round reset.");
    onWorkflowStateChange?.((current) => ({
      ...current,
      ...buildWorkflowResetPatch(current, payload?.cleared_targets),
    }));
  }

  function applyDsaSessionPayload(payload, { infoMessage: nextInfoMessage, dsaRoundCompletedOverride } = {}) {
    const resolvedQuestionNumber = normalizeQuestionNumber(payload?.question_number || currentQuestionNumber);
    const resolvedRoundCompleted = typeof dsaRoundCompletedOverride === "boolean"
      ? dsaRoundCompletedOverride
      : Boolean(payload?.progress_summary?.round_completed ?? workflowState?.dsaRoundCompleted);
    const resolvedSupportedLanguages = normalizeSupportedLanguages(payload?.supported_languages);
    const { currentLanguage, languageDrafts: nextLanguageDrafts } = buildLanguageDrafts(payload, resolvedSupportedLanguages);

    setDsaSession(payload);
    setSelectedLanguage(currentLanguage);
    setLanguageDrafts(nextLanguageDrafts);
    setCodeDraft(nextLanguageDrafts[currentLanguage] || payload.current_code_draft || payload.problem?.starter_code || "");
    setLatestExecution(payload.execution_results || null);
    if (typeof nextInfoMessage === "string" && nextInfoMessage) {
      setInfoMessage(nextInfoMessage);
    }
    syncDsaWorkflow({
      dsaCurrentQuestionNumber: resolvedQuestionNumber,
      dsaRoundCompleted: resolvedRoundCompleted,
    });
  }

  async function startSession(questionNumber = currentQuestionNumber) {
    if (!sessionId) {
      setErrorMessage("Start from Resume Intake first.");
      return;
    }
    setBusyState("starting");
    setErrorMessage("");
    try {
      const response = await fetch(`${baseUrl}/dsa/start`, {
        method: "POST",
        headers: buildApiHeaders(accessToken, { "Content-Type": "application/json" }),
        body: JSON.stringify({
          session_id: sessionId,
          question_number: questionNumber,
          role_key: roleKey || undefined,
          language: selectedLanguage,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(resolveErrorMessage(payload, "Could not start the DSA round."));
      }
      applyDsaSessionPayload(payload, {
        infoMessage: payload.created_dsa_session ? `Question ${questionNumber} loaded.` : `Question ${questionNumber} restored.`,
        dsaRoundCompletedOverride: questionNumber === 1 && payload.created_dsa_session ? false : undefined,
      });
    } catch (error) {
      setErrorMessage(error.message);
    } finally {
      setBusyState("idle");
    }
  }

  async function runCode() {
    setBusyState("running");
    setErrorMessage("");
    try {
      const response = await fetch(`${baseUrl}/dsa/run`, {
        method: "POST",
        headers: buildApiHeaders(accessToken, { "Content-Type": "application/json" }),
        body: JSON.stringify({ session_id: sessionId, question_number: currentQuestionNumber, code: codeDraft, language: selectedLanguage }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(resolveErrorMessage(payload, "Could not run the sample tests."));
      }
      applyDsaSessionPayload(payload, { infoMessage: `Question ${currentQuestionNumber} sample run persisted.` });
    } catch (error) {
      setErrorMessage(error.message);
    } finally {
      setBusyState("idle");
    }
  }

  async function submitCode() {
    setBusyState("submitting");
    setErrorMessage("");
    try {
      const response = await fetch(`${baseUrl}/dsa/submit`, {
        method: "POST",
        headers: buildApiHeaders(accessToken, { "Content-Type": "application/json" }),
        body: JSON.stringify({ session_id: sessionId, question_number: currentQuestionNumber, code: codeDraft, language: selectedLanguage }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(resolveErrorMessage(payload, "Could not submit the hidden tests."));
      }
      const coachingMessage = payload.coach_message || payload.coaching_note;
      applyDsaSessionPayload(payload, {
        infoMessage: payload.next_stage === "optimization"
          ? coachingMessage || `Question ${currentQuestionNumber} passed hidden tests, but the solution still looks brute-force. Use the optimization pass before debrief.`
          : payload.next_stage === "complete"
          ? currentQuestionNumber === TOTAL_DSA_QUESTIONS
            ? "Question 2 passed hidden tests. DSA round complete."
            : `Question ${currentQuestionNumber} finalized.`
          : payload.next_stage === "debrief"
          ? coachingMessage || (currentQuestionNumber === 1
            ? "Question 1 passed hidden tests. You can open question 2 now or save the debrief first."
            : `Question ${currentQuestionNumber} passed hidden tests. Add a debrief note to complete the round.`)
          : coachingMessage || `Question ${currentQuestionNumber} submission saved. Keep iterating in coding stage.`,
        dsaRoundCompletedOverride: payload.next_stage === "complete" && currentQuestionNumber === TOTAL_DSA_QUESTIONS ? true : undefined,
      });
    } catch (error) {
      setErrorMessage(error.message);
    } finally {
      setBusyState("idle");
    }
  }

  async function sendMessage() {
    setBusyState("messaging");
    setErrorMessage("");
    try {
      const response = await fetch(`${baseUrl}/dsa/message`, {
        method: "POST",
        headers: buildApiHeaders(accessToken, { "Content-Type": "application/json" }),
        body: JSON.stringify({
          session_id: sessionId,
          question_number: currentQuestionNumber,
          message: messageDraft,
          message_kind: messageKind,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(resolveErrorMessage(payload, "Could not save the DSA note."));
      }
      const savedDebrief = payload.message_channel === "debrief" && hasDebriefMessages(payload);
      const coachingMessage = payload.coach_message || payload.coaching_note;
      applyDsaSessionPayload(payload, {
        infoMessage: payload.message_channel === "debrief"
          ? currentQuestionNumber === TOTAL_DSA_QUESTIONS
            ? "DSA round complete. Project Discussion is unlocked."
            : "Debrief saved. Load question 2 when you are ready."
          : coachingMessage || `Question ${currentQuestionNumber} note saved.`,
        dsaRoundCompletedOverride: savedDebrief && currentQuestionNumber === TOTAL_DSA_QUESTIONS,
      });
      setMessageDraft("");
    } catch (error) {
      setErrorMessage(error.message);
    } finally {
      setBusyState("idle");
    }
  }

  function handleCodeChange(nextCode) {
    setCodeDraft(nextCode);
    setLanguageDrafts((current) => ({
      ...current,
      [selectedLanguage]: nextCode,
    }));
  }

  function handleLanguageChange(event) {
    const nextLanguage = normalizeLanguageKey(event.target.value);
    const nextDrafts = {
      ...languageDrafts,
      [selectedLanguage]: codeDraft,
    };
    const nextDraft = String(nextDrafts[nextLanguage] || dsaSession?.problem?.starter_codes?.[nextLanguage] || "");

    nextDrafts[nextLanguage] = nextDraft;
    setSelectedLanguage(nextLanguage);
    setLanguageDrafts(nextDrafts);
    setCodeDraft(nextDraft);
  }

  if (!roleRequiresDsa) {
    return (
      <div className="page-shell dsa-workspace-shell">
        <section className="glass-panel dsa-surface-card dsa-surface-card--centered">
          <p className="section-kicker">DSA skipped</p>
          <h1>This role does not require the DSA round.</h1>
          <p className="hero-text">This path moves directly from Technical to Project Discussion.</p>
          <div className="action-row">
            <button type="button" className="primary-button action-row__button" onClick={() => onNavigate?.("project_discussion")}>
              Open Project Discussion
            </button>
          </div>
        </section>
      </div>
    );
  }

  if (!accessToken) {
    return (
      <div className="page-shell dsa-workspace-shell">
        <section className="glass-panel dsa-surface-card dsa-surface-card--centered">
          <p className="section-kicker">Authentication required</p>
          <h1>Sign in to run the DSA round.</h1>
          <p className="hero-text">The backend routes for start, run, submit, and debrief are tied to the owned interview session.</p>
        </section>
      </div>
    );
  }

  return (
    <div className="page-shell dsa-workspace-shell">
      <div className="message-stack">
        {infoMessage ? <p className="info-banner">{infoMessage}</p> : null}
        {errorMessage ? <p className="error-banner">{errorMessage}</p> : null}
      </div>

      <div className="action-row">
        <button
          type="button"
          className="secondary-button secondary-button--inline"
          disabled={busyState !== "idle" || currentQuestionNumber === 1}
          onClick={() => startSession(1)}
        >
          {currentQuestionNumber === 1 ? "Viewing question 1" : "Open question 1"}
        </button>
        <button
          type="button"
          className="secondary-button secondary-button--inline"
          disabled={busyState !== "idle" || !questionTwoUnlocked}
          onClick={() => startSession(2)}
        >
          {currentQuestionNumber === 2 ? "Viewing question 2" : "Open question 2"}
        </button>
        <WorkflowResetControl
          accessToken={accessToken}
          apiBaseUrl={baseUrl}
          sessionId={sessionId}
          currentTarget="dsa"
          currentLabel="DSA round"
          onResetApplied={handleWorkflowReset}
          triggerClassName="secondary-button secondary-button--inline"
          triggerLabel="Reset"
          disabled={busyState !== "idle"}
        />
      </div>

      <section className="dsa-workspace-grid">
        <section className="glass-panel dsa-surface-card dsa-problem-card dsa-problem-card--workspace">
          <div className="dsa-problem-card__body">
            <>
              <div className="panel-head panel-head--tight">
                <div>
                  <p className="section-kicker">{questionLabel}</p>
                  <h2>{dsaSession?.problem?.title || "Run the coding workspace"}</h2>
                </div>
              </div>
              <div className="dsa-problem-statement">
                <p className="dsa-problem-statement__copy">
                  {dsaSession?.problem?.statement || "Start the round to load the full description, constraints, and examples."}
                </p>
              </div>
              {dsaSession?.problem?.constraints?.length ? (
                <ul className="dsa-problem-list">
                  {dsaSession.problem.constraints.map((constraint) => (
                    <li key={constraint}>{constraint}</li>
                  ))}
                </ul>
              ) : <p className="empty-state">Constraints will appear once the session is started.</p>}
              {dsaSession?.problem?.examples?.length ? (
                <div className="dsa-example-stack">
                  {dsaSession.problem.examples.map((example, index) => (
                    <article key={`${example.input_text}-${index}`} className="dsa-example-card">
                      <strong>Example {index + 1}</strong>
                      <pre>{example.input_text}</pre>
                      <pre>{example.output_text}</pre>
                      {example.explanation ? <p>{example.explanation}</p> : null}
                    </article>
                  ))}
                </div>
              ) : null}

              {stageKey === "debrief" ? (
              <section className="dsa-inline-panel">
                <div className="panel-head panel-head--tight">
                  <div>
                    <p className="section-kicker">Debrief</p>
                    <h2>Save the final note for this question</h2>
                  </div>
                </div>
                <div className="dsa-message-card__controls">
                  <textarea
                    className="dsa-message-card__textarea"
                    value={messageDraft}
                    onChange={(event) => setMessageDraft(event.target.value)}
                    placeholder="What worked, what failed, and what you would improve."
                    disabled={!dsaSession}
                  />
                </div>
                <button type="button" className="primary-button action-row__button" disabled={!canSendMessage || busyState !== "idle"} onClick={sendMessage}>
                  {busyState === "messaging" ? "Saving..." : "Save debrief"}
                </button>
              </section>
            ) : null}
            </>
          </div>
        </section>

        <section className="dsa-workspace-column dsa-workspace-column--editor">
          <TimerBar
            deadlineAt={questionTimingStopped ? null : dsaSession?.deadline_at}
            stageStartedAt={questionStartedAt}
            stageLabel={`${questionLabel} timer`}
            inactiveLabel={questionTimingStopped ? "Stopped" : "Not armed"}
            inactiveNote={questionTimingStopped ? "This question is no longer timed." : undefined}
          />
          <CodeEditor
            value={codeDraft}
            onChange={handleCodeChange}
            statusLabel={busyState === "idle" ? "Code editor" : busyState}
            fileName={currentLanguageMeta?.file_name || "solve.py"}
            languageKey={selectedLanguage}
            languageLabel={currentLanguageMeta?.label || "Python 3"}
            height="68vh"
            placeholder={selectedLanguage === "cpp"
              ? "Implement solve() using cin and cout. Do not declare main()."
              : selectedLanguage === "java"
              ? "Implement static solve() using System.in/System.out. Do not declare Main or main()."
              : "Write your solve() implementation here."}
            wrapLongLines={false}
            headerActions={(
              <select
                className="dsa-editor__language-select"
                aria-label="Programming language"
                value={selectedLanguage}
                onChange={handleLanguageChange}
                disabled={busyState !== "idle"}
              >
                {supportedLanguages.map((language) => (
                  <option key={language.key} value={language.key}>{language.label}</option>
                ))}
              </select>
            )}
            readOnly={!dsaSession}
          />
          <div className="action-row">
            <button type="button" className="primary-button action-row__button" disabled={!canStart || busyState !== "idle"} onClick={() => startSession()}>
              {dsaSession ? `Question ${currentQuestionNumber} loaded` : busyState === "starting" ? "Starting..." : `Start question ${currentQuestionNumber}`}
            </button>
            <button type="button" className="secondary-button action-row__button" disabled={!canRunOrSubmit || busyState !== "idle"} onClick={runCode}>
              {busyState === "running" ? "Running..." : "Run sample tests"}
            </button>
            <button type="button" className="secondary-button action-row__button" disabled={!canRunOrSubmit || busyState !== "idle"} onClick={submitCode}>
              {busyState === "submitting" ? "Submitting..." : "Submit hidden tests"}
            </button>
            {currentQuestionNumber === 1 ? (
              <button
                type="button"
                className="secondary-button action-row__button"
                disabled={!questionTwoUnlocked || busyState !== "idle"}
                onClick={() => startSession(2)}
              >
                {busyState === "starting" ? "Loading..." : "Open question 2"}
              </button>
            ) : null}
            {currentQuestionNumber === 2 && dsaRoundCompleted ? (
              <button
                type="button"
                className="secondary-button action-row__button"
                onClick={() => onNavigate?.("project_discussion")}
              >
                Open Project Discussion
              </button>
            ) : null}
          </div>
          <TestResults executionResults={latestExecution || dsaSession?.execution_results} title="Test results" />
        </section>
      </section>
    </div>
  );
}
