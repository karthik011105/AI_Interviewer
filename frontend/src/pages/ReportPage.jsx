import { useEffect, useState } from "react";

import RoundStatusPage from "../components/RoundStatusPage";
import WorkflowResetControl from "../components/WorkflowResetControl";
import { buildApiHeaders } from "../lib/api";
import { buildWorkflowResetPatch } from "../lib/workflowReset";

const API_DEFAULT = "http://127.0.0.1:8000";
const INTERVIEW_NLP_ROUNDS = [
  { key: "technical", label: "Technical Interview" },
  { key: "project_discussion", label: "Project Discussion" },
  { key: "hr", label: "HR Interview" },
];
const REPORT_SECTION_ORDER = [
  { key: "assessment", label: "Assessment", kicker: "Screening" },
  { key: "technical", label: "Technical Interview", kicker: "Technical" },
  { key: "dsa", label: "DSA Round", kicker: "Coding" },
  { key: "project_discussion", label: "Project Discussion", kicker: "Projects" },
  { key: "hr", label: "HR Interview", kicker: "Behavioral" },
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

function formatPercent(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "Pending";
  }
  return `${Math.round(numeric * 100)}%`;
}

function formatStatus(status) {
  const normalized = String(status || "not_started").trim().toLowerCase();
  if (normalized === "complete") {
    return "Complete";
  }
  if (normalized === "in_progress") {
    return "In progress";
  }
  return "Not started";
}

function filterDsaCopy(items, roleRequiresDsa) {
  if (roleRequiresDsa) {
    return items;
  }
  return items.filter((item) => !String(item || "").includes("DSA"));
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

function pickConceptCoverage(reportJson, dimensionScores, roundKey) {
  const reportSection = coerceObject(reportJson?.[roundKey]);
  const reportCoverage = coerceObject(reportSection?.concept_coverage);
  if (Object.keys(reportCoverage).length) {
    return reportCoverage;
  }
  const dimensionSection = coerceObject(dimensionScores?.[roundKey]);
  return coerceObject(dimensionSection?.concept_coverage);
}

function pickConfidenceSignal(reportJson, dimensionScores, roundKey) {
  const reportSection = coerceObject(reportJson?.[roundKey]);
  const reportConfidence = coerceObject(reportSection?.confidence_signal);
  if (Object.keys(reportConfidence).length) {
    return reportConfidence;
  }
  const dimensionSection = coerceObject(dimensionScores?.[roundKey]);
  return coerceObject(dimensionSection?.confidence_signal);
}

function pickSkillProfileSummary(reportJson, dimensionScores, roundKey) {
  const reportSection = coerceObject(reportJson?.[roundKey]);
  const reportSummary = coerceObject(reportSection?.skill_profile_summary);
  if (Object.keys(reportSummary).length) {
    return reportSummary;
  }
  const dimensionSection = coerceObject(dimensionScores?.[roundKey]);
  return coerceObject(dimensionSection?.skill_profile_summary);
}

function pickCoveredTopicSummary(reportJson, dimensionScores, roundKey) {
  const reportSection = coerceObject(reportJson?.[roundKey]);
  const reportSummary = coerceObject(reportSection?.covered_topic_summary);
  if (Object.keys(reportSummary).length) {
    return reportSummary;
  }
  const dimensionSection = coerceObject(dimensionScores?.[roundKey]);
  return coerceObject(dimensionSection?.covered_topic_summary);
}

function pickCoveredTopics(reportJson, dimensionScores, roundKey) {
  const reportSection = coerceObject(reportJson?.[roundKey]);
  if (Array.isArray(reportSection?.covered_topics)) {
    return reportSection.covered_topics;
  }
  const dimensionSection = coerceObject(dimensionScores?.[roundKey]);
  return Array.isArray(dimensionSection?.covered_topics) ? dimensionSection.covered_topics : [];
}

function formatSignedMetric(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "Pending";
  }
  const prefix = numeric > 0 ? "+" : "";
  return `${prefix}${numeric.toFixed(2)}`;
}

function formatLabel(value, fallback = "Pending") {
  const normalized = String(value || "").trim();
  if (!normalized) {
    return fallback;
  }
  return normalized
    .split("_")
    .map((part) => (part ? `${part[0].toUpperCase()}${part.slice(1)}` : part))
    .join(" ");
}

function formatAnalysisModes(modes) {
  if (!Array.isArray(modes) || !modes.length) {
    return "Pending";
  }
  return modes.map((mode) => formatLabel(mode)).join(", ");
}

function formatNumber(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "Pending";
  }
  if (Number.isInteger(numeric)) {
    return String(numeric);
  }
  return numeric.toFixed(2);
}

function formatMetricValue(value, kind = "text") {
  if (kind === "percent") {
    return formatPercent(value);
  }
  if (kind === "signed") {
    return formatSignedMetric(value);
  }
  if (kind === "label") {
    return formatLabel(value);
  }
  if (kind === "number") {
    return formatNumber(value);
  }
  const normalized = String(value || "").trim();
  return normalized || "Pending";
}

function sectionStatusTone(status) {
  const normalized = String(status || "not_started").trim().toLowerCase();
  if (normalized === "complete") {
    return "online";
  }
  if (normalized === "in_progress") {
    return "checking";
  }
  if (normalized === "not_started") {
    return "neutral";
  }
  return "offline";
}

function buildMetric(label, value, detail, kind = "text") {
  return {
    label,
    value,
    detail,
    kind,
  };
}

function buildRatioValue(current, total) {
  const resolvedCurrent = Number(current);
  const resolvedTotal = Number(total);
  if (!Number.isFinite(resolvedCurrent) || !Number.isFinite(resolvedTotal)) {
    return "Pending";
  }
  return `${resolvedCurrent}/${resolvedTotal}`;
}

function buildAssessmentMetricCards(section) {
  const metrics = coerceObject(section?.metrics);
  const totalQuestions = Number(metrics.total_questions ?? section?.total_questions ?? 0);
  const answeredCount = Number(metrics.answered_count ?? section?.answered_count ?? 0);
  const correctCount = Number(metrics.correct_count ?? section?.correct_count ?? 0);
  const statusLabel = formatStatus(section?.status);

  return [
    buildMetric(
      "Round Score",
      section?.score,
      totalQuestions > 0
        ? `${correctCount}/${totalQuestions} questions were answered correctly.`
        : "Assessment score appears after saved screening results are available.",
      "percent"
    ),
    buildMetric(
      "Correct",
      totalQuestions > 0 ? buildRatioValue(correctCount, totalQuestions) : "Pending",
      "Questions answered correctly in the screening round."
    ),
    buildMetric(
      "Attempted",
      totalQuestions > 0 ? buildRatioValue(answeredCount, totalQuestions) : "Pending",
      "Questions attempted before the timed assessment was submitted."
    ),
    buildMetric(
      "Status",
      statusLabel,
      totalQuestions > 0 ? `Assessment is currently ${statusLabel.toLowerCase()}.` : "Assessment session has not started yet."
    ),
  ];
}

function buildInterviewMetricCards(section) {
  const metrics = coerceObject(section?.metrics);
  const averageScores = coerceObject(metrics.average_scores ?? section?.average_scores);
  const totalQuestions = Number(metrics.total_questions ?? section?.total_questions ?? 0);
  const responseCount = Number(metrics.response_count ?? section?.response_count ?? 0);
  const responseCountSource = String(metrics.response_count_source || "responses").trim().toLowerCase();
  const hasFallbackProgress = responseCountSource === "progress_fallback";

  return [
    buildMetric(
      "Round Score",
      section?.score,
      responseCount > 0
        ? hasFallbackProgress
          ? "Round progress was preserved, but detailed per-answer scoring for this session is incomplete."
          : "Average score across the saved answers in this round."
        : "Round scoring appears after at least one answer is saved.",
      "percent"
    ),
    buildMetric(
      hasFallbackProgress ? "Progress" : "Answers Logged",
      totalQuestions > 0 ? buildRatioValue(responseCount, totalQuestions) : responseCount > 0 ? String(responseCount) : "Pending",
      hasFallbackProgress
        ? "Estimated from saved round progress because detailed answer rows are unavailable for this session."
        : "Saved answers that currently contribute to this round report."
    ),
    buildMetric(
      "Rubric Average",
      averageScores.groq,
      hasFallbackProgress
        ? "Unavailable because detailed answer rows were not preserved for this session."
        : "Groq rubric average for this round.",
      "percent"
    ),
    buildMetric(
      "Communication",
      averageScores.communication,
      hasFallbackProgress
        ? "Unavailable because detailed answer rows were not preserved for this session."
        : "Communication signal averaged across saved answers.",
      "percent"
    ),
  ];
}

function buildDsaMetricCards(section) {
  const metrics = coerceObject(section?.metrics);
  const questionScores = coerceObject(metrics.question_scores ?? section?.question_scores);
  const completedQuestionCount = Number(metrics.completed_question_count ?? section?.completed_question_count ?? 0);
  const aggregateScore = metrics.aggregate_score ?? section?.aggregate_score ?? section?.score;

  return [
    buildMetric(
      "Aggregate Score",
      aggregateScore,
      completedQuestionCount >= 2
        ? "Weighted from both DSA questions using the backend round aggregate."
        : "The weighted aggregate appears after both coding questions are complete.",
      "percent"
    ),
    buildMetric(
      "Questions Complete",
      `${completedQuestionCount}/2`,
      "Coding questions completed in the DSA round."
    ),
    buildMetric("Q1 Score", questionScores.q1, "Final score for DSA question 1.", "percent"),
    buildMetric("Q2 Score", questionScores.q2, "Final score for DSA question 2.", "percent"),
  ];
}

function buildSectionMetricCards(key, section) {
  if (key === "assessment") {
    return buildAssessmentMetricCards(section);
  }
  if (key === "dsa") {
    return buildDsaMetricCards(section);
  }
  return buildInterviewMetricCards(section);
}

function buildDsaQuestionMetricCards(questionReport) {
  const dimensionScores = coerceObject(questionReport?.dimension_scores);
  const codingJourney = coerceObject(questionReport?.coding_journey);

  return [
    buildMetric("Question Score", questionReport?.score, "Final score for this DSA question.", "percent"),
    buildMetric("Approach", dimensionScores.approach_quality, "Approach quality score.", "percent"),
    buildMetric("Correctness", dimensionScores.code_correctness, "Code correctness score.", "percent"),
    buildMetric("Complexity", dimensionScores.complexity_awareness, "Complexity awareness score.", "percent"),
    buildMetric("Follow-up", dimensionScores.followup_depth, "Follow-up depth score.", "percent"),
    buildMetric("Submissions", codingJourney.submission_count, "Saved submissions before the final evaluation.", "number"),
  ];
}

function describeCoverageTuning(profile) {
  const weights = coerceObject(profile?.weights);
  const coverageWeight = Number(weights?.concept_coverage);
  const communicationWeight = Number(weights?.communication);
  if (!Number.isFinite(coverageWeight) || !Number.isFinite(communicationWeight)) {
    return "Round-specific NLP tuning is active.";
  }
  if (coverageWeight > communicationWeight) {
    return "This round gives more weight to technical concept coverage than communication polish.";
  }
  if (communicationWeight > coverageWeight) {
    return "This round gives more weight to communication polish than concept coverage.";
  }
  return "This round balances concept coverage and communication evenly.";
}

function describeConfidenceSignal(signal) {
  const dominantLabel = String(signal?.dominant_confidence_label || "").trim().toLowerCase();
  if (dominantLabel === "confident") {
    return "Saved answers in this round were mostly direct and low on uncertainty markers.";
  }
  if (dominantLabel === "hesitant") {
    return "Saved answers in this round showed visible uncertainty, so delivery needs tightening.";
  }
  return "Saved answers in this round were mostly steady, with some uncertainty cues still present.";
}

function describeSkillProfile(summary, coveredSummary) {
  const strongCount = Number(summary?.strong_count || 0);
  const familiarCount = Number(summary?.familiar_count || 0);
  const absentCount = Number(summary?.absent_count || 0);
  const coveredCount = Number(coveredSummary?.total_questions_covered || 0);
  if (coveredCount > 0) {
    return `The technical round profiled ${strongCount + familiarCount + absentCount} role-relevant skills and has already tracked ${coveredCount} targeted prompts.`;
  }
  return `The technical round profiled ${strongCount + familiarCount + absentCount} role-relevant skills and is ready to track targeted prompt coverage.`;
}

export default function ReportPage({ authState, workflowState, onNavigate, onWorkflowStateChange, roleRequiresDsa = true }) {
  const [resetMessage, setResetMessage] = useState("");
  const [reportEnvelope, setReportEnvelope] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [infoMessage, setInfoMessage] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const hrComplete = workflowState?.interviewRoundsDone?.hr != null;
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
        if (cancelled) {
          return;
        }
        setReportEnvelope(payload);
      } catch (error) {
        if (cancelled) {
          return;
        }
        setReportEnvelope(null);
        setErrorMessage(String(error?.message || "Could not load the report."));
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    loadReport();
    return () => {
      cancelled = true;
    };
  }, [accessToken, apiBaseUrl, refreshKey, sessionId]);

  async function handleGenerateReport() {
    if (!sessionId || !accessToken || isGenerating) {
      return;
    }

    setIsGenerating(true);
    setErrorMessage("");
    setInfoMessage("");

    try {
      const response = await fetch(`${trimSlash(apiBaseUrl)}/report/session/${encodeURIComponent(sessionId)}/generate`, {
        method: "POST",
        headers: buildApiHeaders(accessToken),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(resolveErrorMessage(payload, "Could not generate the report."));
      }
      setReportEnvelope(payload);
      setInfoMessage(payload?.persisted ? "Report persisted successfully." : "Report generated, but persistence is unavailable right now.");
    } catch (error) {
      setErrorMessage(String(error?.message || "Could not generate the report."));
    } finally {
      setIsGenerating(false);
    }
  }

  const resolvedReport = reportEnvelope?.snapshot || reportEnvelope?.report || null;
  const reportJson = resolvedReport && typeof resolvedReport.report_json === "object" ? resolvedReport.report_json : {};
  const dimensionScores = resolvedReport && typeof resolvedReport.dimension_scores === "object" ? resolvedReport.dimension_scores : {};
  const reportOverview = reportJson?.overview && typeof reportJson.overview === "object" ? reportJson.overview : {};
  const roundSummaries = Array.isArray(reportJson?.round_summaries)
    ? reportJson.round_summaries.filter((entry) => roleRequiresDsa || entry?.key !== "dsa")
    : [];
  const highlights = filterDsaCopy(Array.isArray(reportJson?.highlights) ? reportJson.highlights : [], roleRequiresDsa);
  const recommendations = filterDsaCopy(Array.isArray(reportJson?.recommendations) ? reportJson.recommendations : [], roleRequiresDsa);
  const hasPersistedReport = Boolean(reportEnvelope?.report_exists || reportEnvelope?.persisted);
  const persistenceSupported = reportEnvelope?.persistence_supported !== false;
  const persistenceDetail = String(reportEnvelope?.persistence_detail || "").trim();
  const overallScore = resolvedReport?.overall_score;
  const completedStageCount = Number(reportOverview?.completed_stage_count || 0);
  const scoredStageCount = Number(reportOverview?.scored_stage_count || 0);
  const nlpSignals = INTERVIEW_NLP_ROUNDS
    .map(({ key, label }) => {
      const conceptCoverage = pickConceptCoverage(reportJson, dimensionScores, key);
      const averageRatio = Number(conceptCoverage?.average_ratio);
      if (!Number.isFinite(averageRatio)) {
        return null;
      }
      const scoringProfile = coerceObject(conceptCoverage?.scoring_profile);
      return {
        key,
        label,
        conceptCoverage,
        scoringProfile,
        sampleCoveredPoints: Array.isArray(conceptCoverage?.sample_covered_points) ? conceptCoverage.sample_covered_points.slice(0, 3) : [],
        sampleMissingPoints: Array.isArray(conceptCoverage?.sample_missing_points) ? conceptCoverage.sample_missing_points.slice(0, 3) : [],
      };
    })
    .filter(Boolean);
  const confidenceSignals = INTERVIEW_NLP_ROUNDS
    .map(({ key, label }) => {
      const confidenceSignal = pickConfidenceSignal(reportJson, dimensionScores, key);
      const averageConfidence = Number(confidenceSignal?.average_confidence);
      if (!Number.isFinite(averageConfidence)) {
        return null;
      }
      return {
        key,
        label,
        confidenceSignal,
      };
    })
    .filter(Boolean);
  const technicalSkillProfileSummary = pickSkillProfileSummary(reportJson, dimensionScores, "technical");
  const technicalCoveredTopicSummary = pickCoveredTopicSummary(reportJson, dimensionScores, "technical");
  const technicalCoveredTopics = pickCoveredTopics(reportJson, dimensionScores, "technical");
  const roundSummaryLookup = roundSummaries.reduce((lookup, entry) => {
    if (entry?.key) {
      lookup[entry.key] = entry;
    }
    return lookup;
  }, {});
  const sectionReports = REPORT_SECTION_ORDER
    .filter((entry) => roleRequiresDsa || entry.key !== "dsa")
    .map((entry) => {
      const section = coerceObject(reportJson?.[entry.key]);
      const fallbackSummary = coerceObject(roundSummaryLookup[entry.key]);
      return {
        key: entry.key,
        kicker: entry.kicker,
        label: String(section?.label || entry.label),
        status: String(section?.status || fallbackSummary?.status || "not_started"),
        score: section?.score ?? fallbackSummary?.score ?? null,
        summary: String(section?.summary || fallbackSummary?.summary || `${entry.label} is waiting on saved data.`),
        detail: String(section?.detail || fallbackSummary?.detail || "Generate or refresh the report after more stage data is saved."),
        metrics: buildSectionMetricCards(entry.key, section),
        strengths: coerceList(section?.strengths),
        risks: coerceList(section?.risks),
        sectionRecommendations: coerceList(section?.recommendations),
        evidence: coerceList(section?.evidence),
        questionReports: entry.key === "dsa" && Array.isArray(section?.question_reports) ? section.question_reports : [],
      };
    });
  const hasDeepAnalysis =
    Boolean(nlpSignals.length) ||
    Boolean(confidenceSignals.length) ||
    Boolean(Object.keys(technicalSkillProfileSummary).length) ||
    Boolean(Object.keys(technicalCoveredTopicSummary).length) ||
    Boolean(technicalCoveredTopics.length);
  const reportStatusLabel = isLoading
    ? "Loading"
    : hasPersistedReport
      ? "Persisted"
      : resolvedReport
        ? "Snapshot"
        : "Unavailable";
  const reportStatusTone = hasPersistedReport ? "online" : resolvedReport ? "checking" : "offline";

  if (!sessionId || !accessToken) {
    return (
      <RoundStatusPage
        kicker="Report"
        title="A saved interview session is required before a report can load."
        description="Resume Intake and the authenticated workflow create the session and score history this page reads from."
        statusLabel="Waiting on session"
        statusTone="checking"
        cards={[
          {
            title: "Session",
            headline: "No saved report context yet.",
            body: "Start from Resume Intake and complete the protected workflow before opening the report.",
          },
        ]}
        note="Sign in and complete earlier rounds to unlock report retrieval and persistence."
        previewTitle="Requires saved session"
        previewDescription="The report API reads persisted assessment, interview, and DSA state for one owned session."
        previewChecklist={[
          "Sign in with the same account that owns the interview session.",
          "Run Resume Intake to create the persisted session.",
          "Complete one or more stages before generating the report.",
        ]}
        primaryAction={{ label: "Open Resume Intake", target: "upload" }}
        onNavigate={onNavigate}
      />
    );
  }

  // One overall score, one card per round, and two short lists. The previous
  // layout repeated "Pending / Not started" in a dozen boxes per round, plus
  // persistence state and transcript analytics a candidate cannot act on.
  const toPct = (value) => {
    const numeric = Number(value);
    if (value === null || value === undefined || !Number.isFinite(numeric)) return null;
    return Math.round(numeric <= 1 ? numeric * 100 : numeric);
  };
  const overallPct = toPct(overallScore);
  const doneCount = sectionReports.filter((s) => String(s.status).toLowerCase() === "complete").length;
  const missingSkills = Array.isArray(technicalSkillProfileSummary?.absent_skills)
    ? technicalSkillProfileSummary.absent_skills
    : [];
  const statusOf = (status) => {
    const normalized = String(status || "").toLowerCase();
    if (normalized === "complete" || normalized === "completed") return { label: "Done", tone: "done" };
    if (normalized === "in_progress" || normalized === "active" || normalized === "partial") return { label: "In progress", tone: "progress" };
    return { label: "Not started", tone: "idle" };
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
          <button type="button" className="primary-button" onClick={handleGenerateReport} disabled={isGenerating || isLoading}>
            {isGenerating ? "Updating…" : hasPersistedReport ? "Update report" : "Save report"}
          </button>
          <WorkflowResetControl
            accessToken={accessToken}
            apiBaseUrl={apiBaseUrl}
            sessionId={sessionId}
            currentTarget="report"
            currentLabel="Report"
            onResetApplied={(payload, { successMessage } = {}) => {
              setResetMessage(successMessage || "Report data reset.");
              setInfoMessage("");
              setErrorMessage("");
              setReportEnvelope(null);
              setRefreshKey((current) => current + 1);
              onWorkflowStateChange?.((current) => ({
                ...current,
                ...buildWorkflowResetPatch(current, payload?.cleared_targets),
              }));
            }}
            triggerClassName="secondary-button"
            triggerLabel="Reset"
            disabled={!sessionId || !accessToken}
          />
        </div>
      </div>

      {errorMessage ? <p className="error-banner">{errorMessage}</p> : null}
      {resetMessage ? <p className="info-banner">{resetMessage}</p> : null}

      <section className="pp-card pp-report__hero">
        <div className="pp-ring" style={ringStyle} aria-label={overallPct === null ? "No score yet" : `Overall score ${overallPct} percent`}>
          <span>{overallPct === null ? "–" : overallPct}</span>
          <small>{overallPct === null ? "no score yet" : "overall"}</small>
        </div>
        <div className="pp-report__hero-text">
          <h2>
            {doneCount === 0
              ? "Complete a round to see your score"
              : doneCount === sectionReports.length
                ? "All rounds complete"
                : `${doneCount} of ${sectionReports.length} rounds complete`}
          </h2>
          <div className="pp-report__progress" aria-hidden="true">
            {sectionReports.map((section) => (
              <i key={section.key} className={`pp-report__seg pp-report__seg--${statusOf(section.status).tone}`} />
            ))}
          </div>
          <p>{isLoading ? "Loading your results…" : "Scores update as you finish each round."}</p>
        </div>
      </section>

      <section className="pp-round-grid" aria-label="Rounds">
        {sectionReports.map((section) => {
          const status = statusOf(section.status);
          const pct = toPct(section.score);
          const hasDetail = section.strengths.length || section.risks.length || section.sectionRecommendations.length;
          return (
            <article key={section.key} className={`pp-round pp-round--${status.tone}`}>
              <div className="pp-round__top">
                <h3>{section.label}</h3>
                <span className={`pp-chip pp-chip--${status.tone}`}>{status.label}</span>
              </div>
              <p className="pp-round__score">
                {status.tone === "idle" || pct === null ? <span className="pp-round__dash">–</span> : <>{pct}<small>%</small></>}
              </p>
              {status.tone !== "idle" ? <p className="pp-round__summary">{section.summary}</p> : null}
              {status.tone === "idle" ? (
                <button type="button" className="secondary-button" onClick={() => onNavigate?.(section.key)}>
                  Start this round →
                </button>
              ) : hasDetail ? (
                <details className="pp-round__details">
                  <summary>Details</summary>
                  {section.strengths.length ? (
                    <div><h4>Strengths</h4><ul>{section.strengths.map((item) => <li key={item}>{item}</li>)}</ul></div>
                  ) : null}
                  {section.risks.length ? (
                    <div><h4>Work on</h4><ul>{section.risks.map((item) => <li key={item}>{item}</li>)}</ul></div>
                  ) : null}
                  {section.sectionRecommendations.length ? (
                    <div><h4>Try next</h4><ul>{section.sectionRecommendations.map((item) => <li key={item}>{item}</li>)}</ul></div>
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
