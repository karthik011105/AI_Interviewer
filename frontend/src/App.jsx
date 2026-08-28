import { Suspense, lazy, useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import AuthPanel from "./components/AuthPanel";
import UploadPage from "./pages/UploadPage";
import { roleRequiresDsa } from "./lib/roleFlow";
import { authClient } from "./lib/authClient";

const AssessmentPage = lazy(() => import("./pages/AssessmentPage"));
const DSAPage = lazy(() => import("./pages/DSAPage"));
const InterviewPage = lazy(() => import("./pages/InterviewPage"));
const ReportPage = lazy(() => import("./pages/ReportPage"));

function isLocalDevelopmentHost(hostname) {
	return hostname === "127.0.0.1" || hostname === "localhost";
}



const INITIAL_WORKFLOW_STATE = {
	apiBaseUrl: import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000",
	sessionId: "",
	selectedRoleKey: "",
	selectedRoleTitle: "",
	activeInterviewRound: "technical",
	interviewRoundsDone: {},
	dsaPreviewViewed: false,
	dsaCurrentQuestionNumber: 1,
	dsaRoundCompleted: false,
	resumeParseResult: null,
	resumeSelectionResult: null,
	resumeFileName: "",
	resumeReady: false,
	roleReady: false,
	contextsReady: false,
	assessmentStatus: "idle",
	assessmentAnsweredCount: 0,
	assessmentTotalQuestions: 0,
};

const WORKFLOW_STORAGE_KEY = "ai-interview-simulator.workflow";
const VALID_ASSESSMENT_STATUSES = new Set(["idle", "active", "complete"]);

function normalizeDsaQuestionNumber(value) {
	return Number(value) === 2 ? 2 : 1;
}

function loadPersistedWorkflowState() {
	if (typeof window === "undefined") {
		return {};
	}

	try {
		const rawState = window.localStorage.getItem(WORKFLOW_STORAGE_KEY);
		if (!rawState) {
			return {};
		}

		const parsedState = JSON.parse(rawState);
		if (!parsedState || typeof parsedState !== "object") {
			return {};
		}

		const persistedRoundsDone = parsedState.interviewRoundsDone && typeof parsedState.interviewRoundsDone === "object"
			? parsedState.interviewRoundsDone
			: {};

		return {
			sessionId: typeof parsedState.sessionId === "string" ? parsedState.sessionId : "",
			selectedRoleKey: typeof parsedState.selectedRoleKey === "string" ? parsedState.selectedRoleKey : "",
			selectedRoleTitle: typeof parsedState.selectedRoleTitle === "string" ? parsedState.selectedRoleTitle : "",
			activeInterviewRound: INTERVIEW_ROUND_ORDER.includes(parsedState.activeInterviewRound)
				? parsedState.activeInterviewRound
				: INITIAL_WORKFLOW_STATE.activeInterviewRound,
			interviewRoundsDone: Object.fromEntries(
				INTERVIEW_ROUND_ORDER
					.filter((roundKey) => persistedRoundsDone[roundKey] != null && Number.isFinite(Number(persistedRoundsDone[roundKey])))
					.map((roundKey) => [roundKey, Number(persistedRoundsDone[roundKey])]),
			),
			dsaPreviewViewed: Boolean(parsedState.dsaPreviewViewed),
			dsaCurrentQuestionNumber: normalizeDsaQuestionNumber(parsedState.dsaCurrentQuestionNumber),
			dsaRoundCompleted: Boolean(parsedState.dsaRoundCompleted),
			resumeFileName: typeof parsedState.resumeFileName === "string" ? parsedState.resumeFileName : "",
			resumeReady: Boolean(parsedState.resumeReady),
			roleReady: Boolean(parsedState.roleReady),
			contextsReady: Boolean(parsedState.contextsReady),
			assessmentStatus: VALID_ASSESSMENT_STATUSES.has(parsedState.assessmentStatus)
				? parsedState.assessmentStatus
				: INITIAL_WORKFLOW_STATE.assessmentStatus,
			assessmentAnsweredCount: Number.isFinite(Number(parsedState.assessmentAnsweredCount))
				? Number(parsedState.assessmentAnsweredCount)
				: 0,
			assessmentTotalQuestions: Number.isFinite(Number(parsedState.assessmentTotalQuestions))
				? Number(parsedState.assessmentTotalQuestions)
				: 0,
		};
	} catch {
		return {};
	}
}

function getPersistedWorkflowState(workflowState) {
	return {
		sessionId: String(workflowState?.sessionId || "").trim(),
		selectedRoleKey: String(workflowState?.selectedRoleKey || "").trim(),
		selectedRoleTitle: String(workflowState?.selectedRoleTitle || "").trim(),
		activeInterviewRound: INTERVIEW_ROUND_ORDER.includes(workflowState?.activeInterviewRound)
			? workflowState.activeInterviewRound
			: INITIAL_WORKFLOW_STATE.activeInterviewRound,
		interviewRoundsDone: INTERVIEW_ROUND_ORDER.reduce((accumulator, roundKey) => {
			const score = workflowState?.interviewRoundsDone?.[roundKey];
			if (score != null && Number.isFinite(Number(score))) {
				accumulator[roundKey] = Number(score);
			}
			return accumulator;
		}, {}),
		dsaPreviewViewed: Boolean(workflowState?.dsaPreviewViewed),
		dsaCurrentQuestionNumber: normalizeDsaQuestionNumber(workflowState?.dsaCurrentQuestionNumber),
		dsaRoundCompleted: Boolean(workflowState?.dsaRoundCompleted),
		resumeFileName: String(workflowState?.resumeFileName || "").trim(),
		resumeReady: Boolean(workflowState?.resumeReady),
		roleReady: Boolean(workflowState?.roleReady),
		contextsReady: Boolean(workflowState?.contextsReady),
		assessmentStatus: VALID_ASSESSMENT_STATUSES.has(workflowState?.assessmentStatus)
			? workflowState.assessmentStatus
			: INITIAL_WORKFLOW_STATE.assessmentStatus,
		assessmentAnsweredCount: Number.isFinite(Number(workflowState?.assessmentAnsweredCount))
			? Number(workflowState.assessmentAnsweredCount)
			: 0,
		assessmentTotalQuestions: Number.isFinite(Number(workflowState?.assessmentTotalQuestions))
			? Number(workflowState.assessmentTotalQuestions)
			: 0,
	};
}

function sanitizeWorkflowStateForPublicView(workflowState) {
	return {
		...INITIAL_WORKFLOW_STATE,
		apiBaseUrl: String(workflowState?.apiBaseUrl || INITIAL_WORKFLOW_STATE.apiBaseUrl).trim() || INITIAL_WORKFLOW_STATE.apiBaseUrl,
	};
}

function hasSensitiveWorkflowState(workflowState) {
	return Boolean(
		String(workflowState?.sessionId || "").trim()
		|| String(workflowState?.selectedRoleKey || "").trim()
		|| String(workflowState?.selectedRoleTitle || "").trim()
		|| workflowState?.resumeParseResult
		|| workflowState?.resumeSelectionResult
		|| String(workflowState?.resumeFileName || "").trim()
		|| workflowState?.resumeReady
		|| workflowState?.roleReady
		|| workflowState?.contextsReady
		|| workflowState?.assessmentStatus === "active"
		|| workflowState?.assessmentStatus === "complete"
		|| Number(workflowState?.assessmentAnsweredCount || 0) > 0
		|| Number(workflowState?.assessmentTotalQuestions || 0) > 0
		|| workflowState?.dsaPreviewViewed
		|| workflowState?.dsaRoundCompleted
		|| normalizeDsaQuestionNumber(workflowState?.dsaCurrentQuestionNumber) !== INITIAL_WORKFLOW_STATE.dsaCurrentQuestionNumber
		|| String(workflowState?.activeInterviewRound || INITIAL_WORKFLOW_STATE.activeInterviewRound) !== INITIAL_WORKFLOW_STATE.activeInterviewRound
		|| Object.keys(workflowState?.interviewRoundsDone || {}).length > 0
	);
}

const APP_PAGES = [
	{
		key: "upload",
		path: "/resume-intake",
		label: "Resume Intake",
		meta: "Live now",
		iconKey: "resume",
		shellTitle: "Resume Intake Workspace",
		shellSummary: "Upload resume. Lock role.",
		docTitle: "Resume Intake",
		railSummary: "Start here",
		lockedSummary: "Sign in to upload a resume, persist parsed data, and attach the selected role to your account.",
		requiresAuth: true,
		component: UploadPage,
	},
	{
		key: "assessment",
		path: "/assessment",
		label: "Assessment",
		meta: "Live now",
		iconKey: "assessment",
		shellTitle: "Assessment Workspace",
		shellSummary: "Run the MCQ screen.",
		docTitle: "Assessment",
		railSummary: "MCQ round",
		lockedSummary: "Sign in to start the MCQ batch, resume saved answers, and keep the score linked to your session.",
		requiresAuth: true,
		component: AssessmentPage,
	},
	{
		key: "technical",
		path: "/technical-interview",
		label: "Technical",
		meta: "Live now",
		iconKey: "interview",
		shellTitle: "Technical Interview Workspace",
		shellSummary: "Role-specific technical screen.",
		docTitle: "Technical Interview",
		railSummary: "Technical",
		lockedSummary: "Sign in to run the live technical round against your owned interview session.",
		requiresAuth: true,
		component: InterviewPage,
		componentProps: {
			forcedRound: "technical",
			hideRoundNavigation: true,
			nextPageTarget: "dsa",
			nextPageLabel: "Open DSA Workspace",
		},
	},
	{
		key: "dsa",
		path: "/dsa",
		label: "DSA",
		meta: "Live now",
		iconKey: "code",
		shellTitle: "DSA Round Workspace",
		shellSummary: "Run the coding round.",
		docTitle: "DSA Round",
		railSummary: "DSA",
		lockedSummary: "Sign in to run the DSA round against your owned interview session.",
		requiresAuth: true,
		component: DSAPage,
	},
	{
		key: "project_discussion",
		path: "/project-discussion",
		label: "Project Discussion",
		meta: "Live now",
		iconKey: "interview",
		shellTitle: "Project Discussion Workspace",
		shellSummary: "Project deep dive.",
		docTitle: "Project Discussion",
		railSummary: "Projects",
		lockedSummary: "Sign in to run the live project discussion against your owned interview session.",
		requiresAuth: true,
		component: InterviewPage,
		componentProps: {
			forcedRound: "project_discussion",
			hideRoundNavigation: true,
			nextPageTarget: "hr",
			nextPageLabel: "Open HR Interview",
			requiresDsaPreview: true,
			dsaLockedTarget: "dsa",
			dsaLockedLabel: "Open DSA Workspace",
			dsaLockedMessage: "Complete the DSA round first to keep the requested page order before Project Discussion.",
		},
	},
	{
		key: "hr",
		path: "/hr-interview",
		label: "HR",
		meta: "Live now",
		iconKey: "interview",
		shellTitle: "HR Interview Workspace",
		shellSummary: "Final conversation.",
		docTitle: "HR Interview",
		railSummary: "HR",
		lockedSummary: "Sign in to run the live HR round against your owned interview session.",
		requiresAuth: true,
		component: InterviewPage,
		componentProps: {
			forcedRound: "hr",
			hideRoundNavigation: true,
			nextPageTarget: "report",
			nextPageLabel: "Open Report Preview",
		},
	},
	{
		key: "report",
		path: "/report",
		label: "Report",
		meta: "Preview",
		iconKey: "report",
		shellTitle: "Final Report Preview",
		shellSummary: "Results and coaching.",
		docTitle: "Report Preview",
		railSummary: "Summary",
		lockedSummary: "Sign in to inspect the report preview and the scoring inputs that will eventually power it.",
		requiresAuth: true,
		component: ReportPage,
	},
];

const INTERVIEW_ROUND_ORDER = ["technical", "project_discussion", "hr"];

function countCompletedInterviewRounds(workflowState) {
	const roundsDone = workflowState?.interviewRoundsDone || {};
	return INTERVIEW_ROUND_ORDER.filter((roundKey) => roundsDone[roundKey] != null).length;
}

function areAllInterviewRoundsComplete(workflowState) {
	return countCompletedInterviewRounds(workflowState) === INTERVIEW_ROUND_ORDER.length;
}

function resolvePageFromPath(pathname) {
	return APP_PAGES.find((page) => pathname === page.path) ?? APP_PAGES[0];
}

function resolvePageTarget(value) {
	return APP_PAGES.find((page) => page.key === value || page.path === value) ?? APP_PAGES[0];
}

function formatRouteStep(index) {
	return String(index + 1).padStart(2, "0");
}

function RouteIcon({ iconKey }) {
	if (iconKey === "resume") {
		return (
			<svg viewBox="0 0 24 24" aria-hidden="true">
				<path d="M7 3.75h7l4.25 4.25V20.25H7z" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
				<path d="M14 3.75v4.5h4.5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
				<path d="M9.25 12h6.5M9.25 15.5h6.5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
			</svg>
		);
	}

	if (iconKey === "assessment") {
		return (
			<svg viewBox="0 0 24 24" aria-hidden="true">
				<path d="M5 6.75h14v10.5H5z" fill="none" stroke="currentColor" strokeWidth="1.7" rx="2" />
				<path d="M8.5 10.5l2.2 2.2 4.8-4.8" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
			</svg>
		);
	}

	if (iconKey === "interview") {
		return (
			<svg viewBox="0 0 24 24" aria-hidden="true">
				<path d="M12 4.75a3 3 0 0 1 3 3v4.5a3 3 0 0 1-6 0v-4.5a3 3 0 0 1 3-3z" fill="none" stroke="currentColor" strokeWidth="1.7" />
				<path d="M6.75 11.5a5.25 5.25 0 1 0 10.5 0M12 16.75v2.5M9.25 19.25h5.5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
			</svg>
		);
	}

	if (iconKey === "code") {
		return (
			<svg viewBox="0 0 24 24" aria-hidden="true">
				<path d="M9 8.25L5.25 12 9 15.75M15 8.25L18.75 12 15 15.75M13.5 6.75l-3 10.5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
			</svg>
		);
		}

	return (
		<svg viewBox="0 0 24 24" aria-hidden="true">
			<path d="M7 5.75h10a1.5 1.5 0 0 1 1.5 1.5v9.5a1.5 1.5 0 0 1-1.5 1.5H7a1.5 1.5 0 0 1-1.5-1.5v-9.5A1.5 1.5 0 0 1 7 5.75z" fill="none" stroke="currentColor" strokeWidth="1.7" />
			<path d="M8.75 10h6.5M8.75 13h4.5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
		</svg>
	);
}

function getRouteState(page, pageIndex, currentPageIndex, workflowState, requiresDsa) {
	const hasSession = Boolean(workflowState.sessionId);
	const hasRole = Boolean(workflowState.roleReady || workflowState.selectedRoleKey);
	const assessmentActive = workflowState.assessmentStatus === "active";
	const assessmentComplete = workflowState.assessmentStatus === "complete";
	const roundsDone = workflowState?.interviewRoundsDone || {};
	const technicalComplete = roundsDone.technical != null;
	const projectComplete = roundsDone.project_discussion != null;
	const hrComplete = roundsDone.hr != null;
	const dsaRoundCompleted = Boolean(workflowState?.dsaRoundCompleted);

	if (page.key === "upload") {
		if (hasRole) {
			return "complete";
		}
		if (hasSession || workflowState.resumeReady || currentPageIndex === pageIndex) {
			return "active";
		}
		return "pending";
	}

	if (page.key === "assessment") {
		if (assessmentComplete) {
			return "complete";
		}
		if (assessmentActive) {
			return pageIndex === currentPageIndex ? "active" : "ready";
		}
		if (hasRole) {
			return pageIndex === currentPageIndex ? "active" : "ready";
		}
		return "pending";
	}

	if (page.key === "technical") {
		if (pageIndex === currentPageIndex) {
			return "active";
		}
		if (technicalComplete) {
			return "complete";
		}
		return hasSession ? "ready" : "pending";
	}

	if (page.key === "dsa") {
		if (pageIndex === currentPageIndex) {
			return "active";
		}
		if (dsaRoundCompleted) {
			return "complete";
		}
		return hasSession ? "ready" : "pending";
	}

	if (page.key === "project_discussion") {
		if (pageIndex === currentPageIndex) {
			return "active";
		}
		if (projectComplete) {
			return "complete";
		}
		return hasSession ? "ready" : "pending";
	}

	if (page.key === "hr") {
		if (pageIndex === currentPageIndex) {
			return "active";
		}
		if (hrComplete) {
			return "complete";
		}
		return hasSession ? "ready" : "pending";
	}

	if (pageIndex === currentPageIndex) {
		return "active";
	}

	return hasSession ? "ready" : "pending";
}

function getRouteMetaLabel(page, routeState, workflowState, requiresDsa) {
	const roundsDone = workflowState?.interviewRoundsDone || {};
	const technicalComplete = roundsDone.technical != null;
	const projectComplete = roundsDone.project_discussion != null;
	const hrComplete = roundsDone.hr != null;
	const dsaPreviewViewed = Boolean(workflowState?.dsaPreviewViewed);
	const dsaRoundCompleted = Boolean(workflowState?.dsaRoundCompleted);
	const dsaCurrentQuestionNumber = normalizeDsaQuestionNumber(workflowState?.dsaCurrentQuestionNumber);

	if (page.key === "upload") {
		return workflowState.roleReady ? "Ready" : workflowState.resumeReady ? "In progress" : page.meta;
	}

	if (page.key === "assessment") {
		if (workflowState.assessmentStatus === "complete") {
			return "Complete";
		}
		if (workflowState.assessmentStatus === "active") {
			return `${workflowState.assessmentAnsweredCount}/${workflowState.assessmentTotalQuestions || 0} done`;
		}
		return routeState === "ready" ? "Ready" : page.meta;
	}

	if (page.key === "technical") {
		if (technicalComplete) {
			return "Complete";
		}
		return routeState === "ready" ? "Ready" : page.meta;
	}

	if (page.key === "dsa") {
		if (!requiresDsa) {
			return "Skipped";
		}
		if (dsaRoundCompleted) {
			return "Complete";
		}
		if (dsaPreviewViewed) {
			return `Q${dsaCurrentQuestionNumber}/2`;
		}
		return routeState === "ready" ? "Ready" : page.meta;
	}

	if (page.key === "project_discussion") {
		if (projectComplete) {
			return "Complete";
		}
		return routeState === "ready" ? "Ready" : page.meta;
	}

	if (page.key === "hr") {
		if (hrComplete) {
			return "Complete";
		}
		return routeState === "ready" ? "Ready" : page.meta;
	}

	return routeState === "ready" ? "Ready" : page.meta;
}

export default function App() {
	const location = useLocation();
	const navigate = useNavigate();
	const [workflowState, setWorkflowState] = useState(() => ({
		...INITIAL_WORKFLOW_STATE,
		...loadPersistedWorkflowState(),
	}));
	const [authState, setAuthState] = useState({
		status: "loading",
		user: null,
		accessToken: "",
		supabaseConfigured: true,
		busyAction: "idle",
		infoMessage: "",
		errorMessage: "",
	});
	const currentPage = resolvePageFromPath(location.pathname);
	const isStandaloneView = false;
	const isAuthenticated = authState.status === "authenticated";
	const visibleWorkflowState = isAuthenticated
		? workflowState
		: sanitizeWorkflowStateForPublicView(workflowState);
	const selectedRoleRequiresDsa = roleRequiresDsa(visibleWorkflowState?.selectedRoleKey);
	const workflowPages = selectedRoleRequiresDsa
		? APP_PAGES
		: APP_PAGES.filter((page) => page.key !== "dsa");
	const workflowCurrentPageKey = !selectedRoleRequiresDsa && currentPage.key === "dsa"
		? "project_discussion"
		: currentPage.key;
	const currentPageIndex = Math.max(
		0,
		workflowPages.findIndex((page) => page.key === workflowCurrentPageKey),
	);
	const workflowItems = workflowPages.map((page, index) => {
		const routeState = getRouteState(page, index, currentPageIndex, visibleWorkflowState, selectedRoleRequiresDsa);
		return {
			page,
			index,
			routeState,
			routeMetaLabel: getRouteMetaLabel(page, routeState, visibleWorkflowState, selectedRoleRequiresDsa),
		};
	});
	const completedWorkflowCount = workflowItems.filter((item) => item.routeState === "complete").length;
	const currentRoleLabel = visibleWorkflowState?.selectedRoleTitle || "Role pending";
	const hasLiveSession = Boolean(String(visibleWorkflowState?.sessionId || "").trim());
	const sessionSummaryLabel = hasLiveSession
		? `${String(visibleWorkflowState.sessionId).slice(0, 8)}...`
		: "Create in Resume Intake";
	const assessmentSummaryLabel = visibleWorkflowState?.assessmentStatus === "complete"
		? "Assessment done"
		: visibleWorkflowState?.assessmentStatus === "active"
			? `${visibleWorkflowState.assessmentAnsweredCount || 0}/${visibleWorkflowState.assessmentTotalQuestions || 0} answered`
			: "Assessment pending";
	const interviewSummaryLabel = `${countCompletedInterviewRounds(visibleWorkflowState)}/${INTERVIEW_ROUND_ORDER.length} live rounds`;
	const workflowPositionLabel = `Step ${formatRouteStep(currentPageIndex)} of ${formatRouteStep(workflowItems.length - 1)}`;

	useEffect(() => {
		if (typeof window === "undefined") {
			return;
		}

		try {
			window.localStorage.setItem(
				WORKFLOW_STORAGE_KEY,
				JSON.stringify(getPersistedWorkflowState(workflowState)),
			);
		} catch {
			// Ignore storage failures and keep the in-memory workflow state.
		}
	}, [workflowState]);

	useEffect(() => {
		if (authState.status !== "anonymous" && authState.status !== "disabled") {
			return;
		}

		setWorkflowState((current) => (
			hasSensitiveWorkflowState(current)
				? sanitizeWorkflowStateForPublicView(current)
				: current
		));
	}, [authState.status]);

	useEffect(() => {
		if (typeof window !== "undefined") {
			window.scrollTo({ top: 0, left: 0, behavior: "auto" });
		}
	}, [location.pathname]);

	useEffect(() => {
		if (typeof document !== "undefined") {
			document.title = `${currentPage.docTitle} | AI Interview Simulator`;
		}
	}, [currentPage.docTitle]);

	useEffect(() => {
		let active = true;

		function syncAuthSession(session) {
			if (!active) {
				return;
			}
			setAuthState((current) => ({
				...current,
				status: session?.user ? "authenticated" : "anonymous",
				user: session?.user ?? null,
				accessToken: session?.access_token ?? "",
				busyAction: "idle",
			}));
		}

		authClient.getSession().then(({ data }) => {
			syncAuthSession(data.session);
		});

		return () => {
			active = false;
		};
	}, []);

	async function handleSignIn(credentials) {
		setAuthState((current) => ({
			...current,
			busyAction: "sign-in",
			errorMessage: "",
			infoMessage: "",
		}));
		try {
			await authClient.login(credentials.email, credentials.password);
			const { data } = await authClient.getSession();
			setAuthState((current) => ({
				...current,
				status: "authenticated",
				user: data.session?.user,
				accessToken: data.session?.access_token,
				busyAction: "idle",
				infoMessage: "Signed in. Resume uploads and assessment progress will now be owned by this account.",
			}));
		} catch (error) {
			setAuthState((current) => ({
				...current,
				busyAction: "idle",
				errorMessage: error.message,
				infoMessage: "",
			}));
		}
	}

	async function handleEmailContinue({ email }) {
		setAuthState((current) => ({
			...current,
			errorMessage: "Email Link (passwordless) login is not supported in custom MongoDB auth.",
		}));
	}

	async function handleForgotPassword({ email }) {
		setAuthState((current) => ({
			...current,
			errorMessage: "Password recovery is not implemented in custom MongoDB auth.",
		}));
	}

	async function handleSignUp(credentials) {
		setAuthState((current) => ({
			...current,
			busyAction: "sign-up",
			errorMessage: "",
			infoMessage: "",
		}));
		try {
			await authClient.signup(credentials.email, credentials.password);
			const { data } = await authClient.getSession();
			setAuthState((current) => ({
				...current,
				status: "authenticated",
				user: data.session?.user,
				accessToken: data.session?.access_token,
				busyAction: "idle",
				infoMessage: "Account created and signed in.",
			}));
		} catch (error) {
			setAuthState((current) => ({
				...current,
				busyAction: "idle",
				errorMessage: error.message,
				infoMessage: "",
			}));
		}
	}

	async function handleSignOut() {
		setAuthState((current) => ({
			...current,
			busyAction: "sign-out",
			errorMessage: "",
		}));
		authClient.logout();
		setWorkflowState((current) => ({
			...current,
			sessionId: "",
			selectedRoleKey: "",
			selectedRoleTitle: "",
			activeInterviewRound: "technical",
			interviewRoundsDone: {},
			dsaPreviewViewed: false,
			dsaCurrentQuestionNumber: 1,
			dsaRoundCompleted: false,
			resumeParseResult: null,
			resumeSelectionResult: null,
			resumeFileName: "",
			resumeReady: false,
			roleReady: false,
			contextsReady: false,
			assessmentStatus: "idle",
			assessmentAnsweredCount: 0,
			assessmentTotalQuestions: 0,
		}));
		if (typeof window !== "undefined") {
			window.localStorage.removeItem(WORKFLOW_STORAGE_KEY);
		}
		setAuthState((current) => ({
			...current,
			status: "anonymous",
			user: null,
			accessToken: "",
			passwordRecoveryReady: false,
			busyAction: "idle",
			errorMessage: "",
			infoMessage: "Signed out. Sign back in before starting another interview if you want that session owned by your account.",
		}));
		navigate(APP_PAGES[0].path, { replace: true });
	}

	function navigateToPage(value) {
		const targetValue = !selectedRoleRequiresDsa && value === "dsa" ? "project_discussion" : value;
		const targetPage = resolvePageTarget(targetValue);
		navigate(targetPage.path);
	}

	function renderLockedRoute(page) {
		return (
			<section className="page-shell app-entry">
				<div className="app-entry__auth">
					<AuthPanel
						authState={authState}
						onEmailContinue={handleEmailContinue}
						onForgotPassword={handleForgotPassword}
						onSignIn={handleSignIn}
						onSignUp={handleSignUp}
						onSignOut={handleSignOut}
					/>
				</div>
				<section className="glass-panel app-gate">
					<div className="panel-head panel-head--tight">
						<div>
							<p className="section-kicker">{page.label}</p>
							<h2>Sign in to open this stage.</h2>
						</div>
						<span className="status-pill status-pill--checking">Protected route</span>
					</div>
					<p className="hero-text">{page.lockedSummary}</p>
					<div className="hero-tags">
						<span>{page.meta}</span>
						<span>Account required</span>
						<span>Session-safe</span>
					</div>
				</section>
			</section>
		);
	}

	function renderRouteLoading(page) {
		return (
			<section className="page-shell app-entry">
				<section className="glass-panel app-gate" aria-live="polite" aria-busy="true">
					<div className="panel-head panel-head--tight">
						<div>
							<p className="section-kicker">{page.label}</p>
							<h2>Loading stage workspace.</h2>
						</div>
						<span className="status-pill status-pill--checking">Loading</span>
					</div>
					<p className="hero-text">Preparing the {page.label.toLowerCase()} surface and its saved session context.</p>
				</section>
			</section>
		);
	}

	return (
		<div className="app-shell">
			<div className="ambient ambient-one" />
			<div className="ambient ambient-two" />
			<div className="ambient ambient-three" />
			<div className={`app-frame ${isStandaloneView ? "app-frame--standalone" : ""}`}>
				{!isStandaloneView ? (
					<aside className="app-sidebar glass-panel">
						<div className="app-sidebar__brand">
							<p className="section-kicker">AI Interview Simulator</p>
							<div className="app-sidebar__brand-row">
								<span className={`app-brand__icon app-brand__icon--${currentPage.key}`}>
									<RouteIcon iconKey={currentPage.iconKey} />
								</span>
								<div className="app-sidebar__brand-copy">
									<h2>Interview Console</h2>
									<p>Structured simulation flow for fresher hiring.</p>
								</div>
							</div>
							<div className="hero-tags app-sidebar__brand-tags">
								<span>{selectedRoleRequiresDsa ? "DSA path" : "No DSA path"}</span>
								<span>{isAuthenticated ? "Saved workspace" : "Preview mode"}</span>
							</div>
						</div>

						<section className="app-sidebar__workflow" aria-label="Current route and workflow progress">
							<div className="app-sidebar__workflow-head">
								<div>
									<p className="section-kicker">Workflow</p>
									<h3>{workflowPositionLabel}</h3>
								</div>
								<span className={`status-pill status-pill--${completedWorkflowCount === workflowItems.length ? "online" : "checking"}`}>
									{completedWorkflowCount}/{workflowItems.length} complete
								</span>
							</div>
							<ol className="route-progress route-progress--stacked">
								{workflowItems.map(({ page, index, routeState, routeMetaLabel }) => (
									<li key={page.key} className={`route-progress__item route-progress__item--${routeState}`}>
										<NavLink to={page.path} className="route-progress__link">
											<span className="route-progress__step">{formatRouteStep(index)}</span>
											<span className={`route-progress__icon route-progress__icon--${page.key}`}>
												<RouteIcon iconKey={page.iconKey} />
											</span>
											<span className="route-progress__copy">
												<strong>{page.label}</strong>
												<span>{routeMetaLabel}</span>
											</span>
										</NavLink>
									</li>
								))}
							</ol>
						</section>

						<div className="app-sidebar__footer">
							{isAuthenticated ? (
								<div className="app-sidebar__auth">
									<AuthPanel
										authState={authState}
										onEmailContinue={handleEmailContinue}
										onForgotPassword={handleForgotPassword}
										onSignIn={handleSignIn}
										onSignUp={handleSignUp}
										onSignOut={handleSignOut}
										compact
									/>
								</div>
							) : (
								<section className="app-sidebar__guest">
									<p className="section-kicker">Account</p>
									<strong>{authState.supabaseConfigured ? "Guest preview" : "Auth unavailable"}</strong>
									<p>
										{authState.supabaseConfigured
											? "Sign in to own sessions and reports."
											: "Configure Supabase to unlock saved sessions."}
									</p>
								</section>
							)}
						</div>
					</aside>
				) : null}

				<div className={`app-stage ${isStandaloneView ? "app-stage--standalone" : ""}`}>
					{!isStandaloneView ? (
						<section className="app-toolbar glass-panel">
							<div className="app-toolbar__copy">
								<p className="section-kicker">Current stage</p>
								<h2>{currentPage.label}</h2>
								<p className="app-toolbar__summary">{currentPage.shellSummary}</p>
							</div>
							<div className="app-toolbar__aside">
								<div className="hero-tags app-toolbar__chips">
									<span>{workflowPositionLabel}</span>
									<span>{currentPage.meta}</span>
									<span>{currentPage.railSummary}</span>
								</div>
								<div className="app-toolbar__metrics">
									<article className="app-toolbar__metric">
										<span>Role</span>
										<strong>{currentRoleLabel}</strong>
										<p>{selectedRoleRequiresDsa ? "Technical plus DSA path." : "Technical-only path."}</p>
									</article>
									<article className="app-toolbar__metric">
										<span>Session</span>
										<strong>{hasLiveSession ? "Live" : "Pending"}</strong>
										<p>{sessionSummaryLabel}</p>
									</article>
									<article className="app-toolbar__metric">
										<span>Assessment</span>
										<strong>{assessmentSummaryLabel}</strong>
										<p>{interviewSummaryLabel}</p>
									</article>
								</div>
							</div>
						</section>
					) : null}

					<main className="app-content">
					<Routes>
						<Route path="/" element={<Navigate to={APP_PAGES[0].path} replace />} />
						<Route path="/interview" element={<Navigate to="/technical-interview" replace />} />

						{APP_PAGES.map((page) => {
							const PageComponent = page.component;
							return (
								<Route
									key={page.key}
									path={page.path}
									element={
										page.requiresAuth && !isAuthenticated ? (
											renderLockedRoute(page)
										) : (
											<Suspense fallback={renderRouteLoading(page)}>
												<PageComponent
													authState={authState}
													workflowState={visibleWorkflowState}
													onWorkflowStateChange={setWorkflowState}
													onNavigate={navigateToPage}
													roleRequiresDsa={selectedRoleRequiresDsa}
													{...(page.componentProps || {})}
												/>
											</Suspense>
										)
									}
								/>
							);
						})}
						<Route path="*" element={<Navigate to={APP_PAGES[0].path} replace />} />
					</Routes>
					</main>
				</div>
			</div>
		</div>
	);
}