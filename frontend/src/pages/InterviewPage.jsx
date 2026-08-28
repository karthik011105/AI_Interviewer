import { useEffect, useRef, useState } from "react";
import { MicVAD, utils } from "@ricky0123/vad-web";

import WorkflowResetControl from "../components/WorkflowResetControl";
import { buildApiHeaders } from "../lib/api";
import { buildWorkflowResetPatch } from "../lib/workflowReset";

const API_DEFAULT = "http://127.0.0.1:8000";
const TARGET_SAMPLE_RATE = 16000;
const PCM_FRAME_SAMPLES = 320;
const MAX_CLARIFICATIONS_PER_QUESTION = 2;
const INTERVIEW_ROUND_ORDER = ["technical", "project_discussion", "hr"];
const TECHNICAL_TIER_ORDER = ["strong", "familiar", "mentioned", "absent", "general"];

const ROUND_COPY = {
	hr: {
		badge: "HR",
		title: "HR Interview",
		heading: () => "HR Interview",
		startTitle: "Start HR interview",
		startDescription: "Start, resume, or restart the HR round.",
		completionTitle: "HR round complete",
	},
	technical: {
		badge: "TECHNICAL",
		title: "Technical Interview",
		heading: (roleTitle) => roleTitle ? `${roleTitle} Technical Interview` : "Technical Interview",
		startTitle: "Start technical interview",
		startDescription: "Start, resume, or restart the technical round.",
		completionTitle: "Technical round complete",
	},
	project_discussion: {
		badge: "PROJECTS",
		title: "Project Discussion",
		heading: () => "Project Discussion",
		startTitle: "Start project discussion",
		startDescription: "Start, resume, or restart the project discussion round.",
		completionTitle: "Project discussion round complete",
	},
};

const ROUND_PAGE_TARGETS = {
	technical: "technical",
	project_discussion: "project_discussion",
	hr: "hr",
};

const VOICE_STEPS = [
	{ key: "setup", label: "Session Check", meta: "Verify session and auth." },
	{ key: "connecting", label: "Connecting", meta: "Open WebSocket." },
	{ key: "playing", label: "Interviewer Speaking", meta: "Prompt audio playing." },
	{ key: "clarifying", label: "Clarifying", meta: "Interviewer answers your doubt." },
	{ key: "listening", label: "Listening", meta: "Mic live. Voice or text." },
	{ key: "transcribing", label: "Transcribing", meta: "Speech to text." },
	{ key: "evaluating", label: "Evaluating", meta: "Scoring answer." },
	{ key: "feedback", label: "Feedback", meta: "Review and continue." },
	{ key: "complete", label: "Round Complete", meta: "Move to the next stage." },
];

function normalizeInterviewRound(value) {
	const normalized = String(value || "").trim().toLowerCase();
	return Object.prototype.hasOwnProperty.call(ROUND_COPY, normalized) ? normalized : INTERVIEW_ROUND_ORDER[0];
}

function getCompletedInterviewRounds(roundsDone) {
	return INTERVIEW_ROUND_ORDER.filter((roundKey) => roundsDone?.[roundKey] != null);
}

function getNextInterviewRound(roundType) {
	const currentIndex = INTERVIEW_ROUND_ORDER.indexOf(normalizeInterviewRound(roundType));
	if (currentIndex === -1 || currentIndex >= INTERVIEW_ROUND_ORDER.length - 1) {
		return null;
	}
	return INTERVIEW_ROUND_ORDER[currentIndex + 1];
}

function isInterviewRoundUnlocked(roundType, assessmentComplete, roundsDone) {
	return Boolean(normalizeInterviewRound(roundType) || assessmentComplete || roundsDone);
}

function resolveActiveInterviewRound(requestedRound, roundsDone, assessmentComplete) {
	const normalized = normalizeInterviewRound(requestedRound);
	if (roundsDone?.[normalized] != null || isInterviewRoundUnlocked(normalized, assessmentComplete, roundsDone)) {
		return normalized;
	}
	if (!assessmentComplete) {
		return INTERVIEW_ROUND_ORDER[0];
	}
	const firstPendingUnlocked = INTERVIEW_ROUND_ORDER.find(
		(roundKey) => roundsDone?.[roundKey] == null && isInterviewRoundUnlocked(roundKey, assessmentComplete, roundsDone),
	);
	return firstPendingUnlocked || INTERVIEW_ROUND_ORDER[INTERVIEW_ROUND_ORDER.length - 1];
}

function formatInterviewRoundLabel(roundType) {
	return ROUND_COPY[normalizeInterviewRound(roundType)].title;
}

function resolveRequestError(payload, fallback) {
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

function coerceObject(value) {
	return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function toArray(value) {
	return Array.isArray(value) ? value : [];
}

function trimSlash(value) {
	return String(value || "").replace(/\/+$/, "");
}

function formatRoleTitle(value) {
	return String(value || "")
		.split("_")
		.filter(Boolean)
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
		.join(" ");
}

function formatPercent(value) {
	const numeric = Number(value);
	if (!Number.isFinite(numeric)) {
		return "Pending";
	}
	return `${Math.round(numeric * 100)}%`;
}

function formatSignedDelta(value) {
	const numeric = Number(value);
	if (!Number.isFinite(numeric)) {
		return "Pending";
	}
	const prefix = numeric > 0 ? "+" : "";
	return `${prefix}${numeric.toFixed(2)}`;
}

function formatLabel(value) {
	const normalized = String(value || "").trim();
	if (!normalized) {
		return "Pending";
	}
	return normalized
		.split("_")
		.map((segment) => (segment ? `${segment[0].toUpperCase()}${segment.slice(1)}` : segment))
		.join(" ");
}

function getTierSortIndex(value) {
	const normalized = String(value || "").trim().toLowerCase();
	const index = TECHNICAL_TIER_ORDER.indexOf(normalized);
	return index === -1 ? TECHNICAL_TIER_ORDER.length : index;
}

function resolveInterviewSocketUrl(baseUrl, sessionId, roundType, accessToken) {
	const url = new URL(trimSlash(baseUrl || API_DEFAULT));
	const normalizedPath = url.pathname.replace(/\/+$/, "");
	url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
	url.pathname = `${normalizedPath}/interview/ws/${encodeURIComponent(sessionId)}/${encodeURIComponent(roundType)}`;
	url.search = "";
	url.hash = "";
	url.searchParams.set("access_token", accessToken);
	return url.toString();
}

function countQuestionsInRoundSession(roundSession, roundType) {
	const questionsJson = roundSession?.questions_json;
	if (!questionsJson || typeof questionsJson !== "object") {
		return 0;
	}

	if (roundType === "project_discussion") {
		return Array.isArray(questionsJson.projects)
			? questionsJson.projects.reduce(
				(total, project) => total + (Array.isArray(project?.questions) ? project.questions.length : 0),
				0,
			)
			: 0;
	}

	return Array.isArray(questionsJson.questions) ? questionsJson.questions.length : 0;
}

function getTechnicalTargetingState(roundSession, currentIndex) {
	const questionsJson = coerceObject(roundSession?.questions_json);
	const skillProfileSummary = coerceObject(questionsJson?.skill_profile_summary);
	const coveredTopicSummary = coerceObject(questionsJson?.covered_topic_summary);
	const coveredTopics = toArray(questionsJson?.covered_topics);
	const questions = toArray(questionsJson?.questions);
	const promptIndex = Math.max(0, Number(currentIndex) || 0);
	const currentPrompt = coerceObject(questions[promptIndex]);
	return {
		skillProfileSummary,
		coveredTopicSummary,
		coveredTopics,
		currentPrompt,
	};
}

function describeTechnicalTargeting(skillProfileSummary, coveredTopicSummary) {
	const strongCount = Number(skillProfileSummary?.strong_count || 0);
	const familiarCount = Number(skillProfileSummary?.familiar_count || 0);
	const absentCount = Number(skillProfileSummary?.absent_count || 0);
	const totalCovered = Number(coveredTopicSummary?.total_questions_covered || 0);
	if (!strongCount && !familiarCount && !absentCount && !totalCovered) {
		return "Resume-conditioned targeting becomes visible after the technical round is prepared.";
	}
	return `This round is targeting ${strongCount} strong, ${familiarCount} familiar, and ${absentCount} absent role skills. ${totalCovered} prompts have been tracked so far.`;
}

function describeAnalyticsScope(scope) {
	if (scope === "same_role_saved_sessions") {
		return "Based on your saved technical rounds for this role.";
	}
	return "Based on your saved technical rounds across roles.";
}

function extractCompletedRoundScores(roundSessions) {
	return INTERVIEW_ROUND_ORDER.reduce((completedRounds, roundKey) => {
		const roundSession = roundSessions?.[roundKey];
		if (String(roundSession?.status || "").trim().toLowerCase() !== "complete") {
			return completedRounds;
		}

		completedRounds[roundKey] = Number(roundSession?.total_score) || 0;
		return completedRounds;
	}, {});
}

function resolveHydratedInterviewRound(preferredRound, roundSessions, assessmentComplete) {
	const completedRounds = extractCompletedRoundScores(roundSessions);
	const inProgressRound = INTERVIEW_ROUND_ORDER.find(
		(roundKey) => String(roundSessions?.[roundKey]?.status || "").trim().toLowerCase() === "in_progress",
	);
	if (inProgressRound) {
		return inProgressRound;
	}

	const normalizedPreferredRound = normalizeInterviewRound(preferredRound);
	if (
		completedRounds[normalizedPreferredRound] == null
		&& isInterviewRoundUnlocked(normalizedPreferredRound, assessmentComplete, completedRounds)
	) {
		return normalizedPreferredRound;
	}

	const firstPendingUnlocked = INTERVIEW_ROUND_ORDER.find(
		(roundKey) => completedRounds[roundKey] == null && isInterviewRoundUnlocked(roundKey, assessmentComplete, completedRounds),
	);
	return firstPendingUnlocked || INTERVIEW_ROUND_ORDER[INTERVIEW_ROUND_ORDER.length - 1];
}

function buildPersistedCompletionFeedback(roundType, roundSession) {
	const totalScore = Number(roundSession?.total_score);
	return {
		round: roundType,
		average_score: Number.isFinite(totalScore) ? totalScore : 0,
		strengths: [],
		improvement_areas: [],
		coaching_note: "This round was already completed in a previous session. Restart it to run the live interview again, or continue to the next stage.",
		feedback_mode: "persisted_session",
	};
}

function concatFloat32Arrays(left, right) {
	if (!left?.length) {
		return right || new Float32Array(0);
	}
	if (!right?.length) {
		return left;
	}
	const merged = new Float32Array(left.length + right.length);
	merged.set(left, 0);
	merged.set(right, left.length);
	return merged;
}

function concatInt16Arrays(left, right) {
	if (!left?.length) {
		return right || new Int16Array(0);
	}
	if (!right?.length) {
		return left;
	}
	const merged = new Int16Array(left.length + right.length);
	merged.set(left, 0);
	merged.set(right, left.length);
	return merged;
}

function downsampleChunk(chunk, inputRate, targetRate, resampleState) {
	const combined = concatFloat32Arrays(
		resampleState?.buffer || new Float32Array(0),
		chunk || new Float32Array(0),
	);
	if (!combined.length) {
		return {
			output: new Float32Array(0),
			state: { buffer: new Float32Array(0), position: 0 },
		};
	}

	if (!inputRate || inputRate <= 0 || inputRate === targetRate) {
		return {
			output: combined,
			state: { buffer: new Float32Array(0), position: 0 },
		};
	}

	const step = inputRate / targetRate;
	if (step <= 1) {
		return {
			output: combined,
			state: { buffer: new Float32Array(0), position: 0 },
		};
	}

	let position = Number(resampleState?.position || 0);
	const outputValues = [];

	while (position + 1 < combined.length) {
		const leftIndex = Math.floor(position);
		const rightIndex = Math.min(leftIndex + 1, combined.length - 1);
		const fraction = position - leftIndex;
		const leftSample = combined[leftIndex] || 0;
		const rightSample = combined[rightIndex] || leftSample;
		outputValues.push(leftSample + (rightSample - leftSample) * fraction);
		position += step;
	}

	const keepFrom = Math.min(combined.length, Math.floor(position));

	return {
		output: Float32Array.from(outputValues),
		state: {
			buffer: combined.slice(keepFrom),
			position: position - keepFrom,
		},
	};
}

function float32ToInt16(floatChunk) {
	const pcm = new Int16Array(floatChunk.length);
	for (let index = 0; index < floatChunk.length; index += 1) {
		const sample = Math.max(-1, Math.min(1, floatChunk[index] || 0));
		pcm[index] = sample < 0 ? sample * 32768 : sample * 32767;
	}
	return pcm;
}

function pcm16ToFloat32(arrayBuffer) {
	const input = new Int16Array(arrayBuffer);
	const output = new Float32Array(input.length);
	for (let index = 0; index < input.length; index += 1) {
		output[index] = input[index] / 32768;
	}
	return output;
}

function paletteCard(tone) {
	const colors = {
		neutral: { background: "#ffffff", border: "#dbe4f0" },
		indigo: { background: "#eef2ff", border: "#c7d2fe" },
		green: { background: "#ecfdf5", border: "#86efac" },
		warning: { background: "#fffbeb", border: "#fcd34d" },
	};
	const choice = colors[tone] || colors.neutral;
	return {
		background: choice.background,
		border: `1px solid ${choice.border}`,
		borderRadius: 14,
		padding: "18px 20px",
		boxShadow: "0 12px 28px rgba(15, 23, 42, 0.06)",
	};
}

function buttonStyle({ color = "#2563eb", ghost = false } = {}) {
	return {
		padding: "9px 16px",
		borderRadius: 9,
		border: `1px solid ${color}`,
		background: ghost ? "transparent" : color,
		color: ghost ? color : "#ffffff",
		cursor: "pointer",
		fontWeight: 700,
		fontSize: 14,
	};
}

function ScoreBadge({ score, label }) {
	const pct = Math.round((Number(score) || 0) * 100);
	const tone = pct >= 75 ? "good" : pct >= 50 ? "avg" : "poor";
	const colors = {
		good: { background: "#dcfce7", color: "#166534" },
		avg: { background: "#fef3c7", color: "#92400e" },
		poor: { background: "#fee2e2", color: "#991b1b" },
	};
	const style = colors[tone];

	return (
		<span
			style={{
				background: style.background,
				color: style.color,
				padding: "3px 10px",
				borderRadius: 999,
				fontSize: 12,
				fontWeight: 700,
			}}
		>
			{label ? `${label} ` : ""}
			{pct}%
		</span>
	);
}

function StatusPill({ label, tone }) {
	const styles = {
		neutral: { background: "#e2e8f0", color: "#334155" },
		good: { background: "#dcfce7", color: "#166534" },
		warn: { background: "#fef3c7", color: "#92400e" },
		bad: { background: "#fee2e2", color: "#991b1b" },
		info: { background: "#dbeafe", color: "#1d4ed8" },
	};
	const choice = styles[tone] || styles.neutral;
	return (
		<span
			style={{
				display: "inline-flex",
				alignItems: "center",
				padding: "4px 10px",
				borderRadius: 999,
				background: choice.background,
				color: choice.color,
				fontSize: 12,
				fontWeight: 700,
			}}
		>
			{label}
		</span>
	);
}

function TechnicalAnalyticsMiniChart({ analytics }) {
	const tierStats = toArray(analytics?.tier_stats)
		.filter((entry) => entry && typeof entry === "object")
		.slice()
		.sort((left, right) => getTierSortIndex(left?.tier) - getTierSortIndex(right?.tier));
	if (!tierStats.length) {
		return null;
	}

	const labelWidth = 74;
	const barWidth = 120;
	const metricOffset = 16;
	const rowHeight = 34;
	const chartWidth = labelWidth + barWidth + 84;
	const chartHeight = tierStats.length * rowHeight + 8;
	const overallScore = Math.max(0, Math.min(1, Number(analytics?.overall_average_score) || 0));
	const overallHesitation = Math.max(0, Math.min(1, Number(analytics?.overall_hesitation_rate) || 0));
	const overallScoreX = labelWidth + overallScore * barWidth;
	const overallHesitationX = labelWidth + overallHesitation * barWidth;

	return (
		<div
			style={{
				marginTop: 12,
				padding: "12px 12px 10px",
				borderRadius: 14,
				background: "linear-gradient(180deg, rgba(255,255,255,0.88), rgba(255,255,255,0.58))",
				border: "1px solid rgba(245, 158, 11, 0.24)",
			}}
		>
			<div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
				<div>
					<p style={{ margin: "0 0 2px", fontSize: 11, color: "#92400e", textTransform: "uppercase", letterSpacing: "0.08em" }}>Tier comparison</p>
					<strong style={{ fontSize: 14, color: "#7c2d12" }}>Score vs hesitation by targeting tier</strong>
				</div>
				<div style={{ display: "flex", gap: 10, flexWrap: "wrap", fontSize: 11, color: "#78716c" }}>
					<span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
						<span style={{ width: 10, height: 10, borderRadius: 999, background: "#2563eb" }} />
						Score
					</span>
					<span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
						<span style={{ width: 10, height: 10, borderRadius: 999, background: "#f97316" }} />
						Hesitation
					</span>
				</div>
			</div>

			<svg
				viewBox={`0 0 ${chartWidth} ${chartHeight}`}
				width="100%"
				height={chartHeight}
				role="img"
				aria-label="Technical targeting analytics chart"
			>
				<line x1={overallScoreX} y1="0" x2={overallScoreX} y2={chartHeight - 6} stroke="#1d4ed8" strokeDasharray="4 4" opacity="0.32" />
				<line x1={overallHesitationX} y1={metricOffset} x2={overallHesitationX} y2={chartHeight - 2} stroke="#ea580c" strokeDasharray="3 5" opacity="0.24" />
				{tierStats.map((entry, index) => {
					const score = Math.max(0, Math.min(1, Number(entry?.average_score) || 0));
					const hesitation = Math.max(0, Math.min(1, Number(entry?.hesitation_rate) || 0));
					const scoreWidth = score > 0 ? Math.max(6, Math.round(score * barWidth)) : 0;
					const hesitationWidth = hesitation > 0 ? Math.max(6, Math.round(hesitation * barWidth)) : 0;
					const y = 8 + index * rowHeight;

					return (
						<g key={`technical-analytics-${entry?.tier || index}`} transform={`translate(0 ${y})`}>
							<text x="0" y="11" fontSize="11" fontWeight="700" fill="#7c2d12">
								{formatLabel(entry?.tier)}
							</text>
							<rect x={labelWidth} y="0" width={barWidth} height="10" rx="5" fill="rgba(148, 163, 184, 0.16)" />
							<rect x={labelWidth} y="0" width={scoreWidth} height="10" rx="5" fill="#2563eb" />
							<rect x={labelWidth} y={metricOffset} width={barWidth} height="8" rx="4" fill="rgba(251, 146, 60, 0.18)" />
							<rect x={labelWidth} y={metricOffset} width={hesitationWidth} height="8" rx="4" fill="#f97316" />
							<text x={labelWidth + barWidth + 8} y="10" fontSize="10" fill="#1e293b">
								{formatPercent(score)}
							</text>
							<text x={labelWidth + barWidth + 8} y={metricOffset + 8} fontSize="10" fill="#9a3412">
								{formatPercent(hesitation)}
							</text>
						</g>
					);
				})}
			</svg>

			<p style={{ margin: "8px 0 0", fontSize: 11, color: "#78716c" }}>
				Dashed markers show the overall saved-response averages for score and hesitation.
			</p>
		</div>
	);
}

function WaveformBar({ active }) {
	return (
		<div style={{ display: "flex", alignItems: "center", gap: 3, height: 32 }}>
			{Array.from({ length: 12 }).map((_, index) => (
				<div
					key={index}
					style={{
						width: 4,
						height: active ? `${8 + (index % 5) * 4}px` : "6px",
						background: active ? "#2563eb" : "#cbd5e1",
						borderRadius: 999,
						animation: active ? `wave-bar ${0.45 + index * 0.04}s ease-in-out infinite alternate` : "none",
					}}
				/>
			))}
			<style>{`
				@keyframes wave-bar {
					from { height: 6px; }
					to { height: 28px; }
				}
			`}</style>
		</div>
	);
}

function ProgressBar({ current, total }) {
	const safeTotal = Math.max(Number(total) || 0, 1);
	const safeCurrent = Math.min(Math.max(Number(current) || 0, 0), safeTotal);
	const width = `${(safeCurrent / safeTotal) * 100}%`;

	return (
		<div style={{ marginBottom: 18 }}>
			<div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, color: "#64748b", marginBottom: 6 }}>
				<span>{`Question ${safeCurrent} of ${safeTotal}`}</span>
				<span>{`${Math.round((safeCurrent / safeTotal) * 100)}%`}</span>
			</div>
			<div style={{ height: 9, borderRadius: 999, background: "#e2e8f0", overflow: "hidden" }}>
				<div style={{ width, height: "100%", borderRadius: 999, background: "linear-gradient(90deg, #2563eb, #06b6d4)" }} />
			</div>
		</div>
	);
}

function Section({ title, items }) {
	if (!Array.isArray(items) || items.length === 0) {
		return null;
	}

	return (
		<div style={{ marginBottom: 16 }}>
			<p style={{ fontWeight: 700, marginBottom: 8 }}>{title}</p>
			<ul style={{ margin: 0, paddingLeft: 18, color: "#374151", lineHeight: 1.6 }}>
				{items.map((item, index) => (
					<li key={`${title}-${index}`}>{item}</li>
				))}
			</ul>
		</div>
	);
}

export default function InterviewPage({
	authState,
	workflowState,
	onWorkflowStateChange,
	onNavigate,
	forcedRound,
	hideRoundNavigation = false,
	roleRequiresDsa = true,
	nextPageTarget,
	nextPageLabel,
	requiresDsaPreview = false,
	dsaLockedTarget = "dsa",
	dsaLockedLabel = "Open DSA Workspace",
	dsaLockedMessage,
}) {
	const baseUrl = trimSlash(workflowState?.apiBaseUrl || API_DEFAULT);
	const sessionId = String(workflowState?.sessionId || "").trim();
	const accessToken = String(authState?.accessToken || "").trim();
	const roleTitle = workflowState?.selectedRoleTitle || formatRoleTitle(workflowState?.selectedRoleKey);
	const assessmentComplete = workflowState?.assessmentStatus === "complete";
	const interviewRoundsDone = workflowState?.interviewRoundsDone || {};
	const selectedRound = forcedRound
		? normalizeInterviewRound(forcedRound)
		: resolveActiveInterviewRound(
			workflowState?.activeInterviewRound,
			interviewRoundsDone,
			assessmentComplete,
		);
	const currentRoundCopy = ROUND_COPY[selectedRound];
	const completedRounds = getCompletedInterviewRounds(interviewRoundsDone);
	const selectedRoundComplete = interviewRoundsDone[selectedRound] != null;
	const interviewRoundUnlocked = selectedRoundComplete || isInterviewRoundUnlocked(selectedRound, assessmentComplete, interviewRoundsDone);
	const dsaRoundCompleted = Boolean(workflowState?.dsaRoundCompleted);
	const dsaGateBlocked = false;
	const selectedRoundUnlocked = interviewRoundUnlocked && !dsaGateBlocked;
	const nextRound = getNextInterviewRound(selectedRound);
	const allRoundsComplete = completedRounds.length === INTERVIEW_ROUND_ORDER.length;
	const workflowOrderLabel = roleRequiresDsa
		? "Assessment -> Technical -> DSA -> Project Discussion -> HR -> Report"
		: "Assessment -> Technical -> Project Discussion -> HR -> Report";

	const [voiceStep, setVoiceStep] = useState("setup");
	const [connectionState, setConnectionState] = useState("idle");
	const [currentQuestion, setCurrentQuestion] = useState(null);
	const [currentIndex, setCurrentIndex] = useState(0);
	const [nextQuestionIndex, setNextQuestionIndex] = useState(0);
	const [totalQuestions, setTotalQuestions] = useState(0);
	const [transcript, setTranscript] = useState("");
	const [transcriptKind, setTranscriptKind] = useState("");
	const [typedAnswer, setTypedAnswer] = useState("");
	const [typedClarification, setTypedClarification] = useState("");
	const [useTyped, setUseTyped] = useState(false);
	const [captureReady, setCaptureReady] = useState(false);
	const [playbackReady, setPlaybackReady] = useState(false);
	const [isPlaying, setIsPlaying] = useState(false);
	const [lastEval, setLastEval] = useState(null);
	const [roundFeedback, setRoundFeedback] = useState(null);
	const [roundSession, setRoundSession] = useState(null);
	const [roundDone, setRoundDone] = useState(false);
	const [busyState, setBusyState] = useState("idle");
	const [sessionSyncState, setSessionSyncState] = useState("idle");
	const [errorMessage, setErrorMessage] = useState("");
	const [infoMessage, setInfoMessage] = useState("");
	const [sttMissCount, setSttMissCount] = useState(0);
	const [persistedRoundSessions, setPersistedRoundSessions] = useState({});
	const [technicalAnalytics, setTechnicalAnalytics] = useState(null);
	const [technicalAnalyticsState, setTechnicalAnalyticsState] = useState("idle");
	const [technicalAnalyticsError, setTechnicalAnalyticsError] = useState("");
	const [inClarificationMode, setInClarificationMode] = useState(false);
	const [clarificationReplies, setClarificationReplies] = useState([]);
	const [clarificationRemaining, setClarificationRemaining] = useState(MAX_CLARIFICATIONS_PER_QUESTION);

	const socketRef = useRef(null);
	const manualSocketCloseRef = useRef(false);
	const typedModeRef = useRef(false);
	const clarificationModeRef = useRef(false);
	const roundDoneRef = useRef(false);
	const voiceStepRef = useRef("setup");
	const playbackContextRef = useRef(null);
	const playbackSampleRateRef = useRef(22050);
	const playbackCursorRef = useRef(0);
	const playbackEndedRef = useRef(false);
	const playbackActiveRef = useRef(false);
	const playbackSourcesRef = useRef(new Set());
	const vadRef = useRef(null);
	const lastSocketErrorRef = useRef("");

	useEffect(() => {
		typedModeRef.current = useTyped;
	}, [useTyped]);

	useEffect(() => {
		clarificationModeRef.current = inClarificationMode;
	}, [inClarificationMode]);

	useEffect(() => {
		voiceStepRef.current = voiceStep;
	}, [voiceStep]);

	useEffect(() => {
		roundDoneRef.current = roundDone;
	}, [roundDone]);

	useEffect(() => {
		if (workflowState?.activeInterviewRound === selectedRound) {
			return;
		}
		onWorkflowStateChange?.((current) => ({
			...current,
			activeInterviewRound: selectedRound,
		}));
	}, [onWorkflowStateChange, selectedRound, workflowState?.activeInterviewRound]);

	const activeTechnicalRoundSession = selectedRound === "technical"
		? (roundSession || persistedRoundSessions.technical || null)
		: null;
	const technicalTargetingState = getTechnicalTargetingState(activeTechnicalRoundSession, currentIndex);
	const trackedTechnicalPromptCount = toArray(activeTechnicalRoundSession?.questions_json?.covered_topics).length;

	useEffect(() => {
		if (selectedRound !== "technical" || !sessionId || !accessToken) {
			setTechnicalAnalytics(null);
			setTechnicalAnalyticsState("idle");
			setTechnicalAnalyticsError("");
			return undefined;
		}

		let cancelled = false;
		setTechnicalAnalyticsState((current) => (current === "idle" ? "loading" : "refreshing"));
		setTechnicalAnalyticsError("");

		async function loadTechnicalAnalytics() {
			try {
				const response = await fetch(
					`${baseUrl}/interview/analytics/${encodeURIComponent(sessionId)}/technical`,
					{ headers: buildApiHeaders(accessToken) },
				);
				const payload = await response.json().catch(() => ({}));
				if (!response.ok) {
					throw new Error(resolveRequestError(payload, "Could not load technical targeting analytics."));
				}
				if (cancelled) {
					return;
				}
				setTechnicalAnalytics(coerceObject(payload?.analytics));
				setTechnicalAnalyticsState("ready");
			} catch (error) {
				if (cancelled) {
					return;
				}
				setTechnicalAnalytics(null);
				setTechnicalAnalyticsState("error");
				setTechnicalAnalyticsError(String(error?.message || "Could not load technical targeting analytics."));
			}
		}

		loadTechnicalAnalytics();
		return () => {
			cancelled = true;
		};
	}, [accessToken, baseUrl, selectedRound, sessionId, trackedTechnicalPromptCount]);

	useEffect(() => {
		if (!sessionId || !accessToken) {
			setSessionSyncState("idle");
			setPersistedRoundSessions({});
			return;
		}

		let cancelled = false;
		const preferredRound = workflowState?.activeInterviewRound;
		setSessionSyncState("loading");
		setPersistedRoundSessions({});

		async function loadRoundSessions() {
			try {
				const roundEntries = await Promise.all(
					INTERVIEW_ROUND_ORDER.map(async (roundKey) => {
						const response = await fetch(
							`${baseUrl}/interview/session/${encodeURIComponent(sessionId)}/${encodeURIComponent(roundKey)}`,
							{ headers: buildApiHeaders(accessToken) },
						);

						if (response.status === 404) {
							return [roundKey, null];
						}

						const payload = await response.json().catch(() => ({}));
						if (!response.ok) {
							throw new Error(
								resolveRequestError(
									payload,
									`Could not load the saved ${formatInterviewRoundLabel(roundKey)} session.`,
								),
							);
						}

						return [roundKey, payload];
					}),
				);

				if (cancelled) {
					return;
				}

				const nextRoundSessions = Object.fromEntries(roundEntries);
				const completedRounds = extractCompletedRoundScores(nextRoundSessions);
				const nextActiveRound = resolveHydratedInterviewRound(
					preferredRound,
					nextRoundSessions,
					assessmentComplete,
				);

				setPersistedRoundSessions(nextRoundSessions);
				setSessionSyncState("ready");
				setErrorMessage("");
				onWorkflowStateChange?.((current) => ({
					...current,
					interviewRoundsDone: completedRounds,
					activeInterviewRound: nextActiveRound,
				}));
			} catch (error) {
				if (cancelled) {
					return;
				}

				setSessionSyncState("error");
				setPersistedRoundSessions({});
				setErrorMessage(error.message || "Could not load the saved interview state.");
			}
		}

		loadRoundSessions();

		return () => {
			cancelled = true;
		};
	}, [accessToken, baseUrl, onWorkflowStateChange, sessionId]);

	useEffect(() => {
		if (busyState === "starting" || connectionState === "connecting" || connectionState === "open") {
			return;
		}

		const persistedRoundSession = persistedRoundSessions[selectedRound] || null;
		if (!persistedRoundSession) {
			setRoundSession(null);
			setRoundDone(false);
			setRoundFeedback(null);
			setCurrentQuestion(null);
			resetClarificationState();
			setCurrentIndex(0);
			setNextQuestionIndex(0);
			setTotalQuestions(0);
			setVoiceStep("setup");
			return;
		}

		const persistedStatus = String(persistedRoundSession.status || "").trim().toLowerCase();
		const persistedIndex = Math.max(0, Number(persistedRoundSession.current_question_index) || 0);
		const persistedQuestionCount = countQuestionsInRoundSession(persistedRoundSession, selectedRound);

		setRoundSession(persistedRoundSession);
		setCurrentQuestion(null);
		setCurrentIndex(persistedIndex);
		setNextQuestionIndex(persistedIndex);
		setTotalQuestions(persistedQuestionCount);
		setLastEval(null);
		setTranscript("");
		setTranscriptKind("");
		setTypedAnswer("");
		setConnectionState("idle");
		setErrorMessage("");

		if (persistedStatus === "complete") {
			if (roundDoneRef.current && roundFeedback?.feedback_mode !== "persisted_session") {
				return;
			}

			setRoundDone(true);
			resetClarificationState();
			setVoiceStep("complete");
			setRoundFeedback(buildPersistedCompletionFeedback(selectedRound, persistedRoundSession));
			setInfoMessage(`${currentRoundCopy.title} is already complete for this session.`);
			return;
		}

		setRoundDone(false);
		resetClarificationState();
		setVoiceStep("setup");
		setRoundFeedback(null);
		if (persistedQuestionCount > 0) {
			setInfoMessage(
				`Saved ${currentRoundCopy.title.toLowerCase()} progress found at question ${Math.min(persistedIndex + 1, persistedQuestionCount)} of ${persistedQuestionCount}. Start the stream to continue from the backend state.`,
			);
		}
	}, [busyState, connectionState, currentRoundCopy.title, persistedRoundSessions, roundFeedback, selectedRound]);

	function resetClarificationState() {
		setInClarificationMode(false);
		setClarificationReplies([]);
		setClarificationRemaining(MAX_CLARIFICATIONS_PER_QUESTION);
		setTypedClarification("");
	}

	function resetRoundState() {
		roundDoneRef.current = false;
		closeSocket(true);
		stopPlayback();
		stopCaptureStream();
		setBusyState("idle");
		setConnectionState("idle");
		setVoiceStep("setup");
		setCurrentQuestion(null);
		setCurrentIndex(0);
		setNextQuestionIndex(0);
		setTotalQuestions(0);
		setTranscript("");
		setTranscriptKind("");
		setTypedAnswer("");
		setLastEval(null);
		setRoundFeedback(null);
		setRoundSession(null);
		setRoundDone(false);
		setUseTyped(false);
		resetClarificationState();
		setSttMissCount(0);
		setErrorMessage("");
		setInfoMessage("");
		lastSocketErrorRef.current = "";
	}

	function handleRoundSelection(nextRound) {
		const normalized = normalizeInterviewRound(nextRound);
		const roundIsUnlocked = interviewRoundsDone[normalized] != null || isInterviewRoundUnlocked(normalized, assessmentComplete, interviewRoundsDone);
		if (!roundIsUnlocked) {
			const previousRound = INTERVIEW_ROUND_ORDER[INTERVIEW_ROUND_ORDER.indexOf(normalized) - 1];
			setErrorMessage(
				assessmentComplete
					? `Finish the ${formatInterviewRoundLabel(previousRound)} first before opening ${formatInterviewRoundLabel(normalized)}.`
					: "Complete the assessment first before opening live interview rounds.",
			);
			return;
		}

		resetRoundState();
		setInfoMessage(`${formatInterviewRoundLabel(normalized)} selected. Start or resume when you are ready.`);
		onWorkflowStateChange?.((current) => ({
			...current,
			activeInterviewRound: normalized,
		}));
	}

	function clearCompletedRound(roundType) {
		onWorkflowStateChange?.((current) => {
			const nextRoundsDone = { ...(current?.interviewRoundsDone || {}) };
			delete nextRoundsDone[roundType];
			return {
				...current,
				interviewRoundsDone: nextRoundsDone,
			};
		});
	}

	function handleWorkflowReset(payload, { successMessage } = {}) {
		const clearedTargets = Array.isArray(payload?.cleared_targets) ? payload.cleared_targets : [];
		resetRoundState();
		setPersistedRoundSessions((current) => {
			const nextSessions = { ...(current || {}) };
			clearedTargets.forEach((target) => {
				if (INTERVIEW_ROUND_ORDER.includes(target)) {
					delete nextSessions[target];
				}
			});
			return nextSessions;
		});
		setErrorMessage("");
		setInfoMessage(successMessage || `${currentRoundCopy.title} reset.`);
		onWorkflowStateChange?.((current) => ({
			...current,
			...buildWorkflowResetPatch(current, clearedTargets),
		}));
	}

	function clearCaptureBuffers() {
		// No longer needed with MicVAD
	}

	function flushCaptureChunk(floatChunk) {
		// Handled directly by MicVAD's onSpeechEnd
	}

	async function prepareCapture() {
		if (vadRef.current) {
			setCaptureReady(true);
			return true;
		}

		try {
			const vad = await MicVAD.new({
				onSpeechStart: () => {
					if (voiceStepRef.current === "playing" || voiceStepRef.current === "clarifying") {
						sendSocketJson({ type: "interrupt" });
					}
					if (voiceStepRef.current === "listening") {
						sendSocketJson({ type: "speech_start" });
					}
				},
				onSpeechEnd: (audio) => {
					if (typedModeRef.current || roundDoneRef.current || voiceStepRef.current !== "listening") return;
					
					const pcmChunk = float32ToInt16(audio);
					const socket = socketRef.current;
					if (socket && socket.readyState === WebSocket.OPEN) {
						sendSocketJson({ type: "speech_end" });
						socket.send(pcmChunk.buffer);
					}
				},
				positiveSpeechThreshold: 0.8,
				negativeSpeechThreshold: 0.8 - 0.15,
				preSpeechPadFrames: 5,
				minSpeechFrames: 3,
			});
			vad.start();
			vadRef.current = vad;
			setCaptureReady(true);
			return true;
		} catch (error) {
			console.error("VAD initialization failed:", error);
			setCaptureReady(false);
			setUseTyped(true);
			setInfoMessage(`Microphone streaming is unavailable: ${error.message}. Typed fallback is enabled.`);
			return false;
		}
	}

	function stopCaptureStream() {
		if (vadRef.current) {
			vadRef.current.destroy();
			vadRef.current = null;
		}
		setCaptureReady(false);
	}

	function closeSocket(manual = true) {
		const socket = socketRef.current;
		if (!socket) {
			return;
		}
		manualSocketCloseRef.current = manual;
		if (socket.readyState < WebSocket.CLOSING) {
			socket.close();
		}
		socketRef.current = null;
	}

	function handleSocketJsonMessage(payload) {
		const type = String(payload?.type || "");

		if (type === "connected") {
			lastSocketErrorRef.current = "";
			setConnectionState("open");
			setBusyState("idle");
			setVoiceStep("connecting");
			setCurrentIndex(Number(payload.current_question_index) || 0);
			setNextQuestionIndex(Number(payload.current_question_index) || 0);
			setTotalQuestions(Number(payload.total_questions) || 0);
			setErrorMessage("");
			return;
		}

		if (type === "state_change") {
			const mapped = {
				IDLE: "connecting",
				PLAYING: "playing",
				CLARIFYING: "clarifying",
				LISTENING: "listening",
				TRANSCRIBING: "transcribing",
				EVALUATING: "evaluating",
				FEEDBACK: "feedback",
				COMPLETE: "complete",
			}[String(payload.state || "").toUpperCase()];
			if (mapped) {
				if (mapped === "playing" || mapped === "clarifying") {
					playbackActiveRef.current = true;
					clearCaptureBuffers();
				}
				if (mapped === "listening") {
					playbackActiveRef.current = false;
				}
				setVoiceStep(mapped);
			}
			return;
		}

		if (type === "question") {
			lastSocketErrorRef.current = "";
			playbackActiveRef.current = true;
			clearCaptureBuffers();
			resetClarificationState();
			setCurrentQuestion({ text: String(payload.text || "") });
			setCurrentIndex(Number(payload.index) || 0);
			setNextQuestionIndex(Number(payload.index) || 0);
			setTotalQuestions(Number(payload.total) || totalQuestions || 0);
			setTranscript("");
			setTranscriptKind("");
			setTypedAnswer("");
			setLastEval(null);
			setErrorMessage("");
			setSttMissCount(0);
			setInfoMessage("Wait for the question audio to finish, or tap Interrupt And Answer to start speaking sooner.");
			return;
		}

		if (type === "clarification_mode_active") {
			setInClarificationMode(true);
			setClarificationRemaining(Number(payload.remaining) || MAX_CLARIFICATIONS_PER_QUESTION);
			setSttMissCount(0);
			setErrorMessage("");
			setInfoMessage(
				useTyped || !captureReady
					? "Clarification mode is active. Type a short doubt below, or return to your answer when ready."
					: "Clarification mode is active. Ask a short doubt, then return to your answer.",
			);
			return;
		}

		if (type === "clarification_mode_inactive") {
			setInClarificationMode(false);
			setClarificationRemaining(Number(payload.remaining) || 0);
			setTypedClarification("");
			setErrorMessage("");
			setInfoMessage("Clarification closed. Continue with your answer.");
			return;
		}

		if (type === "clarification_reply") {
			const clarificationIndex = Number(payload.clarification_index) || 0;
			const remaining = Number(payload.remaining);
			setClarificationReplies((current) => [
				...current,
				{
					index: clarificationIndex || current.length + 1,
					text: String(payload.text || "").trim(),
				},
			]);
			if (Number.isFinite(remaining)) {
				setClarificationRemaining(Math.max(0, remaining));
			}
			setTypedClarification("");
			setErrorMessage("");
			setInfoMessage(
				Number.isFinite(remaining) && remaining <= 0
					? "Clarification limit reached for this question. Continue with your answer."
					: "Clarification received. Ask another doubt or return to your answer.",
			);
			return;
		}

		if (type === "clarification_limit_reached") {
			setInClarificationMode(false);
			setClarificationRemaining(0);
			setErrorMessage("");
			setInfoMessage("Clarification limit reached for this question. Continue with your answer.");
			return;
		}

		if (type === "tts_start") {
			playbackSampleRateRef.current = Number(payload.sample_rate) || 22050;
			playbackEndedRef.current = false;
			playbackActiveRef.current = true;
			clearCaptureBuffers();
			if (playbackContextRef.current) {
				playbackCursorRef.current = playbackContextRef.current.currentTime;
			}
			setIsPlaying(true);
			return;
		}

		if (type === "tts_end") {
			playbackEndedRef.current = true;
			if (playbackSourcesRef.current.size === 0) {
				playbackActiveRef.current = false;
				setIsPlaying(false);
			}
			return;
		}

		if (type === "interrupt_ack") {
			stopPlayback();
			return;
		}

		if (type === "transcript") {
			const text = String(payload.text || "").trim();
			setTranscript(text);
			setTranscriptKind(text ? (clarificationModeRef.current ? "clarification" : "answer") : "");
			if (payload.no_speech) {
				if (clarificationModeRef.current) {
					setErrorMessage("No clarification speech detected. Try again or tap Done Asking.");
					return;
				}
				setSttMissCount((current) => {
					const next = current + 1;
					if (next >= 2) {
						setUseTyped(true);
					}
					return next;
				});
				setErrorMessage("No speech detected. Try again or switch to text input.");
			} else {
				setSttMissCount(0);
				setErrorMessage("");
			}
			return;
		}

		if (type === "feedback") {
			setLastEval(payload.evaluation || null);
			setRoundSession(payload.round_session || null);
			if (payload.round_session) {
				setPersistedRoundSessions((current) => ({
					...current,
					[selectedRound]: payload.round_session,
				}));
			}
			setNextQuestionIndex(Number(payload.next_question_index) || currentIndex);
			setErrorMessage("");
			return;
		}

		if (type === "round_complete") {
			roundDoneRef.current = true;
			setRoundDone(true);
			resetClarificationState();
			setVoiceStep("complete");
			setRoundFeedback(payload.round_feedback || null);
			setRoundSession(payload.round_session || null);
			if (payload.round_session) {
				setPersistedRoundSessions((current) => ({
					...current,
					[selectedRound]: payload.round_session,
				}));
			}
			setInfoMessage(`${currentRoundCopy.title} complete.`);
			onWorkflowStateChange?.((current) => ({
				...current,
				interviewRoundsDone: {
					...(current?.interviewRoundsDone || {}),
					[selectedRound]: Number(payload.total_score) || 0,
				},
			}));
			stopPlayback();
			stopCaptureStream();
			closeSocket(true);
			return;
		}

		if (type === "error") {
			const code = String(payload.code || "").trim();
			const message = String(payload.message || "Interview streaming failed.").trim();
			lastSocketErrorRef.current = message;
			setErrorMessage(message);
			if (payload.fallback === "typed" || code === "stt_unavailable" || code === "stt_failed") {
				if (clarificationModeRef.current) {
					setUseTyped(true);
					setVoiceStep("listening");
					setInfoMessage("Voice clarification is unavailable right now. Type your doubt below or tap Done Asking.");
					return;
				}
				setUseTyped(true);
				setVoiceStep("listening");
				setInfoMessage("Voice transcription is unavailable right now. Continue this round with typed answers.");
			}
		}
	}

	function openRoundSocket() {
		const socketUrl = resolveInterviewSocketUrl(baseUrl, sessionId, selectedRound, accessToken);
		const socket = new WebSocket(socketUrl);
		socket.binaryType = "arraybuffer";
		manualSocketCloseRef.current = false;
		socketRef.current = socket;

		socket.onopen = () => {
			lastSocketErrorRef.current = "";
			setConnectionState("open");
			setBusyState("idle");
		};

		socket.onmessage = (event) => {
			if (typeof event.data === "string") {
				try {
					handleSocketJsonMessage(JSON.parse(event.data));
				} catch {
					setErrorMessage("The interview socket returned malformed JSON.");
				}
				return;
			}

			if (event.data instanceof ArrayBuffer) {
				enqueuePlaybackChunk(event.data);
				return;
			}

			if (event.data?.arrayBuffer) {
				event.data.arrayBuffer().then(enqueuePlaybackChunk).catch(() => {
					setErrorMessage("Question audio playback failed.");
				});
			}
		};

		socket.onerror = () => {
			setBusyState("idle");
			setConnectionState("closed");
			setErrorMessage("The interview socket encountered a network error.");
		};

		socket.onclose = (event) => {
			socketRef.current = null;
			setBusyState("idle");
			setConnectionState("closed");

			if (!manualSocketCloseRef.current && !roundDoneRef.current) {
				const closeReason = String(event.reason || "").trim();
				const serverError = String(lastSocketErrorRef.current || "").trim();
				if (serverError) {
					setErrorMessage(serverError);
				} else if (closeReason) {
					setErrorMessage(closeReason);
				} else if (event.code === 1008) {
					setErrorMessage("The interview stream was rejected. Sign in again or reopen the session.");
				} else {
					setErrorMessage("The interview stream disconnected. Reconnect to continue this round.");
				}
			}
		};
	}

	async function prepareRoundSession(forceRestart) {
		const response = await fetch(`${baseUrl}/interview/start`, {
			method: "POST",
			headers: buildApiHeaders(accessToken, {
				"Content-Type": "application/json",
			}),
			body: JSON.stringify({
				session_id: sessionId,
				round: selectedRound,
				force_restart: forceRestart,
			}),
		});

		const payload = await response.json().catch(() => ({}));
		if (!response.ok) {
			throw new Error(
				resolveRequestError(
					payload,
					forceRestart
						? `Could not restart the ${currentRoundCopy.title}.`
						: `Could not start the ${currentRoundCopy.title}.`,
				),
			);
		}

		return payload;
	}

	async function startRound(options = {}) {
		const shouldForceRestart = Boolean(options.forceRestart ?? selectedRoundComplete);

		if (!sessionId) {
			setErrorMessage("No active session. Complete Resume Intake first.");
			return;
		}
		if (!accessToken) {
			setErrorMessage("Sign in is required before opening the streaming interview.");
			return;
		}

		setBusyState("starting");
		setErrorMessage("");

		let startPayload;
		try {
			startPayload = await prepareRoundSession(shouldForceRestart);
		} catch (error) {
			setBusyState("idle");
			setErrorMessage(error.message || `Could not start the ${currentRoundCopy.title}.`);
			return;
		}

		if (shouldForceRestart) {
			clearCompletedRound(selectedRound);
		}

		resetRoundState();
		setBusyState("starting");
		setConnectionState("connecting");
		setVoiceStep("connecting");
		setRoundSession(startPayload?.round_session || null);
		if (startPayload?.round_session) {
			setPersistedRoundSessions((current) => ({
				...current,
				[selectedRound]: startPayload.round_session,
			}));
		}
		setInfoMessage(
			shouldForceRestart
				? `${currentRoundCopy.title} restarted. Opening the live stream from question 1.`
				: `Opening the ${currentRoundCopy.title.toLowerCase()} stream.`,
		);

		await ensurePlaybackContext();
		await prepareCapture();
		openRoundSocket();
	}

	function interruptPlayback() {
		stopPlayback();
		sendSocketJson({ type: "skip_audio" });
	}

	function startClarificationMode() {
		if (!captureReady) {
			setUseTyped(true);
		}
		setErrorMessage("");
		setInfoMessage(useTyped || !captureReady ? "Opening typed clarification mode..." : "Opening clarification mode...");
		if (!sendSocketJson({ type: "start_clarification" })) {
			setErrorMessage("The interview socket is not connected.");
		}
	}

	function finishClarificationMode() {
		setErrorMessage("");
		setInfoMessage("Returning to answer mode...");
		if (!sendSocketJson({ type: "end_clarification" })) {
			setErrorMessage("The interview socket is not connected.");
		}
	}

	function finishVoiceAnswer() {
		setErrorMessage("");
		setInfoMessage("Finishing your answer. Short pauses are allowed, but this submits the voice captured so far.");
		if (!sendSocketJson({ type: "end_answer" })) {
			setErrorMessage("The interview socket is not connected.");
			return;
		}
	}

	function submitTypedAnswer() {
		const text = typedAnswer.trim();
		if (!text) {
			setErrorMessage("Type your answer first.");
			return;
		}

		if (!sendSocketJson({ type: "typed_answer", text })) {
			setErrorMessage("The interview socket is not connected.");
			return;
		}

		setTranscript(text);
		setTranscriptKind("answer");
		setTypedAnswer("");
		setErrorMessage("");
		setVoiceStep("evaluating");
	}

	function submitTypedClarification() {
		const text = typedClarification.trim();
		if (!text) {
			setErrorMessage("Type your clarification first.");
			return;
		}

		if (!sendSocketJson({ type: "typed_clarification", text })) {
			setErrorMessage("The interview socket is not connected.");
			return;
		}

		setTranscript(text);
		setTranscriptKind("clarification");
		setTypedClarification("");
		setErrorMessage("");
		setInfoMessage("Submitting your clarification...");
	}

	function requestNextQuestion() {
		setLastEval(null);
		setTranscript("");
		setTranscriptKind("");
		setTypedAnswer("");
		setErrorMessage("");
		resetClarificationState();
		if (!sendSocketJson({ type: "request_next" })) {
			setErrorMessage("The interview socket is not connected.");
		}
	}

	useEffect(() => {
		return () => {
			roundDoneRef.current = true;
			closeSocket(true);
			stopPlayback();
			stopCaptureStream();
			if (playbackContextRef.current && playbackContextRef.current.state !== "closed") {
				playbackContextRef.current.close().catch(() => {});
				playbackContextRef.current = null;
			}
		};
	}, []);

	const stageKey = roundDone ? "complete" : voiceStep;
	const activeQuestionNumber = totalQuestions > 0 ? Math.min(currentIndex + 1, totalQuestions) : 0;
	const showTechnicalAnswerTranscript = selectedRound === "technical" && transcriptKind === "answer" && Boolean(transcript.trim()) && currentQuestion && !roundDone;
	const selectedRoundStatus = String(roundSession?.status || "").trim().toLowerCase();
	const selectedRoundHasSavedProgress = !roundDone && selectedRoundStatus === "in_progress" && totalQuestions > 0;
	const startLabel = connectionState === "closed"
		? "Reconnect Stream"
		: selectedRoundComplete
			? `Restart ${currentRoundCopy.title}`
			: selectedRoundHasSavedProgress
				? `Resume ${currentRoundCopy.title}`
				: `Start ${currentRoundCopy.title}`;
	const canShowStart = sessionSyncState !== "loading" && sessionId && accessToken && !currentQuestion && !roundDone && (connectionState === "idle" || connectionState === "closed");
	const micStatus = !captureReady
		? { label: "Mic unavailable", tone: "warn" }
		: useTyped
			? { label: inClarificationMode ? "Typed clarification" : "Mic paused", tone: inClarificationMode ? "info" : "warn" }
			: voiceStep === "listening"
				? { label: inClarificationMode ? "Clarification live" : "Mic live", tone: "good" }
				: { label: "Mic waiting", tone: "info" };
	const canEnterClarification = Boolean(currentQuestion)
		&& connectionState === "open"
		&& voiceStep === "listening"
		&& !inClarificationMode
		&& clarificationRemaining > 0;
	const canExitClarification = inClarificationMode && (voiceStep === "listening" || voiceStep === "clarifying");
	const roundButtonsDisabled = connectionState === "connecting" || sessionSyncState === "loading";
	const isDedicatedRoundPage = hideRoundNavigation || Boolean(forcedRound);
	let completionPrimaryTarget = nextPageTarget || (!isDedicatedRoundPage && nextRound ? ROUND_PAGE_TARGETS[nextRound] : "dsa");
	let completionPrimaryLabel = nextPageLabel || (!isDedicatedRoundPage && nextRound ? `Open ${formatInterviewRoundLabel(nextRound)}` : "Open DSA Preview");
	if (!roleRequiresDsa && completionPrimaryTarget === "dsa") {
		completionPrimaryTarget = "project_discussion";
		completionPrimaryLabel = "Open Project Discussion";
	}
	const voiceStepIndex = Math.max(0, VOICE_STEPS.findIndex((step) => step.key === stageKey));

	return (
		<div className="page-shell interview-shell">
			{sessionId && accessToken ? (
				<div className="action-row">
					<WorkflowResetControl
						accessToken={accessToken}
						apiBaseUrl={baseUrl}
						sessionId={sessionId}
						currentTarget={selectedRound}
						currentLabel={currentRoundCopy.title}
						onResetApplied={handleWorkflowReset}
						triggerClassName="secondary-button action-row__button"
						triggerLabel="Reset"
						disabled={roundButtonsDisabled || !sessionId || !accessToken}
					/>
				</div>
			) : null}

			<section className="interview-console interview-console--compact">
				<main className="interview-console__main">
					{!sessionId && (
						<section className="glass-panel interview-notice interview-notice--warning">
							<div>
								<p className="section-kicker">Blocked</p>
								<h2>No active session</h2>
								<p>Complete Resume Intake first.</p>
							</div>
							<button type="button" style={buttonStyle({ color: "#2563eb" })} onClick={() => onNavigate?.("upload")}>
								Go to Resume Intake
							</button>
						</section>
					)}

					{sessionId && !accessToken && (
						<section className="glass-panel interview-notice interview-notice--warning">
							<div>
								<p className="section-kicker">Authentication</p>
								<h2>Sign in required</h2>
								<p>Sign in so the socket can verify session ownership.</p>
							</div>
						</section>
					)}

					{sessionSyncState === "loading" && sessionId && accessToken && (
						<section className="glass-panel interview-notice interview-notice--info">
							<div>
								<p className="section-kicker">Sync</p>
								<h2>Checking saved interview state</h2>
								<p>Loading saved round state.</p>
							</div>
						</section>
					)}

					{canShowStart && (
						<section className="glass-panel interview-launch-card">
							<div>
								<p className="section-kicker">{currentRoundCopy.badge}</p>
								<h2>{currentRoundCopy.startTitle}</h2>
								<p>
									{selectedRoundComplete
										? `${currentRoundCopy.title} is already complete. Starting again restarts from question 1.`
										: selectedRoundHasSavedProgress
											? `Saved progress found at question ${Math.min(currentIndex + 1, totalQuestions)} of ${totalQuestions}.`
											: currentRoundCopy.startDescription}
								</p>
							</div>
							<button
								type="button"
								style={buttonStyle({ color: "#2563eb" })}
								onClick={() => startRound({ forceRestart: selectedRoundComplete })}
								disabled={busyState === "starting"}
							>
								{busyState === "starting"
									? selectedRoundComplete
										? "Restarting round..."
										: "Opening stream..."
									: startLabel}
							</button>
						</section>
					)}

					{connectionState === "connecting" && !currentQuestion && !roundDone && (
						<section className="glass-panel interview-notice interview-notice--info">
							<div>
								<p className="section-kicker">Connection</p>
								<h2>Connecting to the interview stream</h2>
								<p>Preparing audio and opening the socket.</p>
							</div>
						</section>
					)}

					{errorMessage && <p className="error-banner">{errorMessage}</p>}
					{infoMessage && <p className="info-banner">{infoMessage}</p>}

					{selectedRound === "technical" && sessionId && (
						<section className="glass-panel" style={{ display: "grid", gap: 16 }}>
							<div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
								<div>
									<p className="section-kicker">Resume-Conditioned Targeting</p>
									<h2 style={{ margin: 0, fontSize: "1.2rem" }}>Live technical targeting and historical analytics</h2>
								</div>
								<StatusPill
									label={technicalAnalyticsState === "ready" ? "Analytics ready" : technicalAnalyticsState === "error" ? "Analytics unavailable" : "Loading analytics"}
									tone={technicalAnalyticsState === "ready" ? "good" : technicalAnalyticsState === "error" ? "warn" : "info"}
								/>
							</div>

							<div style={{ display: "grid", gap: 14, gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))" }}>
								<article style={paletteCard("indigo")}>
									<p className="section-kicker" style={{ marginBottom: 8 }}>Skill Profile</p>
									<p style={{ margin: "0 0 10px", fontSize: 14, color: "#475569" }}>
										{describeTechnicalTargeting(technicalTargetingState.skillProfileSummary, technicalTargetingState.coveredTopicSummary)}
									</p>
									<div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
										<div>
											<p style={{ margin: 0, fontSize: 12, color: "#64748b" }}>Strong</p>
											<strong>{Number(technicalTargetingState.skillProfileSummary?.strong_count || 0)}</strong>
										</div>
										<div>
											<p style={{ margin: 0, fontSize: 12, color: "#64748b" }}>Familiar</p>
											<strong>{Number(technicalTargetingState.skillProfileSummary?.familiar_count || 0)}</strong>
										</div>
										<div>
											<p style={{ margin: 0, fontSize: 12, color: "#64748b" }}>Absent</p>
											<strong>{Number(technicalTargetingState.skillProfileSummary?.absent_count || 0)}</strong>
										</div>
									</div>

									{toArray(technicalTargetingState.skillProfileSummary?.priority_focus_areas).length ? (
										<div style={{ marginTop: 12 }}>
											<p style={{ margin: "0 0 8px", fontSize: 12, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.08em" }}>Priority focus areas</p>
											<div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
												{toArray(technicalTargetingState.skillProfileSummary?.priority_focus_areas).slice(0, 5).map((skill) => (
													<span key={`priority-${skill}`} style={{ padding: "6px 10px", borderRadius: 999, background: "rgba(37, 99, 235, 0.08)", color: "#1d4ed8", fontSize: 12, fontWeight: 600 }}>
														{skill}
													</span>
												))}
											</div>
										</div>
									) : null}
								</article>

								<article style={paletteCard("green")}>
									<p className="section-kicker" style={{ marginBottom: 8 }}>Current Target</p>
									<p style={{ margin: "0 0 10px", fontSize: 14, color: "#475569" }}>
										{technicalTargetingState.currentPrompt?.focus_skill
											? `${currentQuestion ? "Current" : "Next"} prompt is targeting ${technicalTargetingState.currentPrompt.focus_skill}.`
											: "The current prompt will show its focus skill once the technical batch is available."}
									</p>
									<div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}>
										<div>
											<p style={{ margin: 0, fontSize: 12, color: "#64748b" }}>Prompt tier</p>
											<strong>{formatLabel(technicalTargetingState.currentPrompt?.question_tier || "general")}</strong>
										</div>
										<div>
											<p style={{ margin: 0, fontSize: 12, color: "#64748b" }}>Tracked prompts</p>
											<strong>{Number(technicalTargetingState.coveredTopicSummary?.total_questions_covered || 0)}</strong>
										</div>
									</div>

									<div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(3, minmax(0, 1fr))", marginTop: 12 }}>
										<div>
											<p style={{ margin: 0, fontSize: 12, color: "#64748b" }}>Strong asked</p>
											<strong>{Number(technicalTargetingState.coveredTopicSummary?.tier_counts?.strong || 0)}</strong>
										</div>
										<div>
											<p style={{ margin: 0, fontSize: 12, color: "#64748b" }}>Familiar asked</p>
											<strong>{Number(technicalTargetingState.coveredTopicSummary?.tier_counts?.familiar || 0)}</strong>
										</div>
										<div>
											<p style={{ margin: 0, fontSize: 12, color: "#64748b" }}>Absent asked</p>
											<strong>{Number(technicalTargetingState.coveredTopicSummary?.tier_counts?.absent || 0)}</strong>
										</div>
									</div>

									{technicalTargetingState.coveredTopics.length ? (
										<div style={{ marginTop: 12 }}>
											<p style={{ margin: "0 0 8px", fontSize: 12, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.08em" }}>Asked so far</p>
											<div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
												{technicalTargetingState.coveredTopics.slice(-5).map((entry) => (
													<span
														key={`covered-${entry.question_index}-${entry.focus_skill || entry.question_tier}`}
														style={{
															padding: "6px 10px",
															borderRadius: 999,
															background: String(entry?.question_tier || "") === "absent" ? "rgba(249, 115, 22, 0.12)" : "rgba(15, 118, 110, 0.12)",
															color: String(entry?.question_tier || "") === "absent" ? "#c2410c" : "#0f766e",
															fontSize: 12,
															fontWeight: 600,
														}}
													>
														{formatLabel(entry?.question_tier)}{entry?.focus_skill ? `: ${entry.focus_skill}` : ""}
													</span>
												))}
											</div>
										</div>
									) : null}
								</article>

								<article style={paletteCard("warning")}>
									<p className="section-kicker" style={{ marginBottom: 8 }}>Cross-Session Analytics</p>
									<p style={{ margin: "0 0 10px", fontSize: 14, color: "#475569" }}>
										{technicalAnalytics ? describeAnalyticsScope(technicalAnalytics?.scope) : "Loading saved technical performance patterns."}
									</p>

									{technicalAnalytics && Number(technicalAnalytics?.responses_analyzed || 0) > 0 ? (
										<>
											<div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}>
												<div>
													<p style={{ margin: 0, fontSize: 12, color: "#64748b" }}>Saved sessions</p>
													<strong>{Number(technicalAnalytics?.sessions_analyzed || 0)}</strong>
												</div>
												<div>
													<p style={{ margin: 0, fontSize: 12, color: "#64748b" }}>Saved responses</p>
													<strong>{Number(technicalAnalytics?.responses_analyzed || 0)}</strong>
												</div>
												<div>
													<p style={{ margin: 0, fontSize: 12, color: "#64748b" }}>Avg score</p>
													<strong>{formatPercent(technicalAnalytics?.overall_average_score)}</strong>
												</div>
												<div>
													<p style={{ margin: 0, fontSize: 12, color: "#64748b" }}>Hesitation rate</p>
													<strong>{formatPercent(technicalAnalytics?.overall_hesitation_rate)}</strong>
												</div>
											</div>

											<div style={{ marginTop: 12, display: "grid", gap: 8 }}>
												<p style={{ margin: 0, fontSize: 13, color: "#475569" }}>
													Lowest scoring tier: <strong>{formatLabel(technicalAnalytics?.lowest_scoring_tier?.tier)}</strong> ({formatPercent(technicalAnalytics?.lowest_scoring_tier?.average_score)}, delta {formatSignedDelta(technicalAnalytics?.lowest_scoring_tier?.score_delta_vs_overall)})
												</p>
												<p style={{ margin: 0, fontSize: 13, color: "#475569" }}>
													Highest hesitation tier: <strong>{formatLabel(technicalAnalytics?.highest_hesitation_tier?.tier)}</strong> ({formatPercent(technicalAnalytics?.highest_hesitation_tier?.hesitation_rate)})
												</p>
											</div>

											<TechnicalAnalyticsMiniChart analytics={technicalAnalytics} />

											<div style={{ marginTop: 12, display: "grid", gap: 8 }}>
												{toArray(technicalAnalytics?.tier_stats).map((entry) => (
													<div key={`tier-stat-${entry.tier}`} style={{ display: "grid", gap: 4, padding: "10px 12px", borderRadius: 12, background: "rgba(255,255,255,0.55)" }}>
														<div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
															<strong>{formatLabel(entry.tier)}</strong>
															<span style={{ fontSize: 12, color: "#64748b" }}>{entry.response_count} responses</span>
														</div>
														<div style={{ display: "flex", gap: 10, flexWrap: "wrap", fontSize: 12, color: "#475569" }}>
															<span>Score {formatPercent(entry.average_score)}</span>
															<span>Delta {formatSignedDelta(entry.score_delta_vs_overall)}</span>
															<span>Hesitation {formatPercent(entry.hesitation_rate)}</span>
														</div>
													</div>
												))}
											</div>
										</>
									) : technicalAnalyticsState === "error" ? (
										<p style={{ margin: 0, fontSize: 13, color: "#b45309" }}>{technicalAnalyticsError}</p>
									) : (
										<p style={{ margin: 0, fontSize: 13, color: "#475569" }}>No saved technical responses are available yet for cross-session analytics.</p>
									)}
								</article>
							</div>
						</section>
					)}

					{currentQuestion && !roundDone && (
						<section className="interview-runtime">
							<section className="glass-panel interview-question-card">
								<ProgressBar current={activeQuestionNumber} total={totalQuestions} />
								<div className="interview-question-card__header">
									<p>{currentQuestion.text}</p>
									<StatusPill label={`Q${activeQuestionNumber}/${Math.max(totalQuestions, 1)}`} tone="info" />
								</div>
							</section>

							{showTechnicalAnswerTranscript && (
								<section className="glass-panel interview-transcript-card">
									<div className="interview-feedback-card__header">
										<p>Answer Transcript</p>
										<StatusPill label={voiceStep === "feedback" ? "Saved" : "Live"} tone={voiceStep === "feedback" ? "good" : "info"} />
									</div>
									<p className="interview-feedback-card__body">
										This is the text captured for your current technical answer after speech-to-text, so you can verify what the interviewer is evaluating.
									</p>
									<p>{transcript}</p>
								</section>
							)}

							{(inClarificationMode || clarificationReplies.length > 0) && (
								<section className="glass-panel interview-clarification-panel">
									<div className="interview-clarification-panel__header">
										<div className="interview-clarification-panel__heading">
											<p className="section-kicker">Clarification</p>
											<h2 className="interview-clarification-panel__title">Question clarification</h2>
										</div>
										<StatusPill
											label={inClarificationMode ? `Live · ${clarificationRemaining} left` : `${clarificationReplies.length} reply${clarificationReplies.length === 1 ? "" : "ies"}`}
											tone={inClarificationMode ? "info" : "neutral"}
										/>
									</div>
									<p className="interview-clarification-panel__summary">
										{inClarificationMode
											? useTyped
												? "Type a short doubt about scope or expectations. When you're ready, return to your answer."
												: "Ask a short doubt about scope or expectations. When you're ready, return to your answer."
											: "Clarification replies remain visible while you finish this question."}
									</p>
									{clarificationReplies.length > 0 ? (
										<div className="interview-clarification-panel__replies">
											{clarificationReplies.map((reply, index) => (
												<div
													key={`clarification-reply-${reply.index}-${index}`}
													className="interview-clarification-panel__reply"
												>
													<p className="interview-clarification-panel__reply-label">
														Interviewer reply {reply.index}
													</p>
													<p className="interview-clarification-panel__reply-text">{reply.text}</p>
												</div>
											))}
										</div>
									) : (
										<div className="interview-clarification-panel__empty">
											No clarification replies yet. Ask one short doubt and the interviewer will respond here.
										</div>
									)}
								</section>
							)}

							{voiceStep === "playing" && (
								<section className="glass-panel interview-state-panel interview-state-panel--info">
									<WaveformBar active={isPlaying} />
									<div className="interview-state-panel__copy">
										<p className="interview-state-panel__title">{isPlaying ? "Interviewer speaking" : "Preparing interviewer audio"}</p>
										<p>{isPlaying ? "Wait for the prompt to finish or interrupt it before answering." : "Prompt audio is being prepared."}</p>
									</div>
									<div className="interview-inline-actions">
										<button type="button" style={buttonStyle({ color: "#2563eb", ghost: true })} onClick={interruptPlayback}>
											Interrupt And Answer
										</button>
										<button type="button" style={buttonStyle({ color: "#0f766e", ghost: true })} onClick={() => setUseTyped(true)}>
											Use text instead
										</button>
									</div>
								</section>
							)}

							{voiceStep === "clarifying" && (
								<section className="glass-panel interview-state-panel interview-state-panel--info">
									<WaveformBar active={isPlaying} />
									<div className="interview-state-panel__copy">
										<p className="interview-state-panel__title">{isPlaying ? "Interviewer clarifying your doubt" : "Preparing clarification reply"}</p>
										<p>{isPlaying ? "Listen to the clarification, or interrupt it if you want to ask another short doubt." : "Clarification audio is being prepared."}</p>
									</div>
									<div className="interview-inline-actions">
										<button type="button" style={buttonStyle({ color: "#2563eb", ghost: true })} onClick={interruptPlayback}>
											Interrupt Clarification
										</button>
										{canExitClarification && (
											<button type="button" style={buttonStyle({ color: "#0f766e", ghost: true })} onClick={finishClarificationMode}>
												Done Asking
											</button>
										)}
									</div>
								</section>
							)}

							{voiceStep === "listening" && (
								<section className="glass-panel interview-state-panel interview-state-panel--neutral">
									<WaveformBar active={captureReady && !useTyped} />
									<div className="interview-state-panel__copy">
										<p className="interview-state-panel__title">
											{inClarificationMode
												? useTyped || !captureReady
													? "Typed clarification is active"
													: "Clarification mic is active"
												: captureReady && !useTyped
													? "Live mic is active"
													: "Typed fallback ready"}
										</p>
										<p>
											{inClarificationMode
												? useTyped || !captureReady
													? "Type a short clarification now. Submit it below, or tap Done Asking when you want to answer."
													: "Ask a short clarification question now. Tap Done Asking when you want to answer."
												: captureReady && !useTyped
													? "Speak after the prompt ends. Tap Finish Answer when done."
													: "Use the text box below to answer."}
										</p>
									</div>
									<div className="interview-inline-actions">
										{inClarificationMode ? (
											<button type="button" style={buttonStyle({ color: "#0f766e" })} onClick={finishClarificationMode}>
												Done Asking
											</button>
										) : captureReady && !useTyped && (
											<button type="button" style={buttonStyle({ color: "#16a34a" })} onClick={finishVoiceAnswer}>
												Finish Answer
											</button>
										)}
										{canEnterClarification && (
											<button type="button" style={buttonStyle({ color: "#0f766e", ghost: true })} onClick={startClarificationMode}>
												Ask Doubt
											</button>
										)}
										{captureReady && !useTyped && !inClarificationMode && (
											<button type="button" style={buttonStyle({ color: "#2563eb", ghost: true })} onClick={() => setUseTyped(true)}>
												Use text instead
											</button>
										)}
										{inClarificationMode && !useTyped && captureReady && (
											<button type="button" style={buttonStyle({ color: "#2563eb", ghost: true })} onClick={() => setUseTyped(true)}>
												Type doubt instead
											</button>
										)}
										{inClarificationMode && useTyped && captureReady && (
											<button type="button" style={buttonStyle({ color: "#0f766e", ghost: true })} onClick={() => setUseTyped(false)}>
												Return to live mic
											</button>
										)}
										{captureReady && useTyped && !inClarificationMode && (
											<button type="button" style={buttonStyle({ color: "#0f766e", ghost: true })} onClick={() => setUseTyped(false)}>
												Return to live mic
											</button>
										)}
									</div>
								</section>
							)}

							{voiceStep === "transcribing" && (
								<section className="glass-panel interview-state-panel interview-state-panel--info">
									<div className="interview-state-panel__copy">
										<p className="interview-state-panel__title">Transcribing your answer</p>
										<p>Converting speech to text.</p>
									</div>
								</section>
							)}

							{voiceStep === "evaluating" && (
								<section className="glass-panel interview-state-panel interview-state-panel--info">
									<div className="interview-state-panel__copy">
										<p className="interview-state-panel__title">Evaluating your answer</p>
										<p>Running rubric, similarity, and communication scoring.</p>
									</div>
								</section>
							)}

							{inClarificationMode && useTyped && voiceStep !== "feedback" && voiceStep !== "complete" && (
								<section className="glass-panel interview-typed-panel">
									<p className="section-kicker">Typed clarification</p>
									<textarea
										value={typedClarification}
										onChange={(event) => setTypedClarification(event.target.value)}
										placeholder="Type your clarification question here..."
										rows={4}
										className="interview-typed-panel__textarea"
									/>
									<div className="interview-inline-actions">
										<button type="button" style={buttonStyle({ color: "#2563eb" })} onClick={submitTypedClarification}>
											Submit Clarification
										</button>
										{captureReady && (
											<button type="button" style={buttonStyle({ color: "#0f766e", ghost: true })} onClick={() => setUseTyped(false)}>
												Use live mic
											</button>
										)}
										<button type="button" style={buttonStyle({ color: "#0f766e", ghost: true })} onClick={finishClarificationMode}>
											Done Asking
										</button>
									</div>
								</section>
							)}

							{useTyped && !inClarificationMode && voiceStep !== "feedback" && voiceStep !== "complete" && (
								<section className="glass-panel interview-typed-panel">
									<p className="section-kicker">Typed answer</p>
									<textarea
										value={typedAnswer}
										onChange={(event) => setTypedAnswer(event.target.value)}
										placeholder="Type your answer here..."
										rows={5}
										className="interview-typed-panel__textarea"
									/>
									<div className="interview-inline-actions">
										<button type="button" style={buttonStyle({ color: "#2563eb" })} onClick={submitTypedAnswer}>
											Submit Typed Answer
										</button>
										{captureReady && (
											<button type="button" style={buttonStyle({ color: "#0f766e", ghost: true })} onClick={() => setUseTyped(false)}>
												Use live mic
											</button>
										)}
									</div>
								</section>
							)}

							{voiceStep === "feedback" && lastEval && (
								<section className="glass-panel interview-feedback-card">
									<div className="interview-feedback-card__header">
										<p>Answer Feedback</p>
									</div>
									<p className="interview-feedback-card__body">{lastEval.feedback || lastEval.rubric?.feedback}</p>
									{!roundDone && nextQuestionIndex < totalQuestions && (
										<button type="button" style={buttonStyle({ color: "#16a34a" })} onClick={requestNextQuestion}>
											Next Question
										</button>
									)}
									{!roundDone && nextQuestionIndex >= totalQuestions && <p className="interview-feedback-card__body">Finalizing the round summary...</p>}
								</section>
							)}

							{connectionState === "closed" && !roundDone && (
								<div className="interview-inline-actions">
									<button type="button" style={buttonStyle({ color: "#2563eb" })} onClick={startRound}>
										Reconnect Stream
									</button>
								</div>
							)}
						</section>
					)}

					{roundDone && (
						<section className="glass-panel interview-complete-card">
							<div className="interview-complete-card__header">
								<p>{currentRoundCopy.completionTitle}</p>
							</div>
							{roundFeedback?.coaching_note && (
								<div className="interview-complete-card__coaching">{roundFeedback.coaching_note}</div>
							)}
							{!roundFeedback?.coaching_note && (
								<p className="interview-complete-card__note">This round is complete. Move to the next stage or restart this round if needed.</p>
							)}
							<div className="interview-inline-actions">
								{completionPrimaryTarget && (
									<button type="button" style={buttonStyle({ color: "#2563eb" })} onClick={() => onNavigate?.(completionPrimaryTarget)}>
										{completionPrimaryLabel}
									</button>
								)}
								{allRoundsComplete && completionPrimaryTarget !== "report" && (
									<button type="button" style={buttonStyle({ color: "#0f766e", ghost: true })} onClick={() => onNavigate?.("report")}>
										Open Report Preview
									</button>
								)}
								<button type="button" style={buttonStyle({ color: "#0f766e", ghost: true })} onClick={() => startRound({ forceRestart: true })}>
									Restart {currentRoundCopy.title}
								</button>
							</div>
						</section>
					)}
				</main>
			</section>
		</div>
	);
}