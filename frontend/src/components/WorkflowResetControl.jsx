import { useEffect, useState } from "react";

import { buildApiHeaders } from "../lib/api";
import {
  buildResetSuccessMessage,
  describeEntireInterviewReset,
  describeScopedReset,
} from "../lib/workflowReset";

const API_DEFAULT = "http://127.0.0.1:8000";

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

export default function WorkflowResetControl({
  accessToken,
  apiBaseUrl,
  sessionId,
  currentTarget,
  currentLabel,
  onResetApplied,
  triggerClassName = "secondary-button",
  triggerLabel = "Reset",
  disabled = false,
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [busyTarget, setBusyTarget] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [toastState, setToastState] = useState(null);

  const baseUrl = trimTrailingSlash(apiBaseUrl || API_DEFAULT);
  const scopedDescription = describeScopedReset(currentTarget, currentLabel);
  const isBusy = busyTarget !== "";
  const canTrigger = Boolean(sessionId && accessToken && currentTarget) && !disabled;

  useEffect(() => {
    if (!toastState?.id) {
      return undefined;
    }

    const timeoutId = window.setTimeout(() => {
      setToastState(null);
    }, 3200);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [toastState]);

  async function handleReset(target) {
    setBusyTarget(target);
    setErrorMessage("");

    try {
      const response = await fetch(`${baseUrl}/workflow/reset`, {
        method: "POST",
        headers: buildApiHeaders(accessToken, {
          "Content-Type": "application/json",
        }),
        body: JSON.stringify({
          session_id: sessionId,
          target,
        }),
      });

      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(resolveErrorMessage(payload, "Could not reset the workflow state."));
      }

      const successMessage = buildResetSuccessMessage(payload, currentLabel);
      setIsOpen(false);
      setToastState({
        id: Date.now(),
        message: successMessage,
      });
      onResetApplied?.(payload, {
        successMessage,
      });
    } catch (error) {
      setErrorMessage(error.message || "Could not reset the workflow state.");
    } finally {
      setBusyTarget("");
    }
  }

  return (
    <>
      <button
        type="button"
        className={triggerClassName}
        onClick={() => {
          setErrorMessage("");
          setIsOpen(true);
        }}
        disabled={!canTrigger || isBusy}
      >
        {isBusy ? "Resetting..." : triggerLabel}
      </button>

      {isOpen ? (
        <div className="workflow-reset-overlay" role="dialog" aria-modal="true" aria-label="Reset workflow">
          <div className="glass-panel workflow-reset-dialog" onClick={(event) => event.stopPropagation()}>
            <div className="panel-head panel-head--tight">
              <div>
                <p className="section-kicker">Reset interview state</p>
                <h2>Choose the reset scope.</h2>
              </div>
            </div>

            <p className="workflow-reset-dialog__lead">
              Pick whether to clear only this stage or the whole interview workflow for the current session.
            </p>

            <div className="workflow-reset-dialog__options">
              <button
                type="button"
                className="workflow-reset-dialog__option"
                onClick={() => handleReset(currentTarget)}
                disabled={isBusy}
              >
                <span>Reset this stage</span>
                <strong>{currentLabel}</strong>
                <p>{scopedDescription}</p>
              </button>

              <button
                type="button"
                className="workflow-reset-dialog__option workflow-reset-dialog__option--danger"
                onClick={() => handleReset("entire_interview")}
                disabled={isBusy}
              >
                <span>Reset entire interview</span>
                <strong>Assessment through report</strong>
                <p>{describeEntireInterviewReset()}</p>
              </button>
            </div>

            {errorMessage ? <p className="error-banner">{errorMessage}</p> : null}

            <div className="action-row workflow-reset-dialog__actions">
              <button
                type="button"
                className="secondary-button action-row__button"
                onClick={() => setIsOpen(false)}
                disabled={isBusy}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {toastState?.message ? (
        <div className="workflow-reset-toast" role="status" aria-live="polite">
          <strong>Reset complete</strong>
          <p>{toastState.message}</p>
        </div>
      ) : null}
    </>
  );
}