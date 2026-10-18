"""Batch question generation for HR, technical, and project discussion rounds.

All three round types are generated in a single Groq call per round.
Once generated, the batch is cached in the interview_round_sessions table so
a reconnect never triggers a new Groq call.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from backend.config import GroqSettings, get_settings
from backend.nlp.groq_client import (
	GroqCompletionError,
	GroqDependencyError,
	create_chat_completion,
)

_LOGGER = logging.getLogger(__name__)

_JSON_FENCE = re.compile(
	r"```(?:json)?\s*(?P<body>\{.*?\}|\[.*?\])\s*```",
	re.DOTALL,
)

# ---------------------------------------------------------------------------
# Public TypedDicts
# ---------------------------------------------------------------------------


class QuestionItem(TypedDict):
	"""One question with evaluation metadata."""

	question: str
	ideal_points: list[str]
	follow_up: str
	difficulty: str  # easy | medium | hard


class ProjectQuestionSet(TypedDict):
	"""Questions for one project from the candidate's resume."""

	project_title: str
	questions: list[QuestionItem]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class QuestionGeneratorError(RuntimeError):
	"""Base error for question generation failures."""


class QuestionGeneratorLlmError(QuestionGeneratorError):
	"""Raised when Groq returns an invalid or unparseable response."""


class QuestionGeneratorUnavailableError(QuestionGeneratorError):
	"""Raised when Groq is not configured."""


# ---------------------------------------------------------------------------
# Role → topic map (19 roles)
# ---------------------------------------------------------------------------

_ROLE_TOPIC_MAP: dict[str, list[str]] = {
	"software_engineer": [
		"Object-oriented principles and class relationships",
		"Data structures and algorithms",
		"REST API concepts and trade-offs",
		"Concurrency and threading",
		"Basic system components and trade-offs",
		"Testing and debugging",
	],
	"frontend_developer": [
		"HTML / CSS layout and box model",
		"JavaScript DOM manipulation and events",
		"React component lifecycle",
		"State management patterns",
		"Browser performance and rendering",
		"Accessibility and responsive design",
	],
	"backend_developer": [
		"REST and GraphQL concepts and trade-offs",
		"Database modelling and SQL",
		"Authentication and authorisation",
		"Caching strategies",
		"Message queues and async processing",
		"Logging and observability",
	],
	"full_stack_developer": [
		"Client-server architecture",
		"REST API concepts and integration",
		"Database modelling concepts",
		"Authentication flows (JWT / session)",
		"Frontend state management",
		"Deployment and CI/CD basics",
	],
	"data_analyst": [
		"SQL aggregations and window functions",
		"Pandas data wrangling",
		"Data cleaning and outlier handling",
		"Descriptive statistics",
		"Matplotlib / Seaborn visualisation",
		"Dashboard design principles",
	],
	"data_engineer": [
		"ETL pipeline design",
		"SQL and query optimisation",
		"Batch vs stream processing",
		"Data warehouse concepts (star schema)",
		"Apache Spark fundamentals",
		"Data quality and lineage",
	],
	"machine_learning_engineer": [
		"Supervised vs unsupervised learning",
		"Model training and evaluation metrics",
		"Feature engineering",
		"Overfitting and regularisation",
		"Neural network fundamentals",
		"ML deployment and monitoring",
	],
	"ai_engineer": [
		"Large language model fundamentals",
		"Prompt engineering strategies",
		"Retrieval-augmented generation (RAG)",
		"Embedding models and vector databases",
		"LangChain / LlamaIndex patterns",
		"AI safety and hallucination mitigation",
	],
	"data_scientist": [
		"Statistical hypothesis testing",
		"Regression and classification",
		"Experimental design and A/B testing",
		"Feature selection",
		"Ensemble methods",
		"Communicating results to non-technical audiences",
	],
	"devops_engineer": [
		"CI/CD pipeline design",
		"Docker containerisation",
		"Linux system administration",
		"Infrastructure as Code (Terraform basics)",
		"Monitoring and alerting",
		"Git branching strategies",
	],
	"cloud_engineer": [
		"Cloud service models (IaaS / PaaS / SaaS)",
		"AWS / GCP core services (compute, storage, networking)",
		"IAM and least-privilege security",
		"Auto-scaling and load balancing",
		"Cost optimisation strategies",
		"Disaster recovery and high availability",
	],
	"site_reliability_engineer": [
		"Service level indicators and objectives (SLI / SLO / SLA)",
		"Monitoring, alerting, and observability fundamentals",
		"Incident response, postmortems, and reliability practices",
		"Linux systems, processes, and networking basics",
		"Capacity planning, autoscaling, and load balancing",
		"Automation, runbooks, and production readiness",
	],
	"mobile_developer": [
		"Mobile app lifecycle (Android / iOS)",
		"UI layout and responsive design",
		"State management in mobile",
		"Network calls and offline handling",
		"Push notifications",
		"App performance profiling",
	],
	"embedded_systems_engineer": [
		"Microcontroller architecture and peripherals",
		"Real-time operating systems (RTOS)",
		"Interrupt handling and timing",
		"Memory-constrained programming",
		"Serial communication protocols (UART / SPI / I2C)",
		"Power management",
	],
	"iot_engineer": [
		"Sensor integration and device data acquisition",
		"Device-to-cloud protocols (MQTT / CoAP / HTTP)",
		"Microcontrollers, peripherals, and edge-device constraints",
		"Serial protocols and hardware interfaces (UART / SPI / I2C)",
		"Connectivity, reliability, and offline behaviour in connected devices",
		"Power management and secure device communication",
	],
	"robotics_software_engineer": [
		"Robot kinematics and coordinate frames",
		"Feedback control loops and actuator/sensor coordination",
		"Path planning and motion planning basics",
		"Sensor fusion and localisation concepts",
		"ROS nodes, topics, and robot software integration",
		"Real-time constraints and safety in robotic systems",
	],
	"qa_automation_engineer": [
		"Test pyramid (unit / integration / E2E)",
		"Writing effective test cases",
		"Selenium / Playwright automation",
		"Test-driven development (TDD)",
		"Bug reporting and reproduction",
		"CI integration for automated tests",
	],
	"cybersecurity_analyst": [
		"OWASP Top 10 vulnerabilities",
		"Network security fundamentals (TCP/IP, firewalls)",
		"Authentication attacks (SQL injection, XSS)",
		"Encryption and PKI basics",
		"Incident response process",
		"Security auditing and logging",
	],
	"network_engineer": [
		"OSI model layers",
		"IP addressing and subnetting",
		"Routing protocols (OSPF, BGP basics)",
		"VLAN and switching",
		"Firewall and ACL configuration",
		"Network troubleshooting tools",
	],
	"vlsi_design_engineer": [
		"Digital logic design and Boolean reasoning",
		"RTL design in Verilog or VHDL",
		"Finite-state machines and synchronous design",
		"Timing analysis, setup/hold, and clock-domain basics",
		"ASIC / FPGA design flow and synthesis fundamentals",
		"Verification, testbenches, and waveform-based debugging",
	],
	"firmware_engineer": [
		"Boot flow, startup code, and hardware bring-up",
		"Interrupts, timers, and deterministic execution",
		"Memory-mapped I/O, registers, and low-level debugging",
		"Device drivers and peripheral control",
		"Serial protocols and board-level communication",
		"Resource-constrained C/C++ programming and fault handling",
	],
	"business_analyst": [
		"Requirements elicitation techniques",
		"Use case and user story writing",
		"Process mapping (BPMN basics)",
		"Stakeholder communication",
		"SQL for business reporting",
		"Agile methodology",
	],
	"product_manager": [
		"Product roadmap and prioritisation (MoSCoW, RICE)",
		"User research and persona development",
		"Metrics and KPIs",
		"Agile / Scrum ceremonies",
		"Competitive analysis",
		"Cross-functional collaboration",
	],
	"ui_ux_designer": [
		"User-centred design process",
		"Wireframing and prototyping",
		"Usability heuristics (Nielsen's 10)",
		"Information architecture",
		"Accessibility standards (WCAG)",
		"Design system fundamentals",
	],
}

_FALLBACK_TOPICS = [
	"Programming language fundamentals",
	"Data structures and algorithmic reasoning",
	"Database and SQL fundamentals",
	"API and client-server fundamentals",
	"Debugging, testing, and code quality",
	"Time and space complexity basics",
]

_ROLE_TOPIC_ALIASES: dict[str, str] = {
	"backend_python_developer": "backend_developer",
	"backend_java_developer": "backend_developer",
	"backend_node_developer": "backend_developer",
	"frontend_react_developer": "frontend_developer",
	"mobile_app_developer": "mobile_developer",
}

_ROLE_TOPIC_FAMILIES: tuple[tuple[tuple[str, ...], str], ...] = (
	(("software", "engineer"), "software_engineer"),
	(("backend",), "backend_developer"),
	(("frontend",), "frontend_developer"),
	(("react",), "frontend_developer"),
	(("full", "stack"), "full_stack_developer"),
	(("data", "analyst"), "data_analyst"),
	(("data", "engineer"), "data_engineer"),
	(("machine", "learning"), "machine_learning_engineer"),
	(("ai",), "ai_engineer"),
	(("data", "scientist"), "data_scientist"),
	(("devops",), "devops_engineer"),
	(("cloud",), "cloud_engineer"),
	(("site", "reliability"), "site_reliability_engineer"),
	(("sre",), "site_reliability_engineer"),
	(("mobile",), "mobile_developer"),
	(("embedded",), "embedded_systems_engineer"),
	(("iot",), "iot_engineer"),
	(("robotics",), "robotics_software_engineer"),
	(("firmware",), "firmware_engineer"),
	(("vlsi",), "vlsi_design_engineer"),
	(("qa",), "qa_automation_engineer"),
	(("automation",), "qa_automation_engineer"),
	(("security",), "cybersecurity_analyst"),
	(("cybersecurity",), "cybersecurity_analyst"),
	(("network",), "network_engineer"),
	(("business", "analyst"), "business_analyst"),
	(("product",), "product_manager"),
	(("ux",), "ui_ux_designer"),
	(("ui",), "ui_ux_designer"),
)

_STACK_TOPIC_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
	(("python",), "Python functions, exceptions, modules, and data structures"),
	(("java",), "Java object-oriented programming, collections, exceptions, and JVM basics"),
	(("spring",), "Dependency injection, REST controllers, and Spring application flow"),
	(("javascript",), "JavaScript scope, closures, promises, and asynchronous execution"),
	(("node", "express"), "Node.js event loop, async flow, and Express middleware fundamentals"),
	(("typescript",), "TypeScript types, interfaces, narrowing, and compile-time safety"),
	(("react",), "React component rendering, hooks, state, and props"),
	(("html", "css"), "HTML semantics, CSS layout, and responsive UI fundamentals"),
	(("sql", "postgresql", "mysql"), "Relational modelling, joins, indexing, and SQL query fundamentals"),
	(("mongodb",), "Document modelling, queries, and indexing concepts"),
	(("fastapi", "flask", "django"), "API request lifecycle, routing, validation, and backend web fundamentals"),
	(("docker",), "Containers, images, networking, and deployment basics"),
	(("aws", "gcp", "azure"), "Cloud compute, storage, networking, IAM, and reliability basics"),
	(("tensorflow", "pytorch", "scikit-learn"), "Model training, evaluation, overfitting, and feature engineering basics"),
	(("pandas", "numpy"), "Array/dataframe operations, transformation, and data analysis fundamentals"),
	(("kafka", "rabbitmq"), "Messaging, queues, ordering, retries, and event-driven basics"),
	(("linux",), "Processes, permissions, filesystems, and shell fundamentals"),
)


def _tokenize_topic_source(*values: Any) -> set[str]:
	tokens: set[str] = set()
	for value in values:
		if isinstance(value, (list, tuple, set)):
			for nested in value:
				tokens.update(_tokenize_topic_source(nested))
			continue
		text = str(value or "").casefold()
		parts = [part for part in re.split(r"[^a-z0-9+#]+", text) if part]
		tokens.update(parts)
	return tokens


def _dedupe_topics(topics: Sequence[str]) -> list[str]:
	result: list[str] = []
	seen: set[str] = set()
	for topic in topics:
		text = str(topic or "").strip()
		if not text:
			continue
		key = text.casefold()
		if key in seen:
			continue
		seen.add(key)
		result.append(text)
	return result


def _resolve_role_topics(
	role_key: str | None,
	role_title: str | None,
	skills: Sequence[Any],
	technologies: Sequence[Any],
) -> list[str]:
	normalized_key = str(role_key or "").strip().casefold()
	resolved_topics: list[str] = []

	canonical_key = _ROLE_TOPIC_ALIASES.get(normalized_key, normalized_key)
	if canonical_key in _ROLE_TOPIC_MAP:
		resolved_topics.extend(_ROLE_TOPIC_MAP[canonical_key])
	else:
		role_tokens = _tokenize_topic_source(normalized_key, role_title)
		for token_group, mapped_key in _ROLE_TOPIC_FAMILIES:
			if all(token in role_tokens for token in token_group):
				resolved_topics.extend(_ROLE_TOPIC_MAP[mapped_key])
				break

	stack_tokens = _tokenize_topic_source(skills, technologies)
	for token_group, topic in _STACK_TOPIC_HINTS:
		if any(token in stack_tokens for token in token_group):
			resolved_topics.append(topic)

	resolved_topics = _dedupe_topics(resolved_topics)
	if not resolved_topics:
		return list(_FALLBACK_TOPICS)

	return resolved_topics[:8]

# ---------------------------------------------------------------------------
# Internal JSON cleanup
# ---------------------------------------------------------------------------


def _extract_json_list(raw: str) -> list[Any]:
	"""Pull the first JSON array out of a raw LLM response."""
	text = raw.strip()

	fence = _JSON_FENCE.search(text)
	if fence:
		text = fence.group("body").strip()

	start = text.find("[")
	end = text.rfind("]")
	if start == -1 or end == -1 or end <= start:
		raise QuestionGeneratorLlmError(
			f"LLM response did not contain a JSON array. Got: {text[:200]}"
		)

	try:
		parsed = json.loads(text[start : end + 1])
	except json.JSONDecodeError as exc:
		raise QuestionGeneratorLlmError(
			f"LLM response contained malformed JSON: {exc}"
		) from exc

	if not isinstance(parsed, list):
		raise QuestionGeneratorLlmError("LLM response top-level value was not a list.")
	return parsed


def _extract_json_object(raw: str) -> dict[str, Any]:
	"""Pull the first JSON object out of a raw LLM response."""
	text = raw.strip()

	fence = _JSON_FENCE.search(text)
	if fence:
		text = fence.group("body").strip()

	start = text.find("{")
	end = text.rfind("}")
	if start == -1 or end == -1 or end <= start:
		raise QuestionGeneratorLlmError(
			f"LLM response did not contain a JSON object. Got: {text[:200]}"
		)

	try:
		parsed = json.loads(text[start : end + 1])
	except json.JSONDecodeError as exc:
		raise QuestionGeneratorLlmError(
			f"LLM response contained malformed JSON: {exc}"
		) from exc

	if not isinstance(parsed, dict):
		raise QuestionGeneratorLlmError("LLM response top-level value was not a dict.")
	return parsed


def _normalise_question_item(raw: Mapping[str, Any]) -> QuestionItem:
	"""Coerce a raw LLM-produced dict into a valid QuestionItem."""
	ideal = raw.get("ideal_points") or []
	if isinstance(ideal, str):
		ideal = [ideal]
	elif not isinstance(ideal, list):
		ideal = []

	difficulty = str(raw.get("difficulty") or "medium").lower().strip()
	if difficulty not in {"easy", "medium", "hard"}:
		difficulty = "medium"

	item = QuestionItem(
		question=str(raw.get("question") or "").strip(),
		ideal_points=[str(p).strip() for p in ideal if str(p).strip()],
		follow_up=str(raw.get("follow_up") or "").strip(),
		difficulty=difficulty,
	)

	question_tier = str(raw.get("question_tier") or "").strip().lower()
	if question_tier in {"strong", "familiar", "mentioned", "absent", "general"}:
		item["question_tier"] = question_tier

	focus_skill = str(raw.get("focus_skill") or "").strip()
	if focus_skill:
		item["focus_skill"] = focus_skill

	return item


# ---------------------------------------------------------------------------
# HR questions
# ---------------------------------------------------------------------------

_HR_SYSTEM_PROMPT = """\
You are a senior HR interviewer conducting a first-round interview for a fresher role.
Generate exactly {n} personalised HR questions based on the candidate profile below.

Return a JSON array (no markdown, no extra keys) with exactly {n} objects.
Each object must have:
  "question"     : the interview question (string)
  "ideal_points" : array of 3-5 bullet strings the ideal answer should cover
  "follow_up"    : one concise follow-up question (string)
  "difficulty"   : one of "easy" | "medium" | "hard"

Rules:
- Questions must be personalised to the candidate's name, summary, and interests.
- Questions must stay non-technical and HR-style.
- Focus on motivation, communication, teamwork, learning attitude, feedback, ownership, priorities, work style, self-awareness, and career growth.
- Do not ask about programming languages, frameworks, APIs, databases, system design, algorithms, debugging, code, architecture, infrastructure, cloud services, or project implementation details.
- Do not turn the resume summary, technical stack, or target role into a technical screening question.
- No generic questions like "Tell me about yourself" unless followed by a specific angle.
- Every question must be appropriate for a fresher/entry-level candidate.
- Return JSON only — no commentary, no markdown fences.
"""

_HR_USER_TEMPLATE = """\
Candidate name    : {name}
Target role       : {role_title}
Summary           : {summary}
Interests         : {interests}
"""

_HR_NON_TECHNICAL_PREFIX = re.compile(
	r"^(tell\s+me\s+about\s+yourself\b|tell\s+me\s+about\s+a\s+time|describe\s+a\s+time|share\s+an\s+example|why\s+(are\s+you\s+interested|this\s+role|do\s+you\s+want)|what\s+(motivates\s+you|are\s+your\s+strengths|is\s+your\s+biggest\s+weakness|kind\s+of\s+team|does\s+success\s+look\s+like|have\s+you\s+learned)|where\s+do\s+you\s+see\s+yourself|how\s+do\s+you\s+(handle|prioritize|communicate|work\s+with|respond\s+to)|how\s+would\s+you\s+handle|can\s+you\s+describe)\b",
	re.IGNORECASE,
)

_HR_TECHNICAL_MARKERS = re.compile(
	r"\b(api|apis|algorithm|algorithms|architecture|array|arrays|async|backend|bug|bugs|cache|caching|class|classes|cloud|code|coding|database|databases|debug|debugging|deployment|docker|endpoint|endpoints|fastapi|framework|frameworks|frontend|function|functions|git|http|infra(?:structure)?|java|javascript|json|linux|microservice|microservices|model|models|network|node(?:\.js)?|oop|pipeline|pipelines|postgres(?:ql)?|programming|python|query|queries|react|redis|rest|runtime|schema|schemas|server|servers|service|services|sql|system\s+design|technical(?:ly)?|technology|technologies|thread|threads|token|tokens|transaction|transactions)\b",
	re.IGNORECASE,
)

_HR_NON_TECHNICAL_SIGNAL_FRAGMENTS: tuple[str, ...] = (
	"career",
	"goal",
	"feedback",
	"motivat",
	"strength",
	"weakness",
	"team",
	"deadline",
	"conflict",
	"learn",
	"adapt",
	"pressure",
	"priorit",
	"communicat",
	"collaborat",
	"challenge",
	"growth",
	"culture",
	"ownership",
	"responsibilit",
	"work style",
	"values",
)


def is_non_technical_hr_question(question_text: str) -> bool:
	text = " ".join(str(question_text or "").split()).strip()
	if not text:
		return False
	if _HR_TECHNICAL_MARKERS.search(text):
		return False
	if _HR_NON_TECHNICAL_PREFIX.search(text):
		return True
	if _NON_TECHNICAL_BEHAVIORAL_PREFIX.search(text):
		return True
	normalized = text.casefold()
	return any(fragment in normalized for fragment in _HR_NON_TECHNICAL_SIGNAL_FRAGMENTS)


def has_valid_non_technical_hr_questions(questions_json: Mapping[str, Any] | None) -> bool:
	"""Return True when a cached HR batch can be reused as-is.

	This checks the *content* of the cached questions, not merely their shape.
	A batch that has drifted technical — for example a cached round containing
	"What is the difference between SQL and NoSQL databases?" — must be
	regenerated, otherwise the candidate keeps being asked database trivia in
	the HR round for the rest of the session.

	The bar is ``is_non_technical_hr_question``, which is the same standard
	generation already satisfies, so a properly generated batch is never
	needlessly regenerated.
	"""

	if not isinstance(questions_json, Mapping):
		return False
	questions = questions_json.get("questions") or []
	if not isinstance(questions, list) or not questions:
		return False
	return all(
		isinstance(item, Mapping)
		and is_non_technical_hr_question(str(item.get("question") or ""))
		for item in questions
	)


def _build_default_hr_questions(
	context: Mapping[str, Any],
	*,
	count: int,
	existing_questions: Sequence[Mapping[str, Any]] | None = None,
) -> list[QuestionItem]:
	role_title = str(
		context.get("selected_role_title")
		or context.get("selected_role_key")
		or "this role"
	).strip()
	interests = context.get("interests") or []
	interest = ""
	if isinstance(interests, list):
		for item in interests:
			interest = str(item or "").strip()
			if interest:
				break
	else:
		interest = str(interests or "").strip()

	def _question_item(
		question: str,
		ideal_points: Sequence[str],
		follow_up: str,
		*,
		difficulty: str = "medium",
	) -> QuestionItem:
		return QuestionItem(
			question=question,
			ideal_points=[str(point).strip() for point in ideal_points if str(point).strip()],
			follow_up=follow_up,
			difficulty=difficulty,
		)

	templates: list[QuestionItem] = [
		_question_item(
			f"What attracted you to an entry-level {role_title} position, and what are you hoping to learn in your first year?",
			[
				"Clear motivation for the role",
				"Realistic expectations for early-career growth",
				"Curiosity and willingness to learn",
			],
			"What would make you feel you are progressing well in that role?",
			difficulty="easy",
		),
		_question_item(
			"Tell me about a time you had to learn something quickly to finish a responsibility well.",
			[
				"Brief context and responsibility",
				"How the learning approach was structured",
				"Outcome and lesson taken forward",
			],
			"What would you do differently the next time you are in that situation?",
		),
		_question_item(
			"How do you respond when you receive constructive feedback on your work?",
			[
				"Openness to feedback",
				"Specific action taken after feedback",
				"Focus on improvement rather than defensiveness",
			],
			"Can you share one example where feedback changed how you approached later work?",
		),
		_question_item(
			"Describe a time you worked with other people to complete something important. What role did you naturally take?",
			[
				"Clear contribution within the group",
				"Communication or coordination approach",
				"Outcome and what was learned about teamwork",
			],
			"How do you handle it when team members work in very different ways?",
		),
		_question_item(
			"When you have multiple deadlines or expectations at the same time, how do you decide what to do first?",
			[
				"Simple prioritization method",
				"Awareness of deadlines and impact",
				"Communication when trade-offs are needed",
			],
			"How do you communicate early when you may need help or more time?",
		),
		_question_item(
			"What strengths do you think you would bring to your first full-time team, and what is one area you still want to improve?",
			[
				"Balanced self-awareness",
				"Specific strengths tied to work habits",
				"Honest growth area with an improvement plan",
			],
			"What are you already doing to improve that growth area?",
		),
	]

	if interest:
		templates.insert(
			1,
			_question_item(
				f"You mentioned an interest in {interest}. How have you kept learning about it, and what does that say about the kind of work you enjoy?",
				[
					"Genuine curiosity and initiative",
					"Examples of self-driven learning",
					"Connection between interests and long-term growth",
				],
				"How would you like to keep growing that interest during your first job?",
					difficulty="easy",
			),
		)

	seen = {
		str(item.get("question") or "").strip().casefold()
		for item in (existing_questions or [])
		if isinstance(item, Mapping)
	}
	result: list[QuestionItem] = []
	for template in templates:
		question_key = template["question"].strip().casefold()
		if question_key in seen:
			continue
		seen.add(question_key)
		result.append(template)
		if len(result) >= count:
			break
	return result


def generate_hr_questions(
	context: Mapping[str, Any],
	*,
	settings: GroqSettings | None = None,
	count: int = 5,
) -> list[QuestionItem]:
	"""Generate a batch of HR questions for the given round context.

	Args:
		context: Output of ``build_hr_round_context``.
		settings: Groq settings; resolved from env when omitted.
		count: Number of questions to generate (default 5).

	Returns:
		List of ``QuestionItem`` dicts.

	Raises:
		QuestionGeneratorUnavailableError: Groq not configured.
		QuestionGeneratorLlmError: LLM returned unparseable output.
	"""
	resolved = settings or get_settings().groq
	if resolved is None:
		raise QuestionGeneratorUnavailableError(
			"GROQ_API_KEY is not configured. HR question generation is unavailable."
		)

	interests = context.get("interests") or []
	if isinstance(interests, list):
		interests_str = ", ".join(str(i) for i in interests[:8]) or "not specified"
	else:
		interests_str = str(interests) or "not specified"

	system_prompt = _HR_SYSTEM_PROMPT.format(n=count)
	user_message = _HR_USER_TEMPLATE.format(
		name=context.get("candidate_name") or "the candidate",
		role_title=context.get("selected_role_title") or context.get("selected_role_key") or "the applied role",
		summary=context.get("summary") or "not provided",
		interests=interests_str,
	)

	try:
		completion = create_chat_completion(
			settings=resolved,
			model=resolved.resume_parser_model,
			temperature=0.7,
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": user_message},
			],
		)
	except GroqDependencyError as exc:
		raise QuestionGeneratorUnavailableError(str(exc)) from exc
	except GroqCompletionError as exc:
		raise QuestionGeneratorLlmError(f"HR question generation failed: {exc}") from exc

	raw_content = completion.choices[0].message.content
	raw_list = _extract_json_list(raw_content)
	questions = [_normalise_question_item(item) for item in raw_list if isinstance(item, dict)]
	questions = [
		question
		for question in questions
		if is_non_technical_hr_question(question.get("question", ""))
	]
	if len(questions) < count:
		questions.extend(
			_build_default_hr_questions(
				context,
				count=count - len(questions),
				existing_questions=questions,
			)
		)

	if not questions:
		raise QuestionGeneratorLlmError("HR question generation returned an empty list.")

	return questions[:count]


# ---------------------------------------------------------------------------
# Technical questions
# ---------------------------------------------------------------------------

_DIFFICULTY_LABELS = {
	0.0: "easy",
	0.33: "easy",
	0.5: "medium",
	0.67: "medium",
	1.0: "hard",
}

_CONCEPTUAL_TECHNICAL_PREFIX = re.compile(
	r"^(what|why|when|which|where|who|what\s+is|what\s+are|how\s+does|how\s+do|explain|describe|compare|tell\s+me\s+about|walk\s+me\s+through)\b",
	re.IGNORECASE,
)

_NON_CONCEPTUAL_TECHNICAL_PREFIX = re.compile(
	r"^(design|implement|write|build|create|develop|architect|model)\b|^how\s+would\s+you\s+(design|implement|write|build|create|develop|architect|model)\b|^explain\s+how\s+you\s+would\s+(design|implement|write|build|create|develop|architect|model)\b",
	re.IGNORECASE,
)

_NON_TECHNICAL_BEHAVIORAL_PREFIX = re.compile(
	r"^(what\s+is\s+your\s+approach\s+to|how\s+do\s+you\s+approach|how\s+would\s+you\s+approach|how\s+do\s+you\s+break\s+down|how\s+would\s+you\s+break\s+down|tell\s+me\s+about\s+a\s+time|describe\s+a\s+time|how\s+do\s+you\s+handle\s+ambiguity|how\s+do\s+you\s+handle\s+pressure|how\s+do\s+you\s+prioritize|how\s+do\s+you\s+communicate|how\s+do\s+you\s+work\s+with|what\s+are\s+your\s+strengths|what\s+is\s+your\s+biggest\s+weakness)\b",
	re.IGNORECASE,
)


def _resolve_difficulty_label(signal: float) -> str:
	if signal < 0.35:
		return "easy"
	if signal < 0.65:
		return "medium"
	return "hard"


def is_conceptual_technical_question(question_text: str) -> bool:
	text = str(question_text or "").strip()
	if not text:
		return False
	if _NON_CONCEPTUAL_TECHNICAL_PREFIX.search(text):
		return False
	if _NON_TECHNICAL_BEHAVIORAL_PREFIX.search(text):
		return False
	return _CONCEPTUAL_TECHNICAL_PREFIX.search(text) is not None


def has_valid_conceptual_technical_questions(questions_json: Mapping[str, Any] | None) -> bool:
	if not isinstance(questions_json, Mapping):
		return False
	questions = questions_json.get("questions") or []
	return bool(isinstance(questions, list) and len(questions) > 0)


_TECHNICAL_SYSTEM_PROMPT = """\
You are a senior technical interviewer for a fresher hiring process.
Generate exactly {n} technical interview questions based on the candidate's profile and the topic areas below.

Return a JSON array (no markdown, no extra keys) with exactly {n} objects.
Each object must have:
  "question"     : the interview question (string)
  "ideal_points" : array of 3-5 bullet strings covering what a strong answer should include
  "follow_up"    : one concise follow-up question (string)
  "difficulty"   : one of "easy" | "medium" | "hard"

If a skill allocation plan is provided in the user message, also include:
	"question_tier" : one of "strong" | "familiar" | "mentioned" | "absent"
	"focus_skill"   : the main skill this question targets

Rules:
- Questions must be grounded in the candidate's actual skills, not generic CS trivia.
- Questions must target the role's core technical subjects and concrete concepts inside those subjects.
- The batch must look like a realistic fresher technical screening for this exact role, not a random mix of concepts from neighbouring roles.
- Every question must be conceptual and explanation-first.
- Ask only explanatory question styles such as: "what", "why", "when", "how does", "explain", "describe", or "compare".
- Do not ask the candidate to design, implement, build, architect, model, write code, or create anything.
- Do not generate coding exercises, low-level design prompts, class-design prompts, or system-design prompts.
- Do not ask HR-style behavioural questions about teamwork, communication style, work habits, prioritisation, ambiguity handling, strengths, weaknesses, or generic problem-solving approach.
- Every question must clearly name or anchor to a concrete technical concept, tool, framework, language, protocol, database, algorithm, data structure, operating-system concept, cloud concept, or ML concept relevant to the role.
- If algorithms or data structures are not explicit role subjects, do not introduce them just as generic filler.
- Include at least one question per skill-gap topic if gaps are provided.
- If a skill allocation plan is provided, return questions in the same order as the plan and copy the requested `question_tier` and `focus_skill` into each matching object.
- Strong-tier questions must verify real depth using mechanisms, trade-offs, failure modes, debugging logic, or internal behavior rather than basic definitions.
- Familiar-tier questions should probe limitations, edge cases, integration details, or trade-offs around the focus skill.
- Absent-tier questions must stay fundamentals-first: ask for core concepts, why the concept matters, when it is used, or what problem it solves. Do not assume hands-on production ownership for an absent skill.
- Do not spend absent-tier slots on advanced optimizations before the core concept is established.
- Target the overall difficulty level: {difficulty_label}.
- Do not ask the same concept twice.
- Return JSON only — no commentary, no markdown fences.
"""

_TECHNICAL_USER_TEMPLATE = """\
Candidate skills      : {skills}
Candidate technologies: {technologies}
Target role           : {role_title}
Role key              : {role_key}
Skill gaps to cover   : {skill_gaps}
Role core subjects    : {topics}
Skill focus profile   :
{skill_focus_block}
"""


def _normalise_string_list(values: Any) -> list[str]:
	if not isinstance(values, list):
		return []

	result: list[str] = []
	seen: set[str] = set()
	for value in values:
		text = str(value or "").strip()
		if not text:
			continue
		key = text.casefold()
		if key in seen:
			continue
		seen.add(key)
		result.append(text)
	return result


def _skill_score_entry(context: Mapping[str, Any], skill: str) -> Mapping[str, Any] | None:
	"""Look up a skill's profile entry, tolerating case and whitespace.

	Both callers below used ``skill_scores.get(skill)`` — an exact dict lookup —
	while every other comparison in this module casefolds. That mismatch is
	reachable: the profiler writes ``skill_scores[skill]`` and appends the same
	``skill`` to its tier lists, but ``_normalise_string_list`` strips those
	lists on the way in here. So a skill name arriving from the LLM with a
	stray leading space (" Verilog") is stored under " Verilog" and looked up
	as "Verilog", and the lookup misses.

	A miss is not loud. ``_resolve_skill_origin`` had no fallback at all and
	returned "role", which quietly collapses the 70/30 role-to-resume split
	toward 100% role — the exact targeting guarantee
	``INTERVIEW_QUESTION_TARGETING.md`` exists to provide, lost with no error.

	Exact match first, so the common path stays a single dict hit.
	"""

	skill_profile = context.get("skill_profile")
	if not isinstance(skill_profile, Mapping):
		return None
	skill_scores = skill_profile.get("skill_scores")
	if not isinstance(skill_scores, Mapping):
		return None

	entry = skill_scores.get(skill)
	if isinstance(entry, Mapping):
		return entry

	wanted = str(skill or "").strip().casefold()
	if not wanted:
		return None
	for key, value in skill_scores.items():
		if str(key or "").strip().casefold() == wanted and isinstance(value, Mapping):
			return value
	return None


def _resolve_skill_tier(context: Mapping[str, Any], skill: str) -> str:
	entry = _skill_score_entry(context, skill)
	if entry is not None:
		tier = str(entry.get("tier") or "").strip().lower()
		if tier in {"strong", "familiar", "mentioned", "absent"}:
			return tier

	for tier, key in (
		("strong", "strong_skills"),
		("familiar", "familiar_skills"),
		("mentioned", "mentioned_skills"),
		("absent", "absent_skills"),
	):
		for candidate in _normalise_string_list(context.get(key)):
			if candidate.casefold() == str(skill or "").strip().casefold():
				return tier
	return "general"


def _resolve_skill_origin(context: Mapping[str, Any], skill: str) -> str:
	"""Where a candidate focus_skill came from: the role profile or the resume.

	Skills the resume profiler could not tag (older cached profiles, tests)
	default to "role" so the 70/30 split degrades to the previous tier-only
	allocation rather than starving on an empty resume bucket.

	That default is only safe when the lookup itself is reliable. It is why
	this goes through ``_skill_score_entry``: an exact-key lookup here used to
	turn a stray space in a skill name into a silent "role", and enough of
	those collapse the split entirely.
	"""
	entry = _skill_score_entry(context, skill)
	if entry is not None:
		origin = str(entry.get("origin") or "").strip().casefold()
		if origin in {"role", "resume", "role_and_resume"}:
			return origin
	return "role"


def _build_skill_allocation_plan(
	context: Mapping[str, Any],
	count: int,
) -> list[dict[str, str]]:
	"""Pick which skills the round's questions target, and in what order.

	Two axes drive the pick: tier (evidence depth, unchanged) and origin
	(role subject matter vs. resume-only skill). A candidate tailors their
	resume to the role they apply for, so the role leads: 70% of targets come
	from the role's subject matter, evidenced tiers first so they get probed
	deeply rather than defined; the remaining 30% come from resume-only
	skills the role profile never asked about.
	"""
	if not isinstance(context.get("skill_profile"), Mapping):
		return []
	if count <= 0:
		return []

	strong_skills = _normalise_string_list(context.get("strong_skills"))
	familiar_skills = _normalise_string_list(context.get("familiar_skills"))
	mentioned_skills = _normalise_string_list(context.get("mentioned_skills"))
	absent_skills = _normalise_string_list(context.get("absent_skills"))
	soft_gap_skills = _normalise_string_list(context.get("soft_gap_skills"))
	priority_focus_areas = _normalise_string_list(context.get("priority_focus_areas"))

	shared_absent_skills = [
		*absent_skills,
		*[skill for skill in soft_gap_skills if skill.casefold() not in {s.casefold() for s in absent_skills}],
	]

	# Evidenced tiers first, so a skill the resume backs is spent before one
	# it doesn't — that is what lets it be probed deeply rather than defined.
	ordered_candidates = _normalise_string_list([
		*strong_skills,
		*familiar_skills,
		*mentioned_skills,
		*priority_focus_areas,
		*shared_absent_skills,
	])

	role_bucket: list[str] = []
	resume_bucket: list[str] = []
	for skill in ordered_candidates:
		if _resolve_skill_origin(context, skill) == "resume":
			resume_bucket.append(skill)
		else:
			role_bucket.append(skill)

	role_quota = min(round(count * 0.7), count)
	resume_quota = count - role_quota

	plan: list[dict[str, str]] = []
	seen: set[str] = set()

	def _fill(skills: Sequence[str], budget: int) -> None:
		added = 0
		for skill in skills:
			if added >= budget or len(plan) >= count:
				return
			normalized = skill.casefold()
			if normalized in seen:
				continue
			seen.add(normalized)
			plan.append({"question_tier": _resolve_skill_tier(context, skill), "focus_skill": skill})
			added += 1

	_fill(role_bucket, role_quota)
	_fill(resume_bucket, resume_quota)

	if len(plan) < count:
		# One bucket ran dry (e.g. no resume-only skills exist) — spend the
		# leftover budget from whichever bucket still has candidates rather
		# than under-filling the round.
		_fill([*role_bucket, *resume_bucket], count - len(plan))

	return plan[:count]


def _build_skill_focus_block(
	context: Mapping[str, Any],
	count: int,
) -> tuple[str, list[dict[str, str]]]:
	plan = _build_skill_allocation_plan(context, count)
	if not plan:
		return "No resume skill allocation available.", []

	strong_skills = ", ".join(_normalise_string_list(context.get("strong_skills"))) or "none"
	familiar_skills = ", ".join(_normalise_string_list(context.get("familiar_skills"))) or "none"
	mentioned_skills = ", ".join(_normalise_string_list(context.get("mentioned_skills"))) or "none"
	absent_skills = ", ".join(_normalise_string_list(context.get("absent_skills"))) or "none"
	soft_gap_skills = ", ".join(_normalise_string_list(context.get("soft_gap_skills"))) or "none"

	lines = [
		"Strong skills    : " + strong_skills,
		"Familiar skills  : " + familiar_skills,
		"Mentioned skills : " + mentioned_skills,
		"Absent skills    : " + absent_skills,
		"Soft-gap skills  : " + soft_gap_skills,
		"Tier guidance    : strong=deep verification, familiar=edge cases and trade-offs, absent=fundamentals and first-principles.",
		"Question plan    : return questions in this exact order with matching question_tier and focus_skill.",
	]
	for index, slot in enumerate(plan, start=1):
		guidance = {
			"strong": "verify depth with mechanisms, trade-offs, or failure modes",
			"familiar": "probe limitations, edge cases, or integration trade-offs",
			"absent": "ask fundamentals first: what problem it solves in this role's context, why it matters, and when it's used",
			"mentioned": "probe whether the concept is understood beyond name recognition",
		}.get(slot["question_tier"], "keep the question conceptual and role-relevant")
		lines.append(
			f"  {index}. question_tier={slot['question_tier']} | focus_skill={slot['focus_skill']} | guidance={guidance}"
		)
	return "\n".join(lines), plan


def _apply_skill_allocation_metadata(
	questions: Sequence[QuestionItem],
	allocation_plan: Sequence[Mapping[str, str]],
) -> list[QuestionItem]:
	enriched_questions: list[QuestionItem] = []
	for index, question in enumerate(questions):
		item = dict(question)
		plan_entry = allocation_plan[index] if index < len(allocation_plan) else None
		question_tier = str(item.get("question_tier") or "").strip().lower()
		if question_tier not in {"strong", "familiar", "mentioned", "absent", "general"}:
			question_tier = str(plan_entry.get("question_tier") or "general").strip().lower() if plan_entry else "general"
		if question_tier != "general":
			item["question_tier"] = question_tier

		focus_skill = str(item.get("focus_skill") or "").strip()
		if not focus_skill and plan_entry is not None:
			focus_skill = str(plan_entry.get("focus_skill") or "").strip()
		if focus_skill:
			item["focus_skill"] = focus_skill

		enriched_questions.append(item)
	return enriched_questions


def generate_technical_questions(
	context: Mapping[str, Any],
	*,
	settings: GroqSettings | None = None,
	difficulty_signal: float = 0.5,
	count: int = 6,
) -> list[QuestionItem]:
	"""Generate a batch of technical questions for the given round context.

	Args:
		context: Output of ``build_technical_round_context``.
		settings: Groq settings; resolved from env when omitted.
		difficulty_signal: Float in [0, 1] where 0 = easiest, 1 = hardest.
		count: Number of questions to generate (default 6).

	Returns:
		List of ``QuestionItem`` dicts.

	Raises:
		QuestionGeneratorUnavailableError: Groq not configured.
		QuestionGeneratorLlmError: LLM returned unparseable output.
	"""
	resolved = settings or get_settings().groq
	if resolved is None:
		raise QuestionGeneratorUnavailableError(
			"GROQ_API_KEY is not configured. Technical question generation is unavailable."
		)

	role_key = context.get("selected_role_key") or ""

	skills = context.get("skills") or []
	technologies = context.get("technologies") or []
	skill_gaps = context.get("skill_gaps") or []
	role_title = context.get("selected_role_title") or role_key or "the applied role"
	topics = _resolve_role_topics(role_key, role_title, skills, technologies)
	skill_focus_block, skill_allocation_plan = _build_skill_focus_block(context, count)

	difficulty_label = _resolve_difficulty_label(difficulty_signal)

	system_prompt = _TECHNICAL_SYSTEM_PROMPT.format(n=count, difficulty_label=difficulty_label)
	user_message = _TECHNICAL_USER_TEMPLATE.format(
		skills=", ".join(str(s) for s in skills[:20]) or "not specified",
		technologies=", ".join(str(t) for t in technologies[:15]) or "not specified",
		role_title=role_title,
		role_key=role_key or "unknown",
		skill_gaps=", ".join(str(g) for g in skill_gaps[:10]) or "none identified",
		topics=", ".join(topics),
		skill_focus_block=skill_focus_block,
	)

	try:
		completion = create_chat_completion(
			settings=resolved,
			model=resolved.resume_parser_model,
			temperature=0.6,
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": user_message},
			],
		)
	except GroqDependencyError as exc:
		raise QuestionGeneratorUnavailableError(str(exc)) from exc
	except GroqCompletionError as exc:
		raise QuestionGeneratorLlmError(f"Technical question generation failed: {exc}") from exc

	raw_content = completion.choices[0].message.content
	raw_list = _extract_json_list(raw_content)
	questions = [_normalise_question_item(item) for item in raw_list if isinstance(item, dict)]
	questions = _apply_skill_allocation_metadata(questions, skill_allocation_plan)

	if not questions:
		raise QuestionGeneratorLlmError(
			"Technical question generation returned an empty list of questions."
		)

	return questions[:count]


# ---------------------------------------------------------------------------
# Project discussion questions
# ---------------------------------------------------------------------------

_PROJECT_SYSTEM_PROMPT = """\
You are a senior technical interviewer conducting a project discussion round for a fresher candidate.
For each project listed below, generate exactly {per_project} technical discussion questions.

Return a JSON object (no markdown, no extra keys) with a single key "projects".
"projects" must be an array where each element has:
  "project_title" : the project title (string, copied from input)
  "questions"     : array of exactly {per_project} question objects

Each question object must have:
  "question"     : the interview question (string)
  "ideal_points" : array of 3-4 bullet strings a strong answer should mention
  "follow_up"    : one concise follow-up question (string)
  "difficulty"   : one of "easy" | "medium" | "hard"

Rules:
- Every question MUST reference the candidate's stated tech stack, outcomes, or design choices.
- Do not ask generic questions unrelated to this specific project.
- Questions should probe understanding, not just ask the candidate to repeat the description.
- Ask at least one question about a trade-off, design decision, or challenge faced.
- Return JSON only — no commentary, no markdown fences.
"""

_PROJECT_USER_TEMPLATE = """\
Projects:
{projects_block}
"""


def _build_projects_block(projects: Sequence[Mapping[str, Any]]) -> str:
	lines: list[str] = []
	for idx, project in enumerate(projects, start=1):
		title = str(project.get("title") or f"Project {idx}").strip()
		description = str(project.get("description") or "").strip()
		tech_stack = project.get("tech_stack") or []
		outcomes = project.get("outcomes") or []
		role = str(project.get("role") or "").strip()

		lines.append(f"Project {idx}: {title}")
		if description:
			lines.append(f"  Description: {description}")
		if tech_stack:
			lines.append(f"  Tech stack: {', '.join(str(t) for t in tech_stack)}")
		if outcomes:
			lines.append(f"  Outcomes: {', '.join(str(o) for o in outcomes)}")
		if role:
			lines.append(f"  Candidate's role: {role}")
		lines.append("")
	return "\n".join(lines).strip()


def generate_project_questions(
	context: Mapping[str, Any],
	*,
	settings: GroqSettings | None = None,
	questions_per_project: int = 3,
) -> list[ProjectQuestionSet]:
	"""Generate project discussion questions for every project in the context.

	Args:
		context: Output of ``build_project_round_context``.
		settings: Groq settings; resolved from env when omitted.
		questions_per_project: Questions generated per project (default 3).

	Returns:
		List of ``ProjectQuestionSet`` dicts, one per project.

	Raises:
		QuestionGeneratorUnavailableError: Groq not configured.
		QuestionGeneratorLlmError: LLM returned unparseable output.
	"""
	resolved = settings or get_settings().groq
	if resolved is None:
		raise QuestionGeneratorUnavailableError(
			"GROQ_API_KEY is not configured. Project question generation is unavailable."
		)

	projects: Sequence[Mapping[str, Any]] = context.get("projects") or []
	if not projects:
		raise QuestionGeneratorLlmError(
			"No projects found in the context; cannot generate project discussion questions."
		)

	projects_block = _build_projects_block(projects)
	system_prompt = _PROJECT_SYSTEM_PROMPT.format(per_project=questions_per_project)
	user_message = _PROJECT_USER_TEMPLATE.format(projects_block=projects_block)

	try:
		completion = create_chat_completion(
			settings=resolved,
			model=resolved.resume_parser_model,
			temperature=0.6,
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": user_message},
			],
		)
	except GroqDependencyError as exc:
		raise QuestionGeneratorUnavailableError(str(exc)) from exc
	except GroqCompletionError as exc:
		raise QuestionGeneratorLlmError(f"Project question generation failed: {exc}") from exc

	raw_content = completion.choices[0].message.content
	raw_obj = _extract_json_object(raw_content)
	raw_projects = raw_obj.get("projects") or []

	if not isinstance(raw_projects, list) or not raw_projects:
		raise QuestionGeneratorLlmError(
			"Project question generation returned no project entries."
		)

	result: list[ProjectQuestionSet] = []
	for raw_proj in raw_projects:
		if not isinstance(raw_proj, dict):
			continue
		raw_qs = raw_proj.get("questions") or []
		questions = [
			_normalise_question_item(q)
			for q in raw_qs
			if isinstance(q, dict)
		]
		result.append(
			ProjectQuestionSet(
				project_title=str(raw_proj.get("project_title") or "").strip(),
				questions=questions[:questions_per_project],
			)
		)

	if not result:
		raise QuestionGeneratorLlmError("Project question generation returned no valid projects.")
	return result


# ---------------------------------------------------------------------------
# Cache-first entry point
# ---------------------------------------------------------------------------


def get_or_generate_questions(
	round: str,
	context: Mapping[str, Any],
	*,
	settings: GroqSettings | None = None,
	cached_questions_json: dict[str, Any] | None = None,
	difficulty_signal: float = 0.5,
) -> dict[str, Any]:
	"""Return cached questions when available; otherwise generate and return fresh ones.

	The returned dict is designed to be stored directly into
	``interview_round_sessions.questions_json``.

	Args:
		round: One of ``"hr"``, ``"technical"``, or ``"project_discussion"``.
		context: The round-specific context dict from ``interview_context.py``.
		settings: Groq settings; resolved from env when omitted.
		cached_questions_json: Existing ``questions_json`` from MongoDB (may be None).
		difficulty_signal: Used only for the technical round.

	Returns:
		Dict with key ``"questions"`` (list) or ``"projects"`` (list) depending on round,
		plus ``"generated"`` bool indicating whether a fresh call was made.

	Raises:
		ValueError: Unknown round name.
		QuestionGeneratorUnavailableError | QuestionGeneratorLlmError: Generation failed.
	"""
	valid_rounds = {"hr", "technical", "project_discussion"}
	if round not in valid_rounds:
		raise ValueError(
			f"Unknown round '{round}'. Must be one of: {', '.join(sorted(valid_rounds))}."
		)

	# ---- return cache if present ----
	if cached_questions_json and isinstance(cached_questions_json, dict):
		if round == "project_discussion":
			if cached_questions_json.get("projects"):
				return {**cached_questions_json, "generated": False}
		elif round == "technical":
			if has_valid_conceptual_technical_questions(cached_questions_json):
				return {**cached_questions_json, "generated": False}
		elif round == "hr":
			if has_valid_non_technical_hr_questions(cached_questions_json):
				return {**cached_questions_json, "generated": False}
		else:
			if cached_questions_json.get("questions"):
				return {**cached_questions_json, "generated": False}

	# ---- generate fresh ----
	if round == "hr":
		questions = generate_hr_questions(context, settings=settings)
		return {"questions": questions, "generated": True}

	if round == "technical":
		questions = generate_technical_questions(
			context,
			settings=settings,
			difficulty_signal=difficulty_signal,
		)
		skill_allocation_plan = [
			{
				"question_tier": str(question.get("question_tier") or "").strip(),
				"focus_skill": str(question.get("focus_skill") or "").strip(),
			}
			for question in questions
			if str(question.get("question_tier") or "").strip()
			or str(question.get("focus_skill") or "").strip()
		]
		return {
			"questions": questions,
			"generated": True,
			"skill_allocation_used": bool(context.get("skill_profile")),
			"skill_allocation_plan": skill_allocation_plan,
		}

	# project_discussion
	project_sets = generate_project_questions(context, settings=settings)
	return {"projects": project_sets, "generated": True}
