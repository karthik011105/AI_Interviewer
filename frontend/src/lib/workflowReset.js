const INTERVIEW_ROUNDS = ["technical", "project_discussion", "hr"];

function normalizeResetTarget(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (["assessment", "technical", "dsa", "project_discussion", "hr", "report", "entire_interview"].includes(normalized)) {
    return normalized;
  }
  return "technical";
}

function normalizeClearedTargets(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map(normalizeResetTarget);
}

export function buildWorkflowResetPatch(currentState, clearedTargets) {
  const cleared = new Set(normalizeClearedTargets(clearedTargets));
  const nextRoundsDone = { ...(currentState?.interviewRoundsDone || {}) };

  INTERVIEW_ROUNDS.forEach((roundKey) => {
    if (cleared.has(roundKey)) {
      delete nextRoundsDone[roundKey];
    }
  });

  const patch = {
    interviewRoundsDone: nextRoundsDone,
  };

  if (cleared.has("assessment")) {
    patch.assessmentStatus = "idle";
    patch.assessmentAnsweredCount = 0;
    patch.assessmentTotalQuestions = 0;
  }

  if (cleared.has("dsa")) {
    patch.dsaPreviewViewed = false;
    patch.dsaCurrentQuestionNumber = 1;
    patch.dsaRoundCompleted = false;
  }

  if (cleared.has("technical")) {
    patch.activeInterviewRound = "technical";
  } else if (cleared.has("project_discussion")) {
    patch.activeInterviewRound = "project_discussion";
  } else if (cleared.has("hr")) {
    patch.activeInterviewRound = "hr";
  }

  return patch;
}

export function describeScopedReset(currentTarget, currentLabel) {
  const normalizedTarget = normalizeResetTarget(currentTarget);
  if (normalizedTarget === "assessment") {
    return "Clears the assessment plus all later interview stages so the workflow order stays consistent.";
  }
  if (normalizedTarget === "technical") {
    return "Clears this technical round plus DSA, Project Discussion, HR, and report data.";
  }
  if (normalizedTarget === "dsa") {
    return "Clears both DSA questions plus Project Discussion, HR, and report data.";
  }
  if (normalizedTarget === "project_discussion") {
    return "Clears this round plus HR and report data.";
  }
  if (normalizedTarget === "hr") {
    return "Clears the HR round and any persisted report data.";
  }
  if (normalizedTarget === "report") {
    return "Clears only persisted report data. Earlier round progress stays intact.";
  }
  return `Clears ${currentLabel || "this stage"}.`;
}

export function describeEntireInterviewReset() {
  return "Clears assessment, interview rounds, DSA, and report data while keeping the resume and selected role attached to this session.";
}

export function buildResetSuccessMessage(payload, currentLabel) {
  const requestedTarget = normalizeResetTarget(payload?.requested_target);
  const clearedTargets = normalizeClearedTargets(payload?.cleared_targets);
  if (requestedTarget === "entire_interview") {
    return "Interview reset. Resume and selected role were kept.";
  }
  if (requestedTarget === "report") {
    return "Report data reset.";
  }
  if (clearedTargets.length > 1) {
    return `${currentLabel} reset. Later dependent stages were also cleared.`;
  }
  return `${currentLabel} reset.`;
}