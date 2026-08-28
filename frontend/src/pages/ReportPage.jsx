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

  return (
    <div className="page-shell report-shell">
      <section className="page-heading report-heading">
        <section className="glass-panel report-hero">
          <div className="panel-head">
            <div>
              <p className="section-kicker">Final Report</p>
              <h2>{hasPersistedReport ? "Persisted interview report is loaded." : "Live report snapshot is ready."}</h2>
            </div>
            <span className={`status-pill status-pill--${reportStatusTone}`}>{reportStatusLabel}</span>
          </div>

          <p className="hero-text">
            {hasPersistedReport
              ? "This page is reading the saved report payload for the current interview session."
              : "No persisted report row exists yet. You can still inspect the live snapshot and generate a saved report when you are ready."}
          </p>

          <div className="hero-tags preview-tags">
            <span>{selectedRoleTitle || reportEnvelope?.role_selected || "Role pending"}</span>
            <span>{formatPercent(overallScore)}</span>
            <span>{hrComplete ? "HR complete" : "Earlier rounds still active"}</span>
          </div>

          <div className="action-row report-actions">
            <button
              type="button"
              className="primary-button action-row__button"
              onClick={handleGenerateReport}
              disabled={isGenerating || isLoading}
            >
              {isGenerating ? "Generating..." : hasPersistedReport ? "Refresh Report" : "Generate Report"}
            </button>
            <button
              type="button"
              className="secondary-button action-row__button"
              onClick={() => onNavigate?.(hrComplete ? (roleRequiresDsa ? "dsa" : "project_discussion") : "hr")}
              disabled={!onNavigate}
            >
              {hrComplete ? (roleRequiresDsa ? "Open DSA Workspace" : "Open Project Discussion") : "Open HR Interview"}
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
              triggerClassName="secondary-button action-row__button"
              triggerLabel="Reset"
              disabled={!sessionId || !accessToken}
            />
          </div>
        </section>

        <aside className="glass-panel report-aside">
          <span>Report State</span>
          <strong>{selectedRoleTitle || reportEnvelope?.role_selected || "Session role pending"}</strong>
          <p>
            {hasPersistedReport
              ? "A saved report row exists for this session. Use Refresh Report after completing more stages."
              : "This view is showing the backend snapshot assembled from assessment, interview, and DSA state."}
          </p>

          <ul className="preview-list report-checklist">
            <li>{workflowState?.sessionId ? "Session is owned and available." : "A persisted session is required."}</li>
            <li>{completedStageCount} stages complete, {scoredStageCount} stages currently scored.</li>
            <li>{persistenceSupported ? "Database report persistence is available." : "Database report persistence still needs the final_reports schema."}</li>
          </ul>
        </aside>
      </section>

      <div className="metric-strip report-metrics">
        <div>
          <span>Overall Score</span>
          <strong>{formatPercent(overallScore)}</strong>
          <p>{hasPersistedReport ? "Loaded from final_reports." : "Derived from the current backend snapshot."}</p>
        </div>
        <div>
          <span>Completed Stages</span>
          <strong>{completedStageCount}</strong>
          <p>Assessment, interview rounds, and DSA completion contribute here.</p>
        </div>
        <div>
          <span>Persistence</span>
          <strong>{hasPersistedReport ? "Saved" : persistenceSupported ? "Ready" : "Schema missing"}</strong>
          <p>{persistenceSupported ? "Generate or refresh to update the saved report row." : "Apply the report schema in Database to store report history."}</p>
        </div>
      </div>

      <div className="value-grid report-round-grid">
        {roundSummaries.map((entry) => (
          <article key={entry.key} className="value-card report-round-card">
            <span>{entry.label}</span>
            <strong>{formatPercent(entry.score)}</strong>
            <p>{entry.summary || "No summary yet."}</p>
            <div className="report-round-card__meta">
              <span>{formatStatus(entry.status)}</span>
              <span>{entry.detail || "Awaiting additional stage data."}</span>
            </div>
          </article>
        ))}
      </div>

      <section className="glass-panel report-list-card report-main-section">
        <div className="panel-head panel-head--tight">
          <div>
            <p className="section-kicker">Round Reports</p>
            <h3>Full report by interview section</h3>
          </div>
          <span className="status-pill status-pill--online">All Sections</span>
        </div>

        <p className="report-inline-note">
          Each block below summarizes what happened in that round, how it scored, what signals were strong, what needs work, and what the next improvement focus should be.
        </p>
      </section>

      <div className="report-section-stack">
        {sectionReports.map((section) => {
          const strengths = section.strengths.length
            ? section.strengths
            : [section.status === "complete" ? "No additional strengths were extracted for this round yet." : `Complete ${section.label} to unlock stronger positives.`];
          const risks = section.risks.length
            ? section.risks
            : [section.status === "complete" ? "No major risks were extracted for this round." : `No risk signals yet because ${section.label} is still incomplete.`];
          const sectionRecommendations = section.sectionRecommendations.length
            ? section.sectionRecommendations
            : [section.status === "complete" ? "No round-specific coaching was generated yet." : `Complete ${section.label} to generate round-specific coaching.`];
          const evidence = section.evidence.length
            ? section.evidence
            : [section.status === "complete" ? "No extra evidence snippets were captured for this round." : `Saved evidence will appear here after ${section.label} records more data.`];

          return (
            <section key={section.key} className="glass-panel report-section-card">
              <div className="panel-head panel-head--tight">
                <div>
                  <p className="section-kicker">{section.kicker}</p>
                  <h3>{section.label}</h3>
                </div>

                <div className="report-section-card__status">
                  <span className={`status-pill status-pill--${sectionStatusTone(section.status)}`}>{formatStatus(section.status)}</span>
                  <strong className="report-section-card__score">{formatPercent(section.score)}</strong>
                </div>
              </div>

              <div className="report-section-card__copy">
                <p className="report-section-card__summary">{section.summary}</p>
                <p className="report-inline-note">{section.detail}</p>
              </div>

              <div className="report-section-card__metrics">
                {section.metrics.map((metric) => (
                  <article key={`${section.key}-${metric.label}`} className="value-card report-section-metric">
                    <span>{metric.label}</span>
                    <strong>{formatMetricValue(metric.value, metric.kind)}</strong>
                    <p>{metric.detail}</p>
                  </article>
                ))}
              </div>

              {section.questionReports.length ? (
                <div className="report-question-grid">
                  {section.questionReports.map((questionReport) => {
                    const questionMetrics = buildDsaQuestionMetricCards(questionReport);
                    const questionStrengths = coerceList(questionReport?.strengths);
                    const questionRisks = coerceList(questionReport?.risks);
                    const questionRecommendations = coerceList(questionReport?.recommendations);
                    const codingJourney = coerceObject(questionReport?.coding_journey);

                    return (
                      <article key={`question-${questionReport?.question_number || questionReport?.problem_id || "pending"}`} className="report-question-card">
                        <div className="report-question-card__header">
                          <div className="report-question-card__title">
                            <span>Question {questionReport?.question_number || "Pending"}</span>
                            <h4>{questionReport?.problem_title || `DSA Question ${questionReport?.question_number || "Pending"}`}</h4>
                          </div>
                          <span className={`status-pill status-pill--${sectionStatusTone(questionReport?.score != null ? "complete" : "in_progress")}`}>
                            {formatPercent(questionReport?.score)}
                          </span>
                        </div>

                        <p className="report-question-card__summary">
                          {questionReport?.strategy_summary || questionReport?.analysis_summary || "No strategy summary was captured for this coding question yet."}
                        </p>

                        <div className="report-question-card__metrics">
                          {questionMetrics.map((metric) => (
                            <article key={`question-${questionReport?.question_number}-${metric.label}`} className="value-card report-section-metric">
                              <span>{metric.label}</span>
                              <strong>{formatMetricValue(metric.value, metric.kind)}</strong>
                              <p>{metric.detail}</p>
                            </article>
                          ))}
                        </div>

                        <div className="report-chip-group">
                          <span>Coding Journey</span>
                          <ul className="report-chip-list">
                            <li className="report-chip">Language: {formatLabel(questionReport?.language, "Pending")}</li>
                            <li className="report-chip">Submissions: {formatNumber(codingJourney?.submission_count)}</li>
                            <li className="report-chip">Judge: {formatLabel(codingJourney?.last_judge_status, "Pending")}</li>
                          </ul>
                        </div>

                        <div className="report-section-list-grid">
                          <article className="report-section-list-card report-section-list-card--positive">
                            <span>Question Strengths</span>
                            <ul className="report-list">
                              {(questionStrengths.length ? questionStrengths : ["No specific strengths were extracted for this question yet."]).map((item) => (
                                <li key={`question-strength-${questionReport?.question_number}-${item}`}>{item}</li>
                              ))}
                            </ul>
                          </article>

                          <article className="report-section-list-card report-section-list-card--danger">
                            <span>Question Risks</span>
                            <ul className="report-list">
                              {(questionRisks.length ? questionRisks : ["No major risks were extracted for this question."]).map((item) => (
                                <li key={`question-risk-${questionReport?.question_number}-${item}`}>{item}</li>
                              ))}
                            </ul>
                          </article>

                          <article className="report-section-list-card report-section-list-card--warning report-section-list-card--full">
                            <span>Question Recommendations</span>
                            <ul className="report-list">
                              {(questionRecommendations.length ? questionRecommendations : ["No extra question-level coaching is available yet."]).map((item) => (
                                <li key={`question-recommendation-${questionReport?.question_number}-${item}`}>{item}</li>
                              ))}
                            </ul>
                          </article>
                        </div>
                      </article>
                    );
                  })}
                </div>
              ) : null}

              <div className="report-section-list-grid">
                <article className="report-section-list-card report-section-list-card--positive">
                  <span>Strengths</span>
                  <ul className="report-list">
                    {strengths.map((item) => (
                      <li key={`${section.key}-strength-${item}`}>{item}</li>
                    ))}
                  </ul>
                </article>

                <article className="report-section-list-card report-section-list-card--danger">
                  <span>Risks</span>
                  <ul className="report-list">
                    {risks.map((item) => (
                      <li key={`${section.key}-risk-${item}`}>{item}</li>
                    ))}
                  </ul>
                </article>

                <article className="report-section-list-card report-section-list-card--warning">
                  <span>Recommendations</span>
                  <ul className="report-list">
                    {sectionRecommendations.map((item) => (
                      <li key={`${section.key}-recommendation-${item}`}>{item}</li>
                    ))}
                  </ul>
                </article>

                <article className="report-section-list-card report-section-list-card--neutral">
                  <span>Evidence</span>
                  <ul className="report-list">
                    {evidence.map((item) => (
                      <li key={`${section.key}-evidence-${item}`}>{item}</li>
                    ))}
                  </ul>
                </article>
              </div>
            </section>
          );
        })}
      </div>

      <section className="glass-panel report-list-card report-analysis-shell report-nlp-section">
        <div className="panel-head panel-head--tight">
          <div>
            <p className="section-kicker">Deep Analysis</p>
            <h3>Interview transcript and targeting analytics</h3>
          </div>
          <span className={`status-pill status-pill--${hasDeepAnalysis ? "checking" : "offline"}`}>{hasDeepAnalysis ? "Analytics Ready" : "Awaiting Data"}</span>
        </div>

        <p className="report-analysis-shell__intro">
          These analytics sit under the main report and explain why the interview rounds scored the way they did. They are transcript-level signals, not the full session summary.
        </p>

        <div className="report-nlp-group">
          <div className="report-nlp-group__header">
            <p className="section-kicker">Concept Coverage</p>
            <h4>Concept coverage by interview round</h4>
          </div>

          {nlpSignals.length ? (
            <div className="value-grid report-nlp-grid">
              {nlpSignals.map((entry) => {
                const coverage = entry.conceptCoverage;
                const weights = coerceObject(entry.scoringProfile?.weights);
                return (
                  <article key={entry.key} className="value-card report-round-card report-nlp-card">
                    <span>{entry.label}</span>
                    <strong>{formatPercent(coverage?.average_ratio)}</strong>
                    <p>
                      {Number(coverage?.covered_count || 0)}/{Number(coverage?.total_concepts || 0)} ideal-answer concepts were matched across saved answers.
                    </p>

                    <div className="report-nlp-card__meta">
                      <span>Threshold {formatPercent(coverage?.threshold)}</span>
                      <span>Avg lexical match {formatPercent(coverage?.average_tfidf)}</span>
                      <span>{Number(coverage?.available_responses || 0)} answers analyzed</span>
                    </div>

                    <p className="report-nlp-card__tuning">{describeCoverageTuning(entry.scoringProfile)}</p>

                    <div className="report-nlp-card__weights">
                      <span>Coverage weight {formatPercent(weights?.concept_coverage)}</span>
                      <span>Communication weight {formatPercent(weights?.communication)}</span>
                    </div>

                    {entry.sampleCoveredPoints.length ? (
                      <div className="report-chip-group">
                        <span>Detected concepts</span>
                        <ul className="report-chip-list">
                          {entry.sampleCoveredPoints.map((point) => (
                            <li key={`${entry.key}-covered-${point}`} className="report-chip">
                              {point}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}

                    {entry.sampleMissingPoints.length ? (
                      <div className="report-chip-group">
                        <span>Still missing</span>
                        <ul className="report-chip-list">
                          {entry.sampleMissingPoints.map((point) => (
                            <li key={`${entry.key}-missing-${point}`} className="report-chip report-chip--missing">
                              {point}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </article>
                );
              })}
            </div>
          ) : (
            <p className="report-inline-note">Concept coverage appears here after interview answers are saved and scored.</p>
          )}
        </div>

        <div className="report-nlp-group">
          <div className="report-nlp-group__header">
            <p className="section-kicker">Confidence Signal</p>
            <h4>Transcript confidence by interview round</h4>
          </div>

          {confidenceSignals.length ? (
            <div className="value-grid report-nlp-grid">
              {confidenceSignals.map((entry) => {
                const signal = entry.confidenceSignal;
                const availableResponses = Number(signal?.available_responses || 0);
                const sentimentBackedResponses = Number(signal?.sentiment_backed_responses || 0);
                return (
                  <article key={`${entry.key}-confidence`} className="value-card report-round-card report-nlp-card">
                    <span>{entry.label}</span>
                    <strong>{formatPercent(signal?.average_confidence)}</strong>
                    <p>{describeConfidenceSignal(signal)}</p>

                    <div className="report-nlp-card__meta">
                      <span>Avg hedging {formatPercent(signal?.average_hedging_ratio)}</span>
                      <span>Avg filler {formatPercent(signal?.average_filler_ratio)}</span>
                      <span>{availableResponses} answers analyzed</span>
                    </div>

                    <div className="report-nlp-card__weights">
                      <span>Dominant label {formatLabel(signal?.dominant_confidence_label)}</span>
                      <span>Sentiment trend {formatLabel(signal?.dominant_sentiment_label)} ({formatSignedMetric(signal?.average_sentiment_compound)})</span>
                      <span>VADER on {sentimentBackedResponses}/{availableResponses} answers</span>
                    </div>

                    <p className="report-inline-note">
                      Confidence comes from transcript sentiment plus hedging and filler cues, so neutral technical answers can still rate as steady.
                    </p>

                    <div className="report-chip-group">
                      <span>Analysis modes</span>
                      <ul className="report-chip-list">
                        {String(formatAnalysisModes(signal?.analysis_modes))
                          .split(", ")
                          .filter(Boolean)
                          .map((mode) => (
                            <li key={`${entry.key}-mode-${mode}`} className="report-chip">
                              {mode}
                            </li>
                          ))}
                      </ul>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : (
            <p className="report-inline-note">Confidence signals appear here after communication details are saved for interview answers.</p>
          )}
        </div>

        <div className="report-nlp-group">
          <div className="report-nlp-group__header">
            <p className="section-kicker">Resume-Conditioned Targeting</p>
            <h4>Technical skill profile and asked-topic coverage</h4>
          </div>

          {Object.keys(technicalSkillProfileSummary).length || Object.keys(technicalCoveredTopicSummary).length ? (
            <div className="value-grid report-nlp-grid">
              <article className="value-card report-round-card report-nlp-card">
                <span>Technical Skill Profile</span>
                <strong>{Number(technicalSkillProfileSummary?.strong_count || 0) + Number(technicalSkillProfileSummary?.familiar_count || 0) + Number(technicalSkillProfileSummary?.absent_count || 0)}</strong>
                <p>{describeSkillProfile(technicalSkillProfileSummary, technicalCoveredTopicSummary)}</p>

                <div className="report-nlp-card__meta">
                  <span>Strong {Number(technicalSkillProfileSummary?.strong_count || 0)}</span>
                  <span>Familiar {Number(technicalSkillProfileSummary?.familiar_count || 0)}</span>
                  <span>Absent {Number(technicalSkillProfileSummary?.absent_count || 0)}</span>
                </div>

                {Array.isArray(technicalSkillProfileSummary?.strong_skills) && technicalSkillProfileSummary.strong_skills.length ? (
                  <div className="report-chip-group">
                    <span>Strong skills</span>
                    <ul className="report-chip-list">
                      {technicalSkillProfileSummary.strong_skills.map((skill) => (
                        <li key={`strong-${skill}`} className="report-chip">{skill}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {Array.isArray(technicalSkillProfileSummary?.absent_skills) && technicalSkillProfileSummary.absent_skills.length ? (
                  <div className="report-chip-group">
                    <span>Missing role skills</span>
                    <ul className="report-chip-list">
                      {technicalSkillProfileSummary.absent_skills.map((skill) => (
                        <li key={`absent-${skill}`} className="report-chip report-chip--missing">{skill}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </article>

              <article className="value-card report-round-card report-nlp-card">
                <span>Asked Topic Coverage</span>
                <strong>{Number(technicalCoveredTopicSummary?.total_questions_covered || 0)}</strong>
                <p>These are the targeted technical areas that were actually asked and tracked during the round.</p>

                <div className="report-nlp-card__meta">
                  <span>Strong prompts {Number(technicalCoveredTopicSummary?.tier_counts?.strong || 0)}</span>
                  <span>Familiar prompts {Number(technicalCoveredTopicSummary?.tier_counts?.familiar || 0)}</span>
                  <span>Absent prompts {Number(technicalCoveredTopicSummary?.tier_counts?.absent || 0)}</span>
                </div>

                {Array.isArray(technicalCoveredTopicSummary?.covered_focus_skills) && technicalCoveredTopicSummary.covered_focus_skills.length ? (
                  <div className="report-chip-group">
                    <span>Tracked focus skills</span>
                    <ul className="report-chip-list">
                      {technicalCoveredTopicSummary.covered_focus_skills.map((skill) => (
                        <li key={`covered-${skill}`} className="report-chip">{skill}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {technicalCoveredTopics.length ? (
                  <div className="report-chip-group">
                    <span>Prompt tiers asked</span>
                    <ul className="report-chip-list">
                      {technicalCoveredTopics.map((entry) => (
                        <li key={`asked-${entry.question_index}-${entry.focus_skill || entry.question_tier}`} className={`report-chip${String(entry?.question_tier || "") === "absent" ? " report-chip--missing" : ""}`}>
                          {formatLabel(entry?.question_tier, "General")}{entry?.focus_skill ? `: ${entry.focus_skill}` : ""}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </article>
            </div>
          ) : (
            <p className="report-inline-note">Resume-conditioned targeting details appear here after a technical round is started with the new skill profiler and at least one tracked question is saved.</p>
          )}
        </div>
      </section>

      <div className="results-grid report-detail-grid">
        <section className="glass-panel report-list-card">
          <div className="panel-head panel-head--tight">
            <div>
              <p className="section-kicker">Highlights</p>
              <h3>What is already going well</h3>
            </div>
            <span className="status-pill status-pill--online">Strengths</span>
          </div>
          <ul className="report-list">
            {(highlights.length ? highlights : ["Generate the report after more completed stages to surface stronger highlights."]).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>

        <section className="glass-panel report-list-card">
          <div className="panel-head panel-head--tight">
            <div>
              <p className="section-kicker">Next Focus</p>
              <h3>Recommendations from persisted state</h3>
            </div>
            <span className="status-pill status-pill--checking">Coaching</span>
          </div>
          <ul className="report-list">
            {(recommendations.length ? recommendations : ["No immediate recommendations are available yet."]).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      </div>

      <div className="message-stack">
        {resetMessage ? <p className="info-banner">{resetMessage}</p> : null}
        {infoMessage ? <p className="info-banner">{infoMessage}</p> : null}
        {!hasPersistedReport && resolvedReport ? <p className="info-banner">No persisted report exists yet. Generate it to store the current snapshot in Database.</p> : null}
        {persistenceDetail ? <p className={persistenceSupported ? "info-banner" : "error-banner"}>{persistenceDetail}</p> : null}
        {errorMessage ? <p className="error-banner">{errorMessage}</p> : null}
      </div>
    </div>
  );
}
