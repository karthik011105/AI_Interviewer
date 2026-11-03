import { useEffect, useState } from "react";
import { Download } from "lucide-react";

import RoundStatusPage from "../components/RoundStatusPage";
import StartOverButton from "../components/StartOverButton";
import { buildApiHeaders } from "../lib/api";
import { buildWorkflowResetPatch } from "../lib/workflowReset";

const API_DEFAULT = "http://127.0.0.1:8000";
const REPORT_SECTION_ORDER = [
  { key: "assessment", label: "Assessment" },
  { key: "technical", label: "Technical" },
  { key: "dsa", label: "Coding" },
  { key: "project_discussion", label: "Projects" },
  { key: "hr", label: "HR" },
];

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

function trimSlash(value) {
  return String(value || "").replace(/\/+$/, "");
}

function coerceObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function coerceList(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item || "").trim()).filter(Boolean);
}

// Older saved reports carry placeholder lines ("Complete the X to add it to
// your report", "No completed stages ..."); the round cards already say that.
const PLACEHOLDER_PATTERNS = [
  /to add it to your report/i,
  /no completed stages/i,
  /finish more rounds to see/i,
  /no critical gaps/i,
  /to unlock/i,
];

function keepUseful(items) {
  return coerceList(items).filter((item) => !PLACEHOLDER_PATTERNS.some((pattern) => pattern.test(item)));
}

function toPct(value) {
  const numeric = Number(value);
  if (value === null || value === undefined || !Number.isFinite(numeric)) return null;
  return Math.round(numeric <= 1 ? numeric * 100 : numeric);
}

export default function ReportPage({ authState, workflowState, onNavigate, onWorkflowStateChange, roleRequiresDsa = true }) {
  const [reportEnvelope, setReportEnvelope] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const selectedRoleTitle = String(workflowState?.selectedRoleTitle || "").trim();
  const sessionId = String(workflowState?.sessionId || "").trim();
  const accessToken = String(authState?.accessToken || "").trim();
  const apiBaseUrl = String(workflowState?.apiBaseUrl || API_DEFAULT).trim();

  useEffect(() => {
    if (!sessionId || !accessToken) {
      setReportEnvelope(null);
      setErrorMessage("");
      return undefined;
    }

    let cancelled = false;

    async function loadReport() {
      setIsLoading(true);
      setErrorMessage("");
      try {
        const response = await fetch(`${trimSlash(apiBaseUrl)}/report/session/${encodeURIComponent(sessionId)}`, {
          headers: buildApiHeaders(accessToken),
        });
        const payload = await response.json().catch(() => null);
        if (!response.ok) {
          throw new Error(resolveErrorMessage(payload, "Could not load the report."));
        }
        if (!cancelled) setReportEnvelope(payload);
      } catch (error) {
        if (cancelled) return;
        setReportEnvelope(null);
        setErrorMessage(String(error?.message || "Could not load the report."));
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    loadReport();
    return () => {
      cancelled = true;
    };
  }, [accessToken, apiBaseUrl, sessionId]);

  // Save the latest report, then download it as a structured PDF.
  async function handleDownloadPdf() {
    if (!sessionId || !accessToken || isDownloading) {
      return;
    }
    setIsDownloading(true);
    setErrorMessage("");
    const base = `${trimSlash(apiBaseUrl)}/report/session/${encodeURIComponent(sessionId)}`;
    try {
      const saved = await fetch(`${base}/generate`, { method: "POST", headers: buildApiHeaders(accessToken) });
      const savedPayload = await saved.json().catch(() => null);
      if (saved.ok && savedPayload) setReportEnvelope(savedPayload);

      const response = await fetch(`${base}/pdf`, { headers: buildApiHeaders(accessToken) });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(resolveErrorMessage(payload, "Could not create the PDF."));
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `PrepForge-report-${(selectedRoleTitle || "interview").replace(/[^A-Za-z0-9]+/g, "-")}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (error) {
      setErrorMessage(String(error?.message || "Could not create the PDF."));
    } finally {
      setIsDownloading(false);
    }
  }

  if (!sessionId || !accessToken) {
    return (
      <RoundStatusPage
        kicker="Report"
        title="Upload your resume to start an interview."
        description="Your report fills in as you finish each round."
        statusLabel="No interview yet"
        statusTone="checking"
        cards={[]}
        primaryAction={{ label: "Upload resume", target: "upload" }}
        onNavigate={onNavigate}
      />
    );
  }

  const resolvedReport = reportEnvelope?.snapshot || reportEnvelope?.report || null;
  const reportJson = coerceObject(resolvedReport?.report_json);
  const dimensionScores = coerceObject(resolvedReport?.dimension_scores);
  const roundSummaryLookup = (Array.isArray(reportJson.round_summaries) ? reportJson.round_summaries : []).reduce(
    (lookup, entry) => {
      if (entry?.key) lookup[entry.key] = entry;
      return lookup;
    },
    {},
  );
  const sectionReports = REPORT_SECTION_ORDER
    .filter((entry) => roleRequiresDsa || entry.key !== "dsa")
    .map((entry) => {
      const section = coerceObject(reportJson[entry.key]);
      const fallbackSummary = coerceObject(roundSummaryLookup[entry.key]);
      return {
        key: entry.key,
        // Same names as the sidebar, whatever the saved report calls them.
        label: entry.label,
        status: String(section.status || fallbackSummary.status || "not_started"),
        score: section.score ?? fallbackSummary.score ?? null,
        summary: String(section.summary || fallbackSummary.summary || ""),
        strengths: keepUseful(section.strengths),
        risks: keepUseful(section.risks),
        nextSteps: keepUseful(section.recommendations),
      };
    });
  const withoutDsa = (items) => (roleRequiresDsa ? items : items.filter((item) => !item.includes("DSA")));
  const highlights = withoutDsa(keepUseful(reportJson.highlights));
  const recommendations = withoutDsa(keepUseful(reportJson.recommendations));
  const skillSummary = coerceObject(coerceObject(reportJson.technical).skill_profile_summary);
  const fallbackSkillSummary = coerceObject(coerceObject(dimensionScores.technical).skill_profile_summary);
  const missingSkills = coerceList(skillSummary.absent_skills ?? fallbackSkillSummary.absent_skills);

  const overallPct = toPct(resolvedReport?.overall_score);
  const doneCount = sectionReports.filter((s) => String(s.status).toLowerCase() === "complete").length;
  // Finished early with "Finish interview": no more rounds to start.
  const interviewEnded = Boolean(
    workflowState?.interviewEnd?.sessionId === sessionId && workflowState?.interviewEnd?.finished,
  );
  const statusOf = (status) => {
    const normalized = String(status || "").toLowerCase();
    if (normalized === "complete" || normalized === "completed") return { label: "Done", tone: "done" };
    if (normalized === "in_progress" || normalized === "active" || normalized === "partial") return { label: "In progress", tone: "progress" };
    return { label: interviewEnded ? "Not taken" : "Not started", tone: "idle" };
  };
  const ringStyle = { "--pct": overallPct ?? 0 };

  return (
    <div className="pp-page pp-report">
      <div className="pp-report__head">
        <div className="pp-page__head">
          <h1>Your interview report</h1>
          <p>{selectedRoleTitle || reportEnvelope?.role_selected || "Your chosen role"}</p>
        </div>
        <div className="pp-report__actions">
          <StartOverButton
            accessToken={accessToken}
            apiBaseUrl={apiBaseUrl}
            sessionId={sessionId}
            onRestarted={(payload) => {
              onWorkflowStateChange?.((current) => ({
                ...current,
                ...buildWorkflowResetPatch(current, payload?.cleared_targets),
              }));
              onNavigate?.("assessment");
            }}
          />
          <button
            type="button"
            className="primary-button pp-report__download"
            onClick={handleDownloadPdf}
            disabled={isDownloading || isLoading || doneCount === 0}
            title={doneCount === 0 ? "Finish a round first" : undefined}
          >
            <Download size={17} aria-hidden="true" />
            {isDownloading ? "Preparing PDF…" : "Download PDF"}
          </button>
        </div>
      </div>

      {errorMessage ? <p className="error-banner">{errorMessage}</p> : null}
      {interviewEnded ? (
        <p className="info-banner">You finished this interview early. The report covers the rounds you completed.</p>
      ) : null}

      <section className="pp-card pp-report__hero">
        <div className="pp-ring" style={ringStyle} aria-label={overallPct === null ? "No score yet" : `Overall score ${overallPct} percent`}>
          <span>{overallPct === null ? "–" : overallPct}</span>
          <small>{overallPct === null ? "no score yet" : "overall"}</small>
        </div>
        <div className="pp-report__hero-text">
          <h2>
            {doneCount === 0
              ? interviewEnded
                ? "No rounds were completed before the interview ended"
                : "Complete a round to see your score"
              : doneCount === sectionReports.length
                ? "All rounds complete"
                : `${doneCount} of ${sectionReports.length} rounds complete`}
          </h2>
          <div className="pp-report__progress" aria-hidden="true">
            {sectionReports.map((section) => (
              <i key={section.key} className={`pp-report__seg pp-report__seg--${statusOf(section.status).tone}`} />
            ))}
          </div>
          <p>{isLoading ? "Loading your results…" : interviewEnded ? "This interview has ended." : "Scores update as you finish each round."}</p>
        </div>
      </section>

      <section className="pp-round-grid" aria-label="Rounds">
        {sectionReports.map((section) => {
          const status = statusOf(section.status);
          const pct = toPct(section.score);
          const hasDetail = section.strengths.length || section.risks.length || section.nextSteps.length;
          return (
            <article key={section.key} className={`pp-round pp-round--${status.tone}`}>
              <div className="pp-round__top">
                <h3>{section.label}</h3>
                <span className={`pp-chip pp-chip--${status.tone}`}>{status.label}</span>
              </div>
              <p className="pp-round__score">
                {status.tone === "idle" || pct === null ? <span className="pp-round__dash">–</span> : <>{pct}<small>%</small></>}
              </p>
              {status.tone !== "idle" && section.summary ? <p className="pp-round__summary">{section.summary}</p> : null}
              {status.tone === "idle" && !interviewEnded ? (
                <button type="button" className="secondary-button" onClick={() => onNavigate?.(section.key)}>
                  Start →
                </button>
              ) : hasDetail && status.tone !== "idle" ? (
                <details className="pp-round__details">
                  <summary>Details</summary>
                  {section.strengths.length ? (
                    <div><h4>Strengths</h4><ul>{section.strengths.map((item) => <li key={item}>{item}</li>)}</ul></div>
                  ) : null}
                  {section.risks.length ? (
                    <div><h4>Work on</h4><ul>{section.risks.map((item) => <li key={item}>{item}</li>)}</ul></div>
                  ) : null}
                  {section.nextSteps.length ? (
                    <div><h4>Try next</h4><ul>{section.nextSteps.map((item) => <li key={item}>{item}</li>)}</ul></div>
                  ) : null}
                </details>
              ) : null}
            </article>
          );
        })}
      </section>

      {doneCount > 0 && (highlights.length || recommendations.length) ? (
        <section className="pp-report__lists">
          {highlights.length ? (
            <div className="pp-card">
              <h3>Going well</h3>
              <ul>{highlights.map((item) => <li key={item}>{item}</li>)}</ul>
            </div>
          ) : null}
          {recommendations.length ? (
            <div className="pp-card">
              <h3>Focus next</h3>
              <ul>{recommendations.map((item) => <li key={item}>{item}</li>)}</ul>
            </div>
          ) : null}
        </section>
      ) : null}

      {missingSkills.length ? (
        <section className="pp-card pp-report__skills">
          <h3>Skills to study for this role</h3>
          <div className="pp-skill-chips">
            {missingSkills.map((skill) => <span key={skill}>{skill}</span>)}
          </div>
        </section>
      ) : null}
    </div>
  );
}
