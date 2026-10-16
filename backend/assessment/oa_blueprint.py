"""What an online assessment (OA) for a role looks like: sections, size, mix.

The old assessment was a fixed 8 mostly easy/medium MCQs. Real OAs for
freshers (TCS NQT, Infosys, Accenture, product-company screens) are longer,
sectioned and mixed: aptitude and reasoning, the role's fundamentals, the
role's technical subject, and reading code or pseudocode. This module decides
the shape of one attempt; ``oa_generator`` fills it.

Every attempt draws a fresh size between ``MIN_QUESTIONS`` and
``MAX_QUESTIONS`` and fresh topic hints, so two attempts never look alike.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field

from backend.nlp.question_formats import role_family, snippet_language

MIN_QUESTIONS = 15
MAX_QUESTIONS = 25

# Harder than the old quiz: most questions are medium or hard.
DIFFICULTY_MIX: tuple[tuple[str, float], ...] = (("easy", 0.15), ("medium", 0.45), ("hard", 0.40))

# Question types (all auto-gradable)
MCQ_SINGLE = "mcq_single"
MCQ_MULTI = "mcq_multi"  # select all that apply
CODE_OUTPUT = "code_output"
FIND_BUG = "find_bug"
PSEUDOCODE = "pseudocode"
NUMERICAL = "numerical"
SCENARIO = "scenario"

QUESTION_TYPES = frozenset({MCQ_SINGLE, MCQ_MULTI, CODE_OUTPUT, FIND_BUG, PSEUDOCODE, NUMERICAL, SCENARIO})
CODE_TYPES = frozenset({CODE_OUTPUT, FIND_BUG, PSEUDOCODE})

_QUANT_TOPICS = (
	"percentages", "profit and loss", "ratio and proportion", "time and work", "pipes and cisterns",
	"time, speed and distance", "trains and relative speed", "boats and streams", "simple and compound interest",
	"permutations and combinations", "probability", "averages and mixtures", "number system and remainders",
	"HCF and LCM", "problems on ages", "data interpretation from a small table",
)
_LOGICAL_TOPICS = (
	"number series", "letter series", "coding-decoding", "blood relations", "direction sense", "syllogisms",
	"seating arrangement", "data sufficiency", "clocks and calendars", "statement and conclusion",
)
_CS_FUNDAMENTALS = (
	"CPU scheduling", "deadlocks", "paging and virtual memory", "process vs thread and synchronisation",
	"DBMS normalisation", "transactions and isolation levels", "indexing and B-trees", "SQL joins and grouping",
	"TCP vs UDP", "HTTP, DNS and TLS", "IP addressing and subnetting", "OOP: inheritance and polymorphism",
	"time and space complexity", "hashing and collisions", "trees and graph traversal", "recursion and the call stack",
	"stacks, queues and heaps", "sorting algorithms",
)
_ML_MATHS = (
	"conditional probability and Bayes' theorem", "expectation and variance", "normal and binomial distributions",
	"hypothesis testing and p-values", "matrix multiplication and rank", "eigenvalues and eigenvectors",
	"gradients and the chain rule", "correlation vs covariance", "entropy and information gain",
	"precision, recall and F1 from a confusion matrix",
)
_STATS = (
	"mean, median and mode", "variance and standard deviation", "percentiles and quartiles", "correlation",
	"conditional probability", "normal distribution and z-scores", "sampling and bias", "hypothesis testing",
	"A/B test interpretation", "outliers and skew",
)
_SQL_TOPICS = (
	"INNER vs LEFT JOIN results", "GROUP BY with HAVING", "window functions (ROW_NUMBER, RANK)", "NULL handling in aggregates",
	"subqueries and correlated subqueries", "common table expressions", "DISTINCT and COUNT", "self joins",
	"date filtering", "UNION vs UNION ALL",
)
_NETWORK_OS = (
	"TCP handshake and ports", "subnetting and CIDR", "DNS resolution", "HTTP status codes and methods",
	"load balancing", "Linux processes and signals", "file permissions", "memory and swap", "TLS certificates",
	"firewalls and NAT",
)
_CORE_FUNDAMENTALS = (
	"Boolean algebra and K-maps", "number systems and two's complement", "flip-flops and counters",
	"multiplexers and decoders", "op-amp circuits", "RC circuits and time constants", "microcontroller timers and interrupts",
	"I2C, SPI and UART", "ADC resolution and sampling", "signals and Nyquist rate",
)
# What each language's "what does this print?" questions should turn on.
_CODE_TOPICS = {
	"java": ("String pool and == vs equals", "Integer caching", "pass-by-value of references", "static vs instance",
			 "exceptions and finally", "collections behaviour", "inheritance and overriding", "integer overflow and division"),
	"python": ("mutable default arguments", "list aliasing and copies", "closures and late binding", "generators",
			   "dict and set behaviour", "slicing", "is vs ==", "exceptions and else/finally"),
	"javascript": ("== vs ===", "hoisting and closures in loops", "this binding", "event loop: setTimeout vs Promise",
				   "array methods and sort", "truthy and falsy values", "spread and destructuring", "typeof quirks"),
	"typescript": ("== vs ===", "closures in loops", "event loop ordering", "array methods", "type narrowing at runtime"),
	"cpp": ("references vs copies", "object slicing", "virtual dispatch", "pre vs post increment", "integer overflow",
			"pointer arithmetic", "static variables", "vector iterator invalidation"),
	"c": ("pointer arithmetic", "pre vs post increment", "integer division and overflow", "static variables",
		  "operator precedence", "arrays and sizeof", "bitwise operators", "loops with break and continue"),
	"bash": ("variable quoting", "exit codes and &&/||", "loops over globs", "string slicing", "here-docs"),
	"sql": _SQL_TOPICS,
}


@dataclass(frozen=True)
class SectionSpec:
	key: str
	title: str
	share: float
	types: tuple[tuple[str, float], ...]
	topics: tuple[str, ...]
	language: str = ""  # for code types
	# Topics for one question type only. Aptitude needs this: a "time and
	# work" topic on a plain-MCQ slot produced an arithmetic question that
	# skipped the numerical answer check (and had a wrong key).
	type_topics: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass
class Slot:
	question_type: str
	difficulty: str
	topic: str


@dataclass
class SectionPlan:
	key: str
	title: str
	count: int
	language: str
	spec: SectionSpec
	slots: list[Slot] = field(default_factory=list)


@dataclass
class OABlueprint:
	family: str
	role_key: str
	role_title: str
	total_questions: int
	language: str
	sections: list[SectionPlan]


_CORE_HINTS = frozenset({"embedded", "vlsi", "robotics", "firmware", "iot", "electronics", "ece", "hardware", "fpga", "verification"})


def oa_family(role_key: str | None, role_title: str | None = "") -> str:
	"""software | ml | data | devops | security | core."""
	tokens = set(re.split(r"[^a-z0-9]+", f"{role_key or ''} {role_title or ''}".casefold()))
	if tokens & _CORE_HINTS:
		return "core"
	return role_family(role_key, role_title)


def _aptitude(share: float = 0.25) -> SectionSpec:
	return SectionSpec(
		key="aptitude",
		title="Aptitude & reasoning",
		share=share,
		types=((NUMERICAL, 0.55), (MCQ_SINGLE, 0.45)),
		topics=_QUANT_TOPICS + _LOGICAL_TOPICS,
		type_topics=((NUMERICAL, _QUANT_TOPICS), (MCQ_SINGLE, _LOGICAL_TOPICS)),
	)


def _sections(family: str, role_title: str, role_topics: tuple[str, ...], language: str) -> list[SectionSpec]:
	role_name = role_title or "Role"
	technical_title = f"{role_name} technical"
	if family == "ml":
		return [
			_aptitude(0.20),
			SectionSpec("fundamentals", "Maths & statistics", 0.25, ((NUMERICAL, 0.6), (MCQ_SINGLE, 0.4)), _ML_MATHS),
			SectionSpec("technical", "ML & AI technical", 0.35, ((MCQ_SINGLE, 0.45), (MCQ_MULTI, 0.2), (NUMERICAL, 0.2), (SCENARIO, 0.15)), role_topics),
			SectionSpec("code", "Python & libraries", 0.20, ((CODE_OUTPUT, 0.7), (FIND_BUG, 0.3)), _CODE_TOPICS["python"], "python"),
		]
	if family == "data":
		return [
			_aptitude(),
			SectionSpec("fundamentals", "Statistics", 0.20, ((NUMERICAL, 0.6), (MCQ_SINGLE, 0.4)), _STATS),
			SectionSpec("code", "SQL", 0.30, ((CODE_OUTPUT, 0.5), (MCQ_SINGLE, 0.3), (FIND_BUG, 0.2)), _SQL_TOPICS, "sql"),
			SectionSpec("technical", "Data analysis", 0.25, ((MCQ_SINGLE, 0.5), (SCENARIO, 0.3), (MCQ_MULTI, 0.2)), role_topics),
		]
	if family == "devops":
		return [
			_aptitude(0.20),
			SectionSpec("fundamentals", "OS & networking", 0.30, ((MCQ_SINGLE, 0.5), (MCQ_MULTI, 0.2), (NUMERICAL, 0.3)), _NETWORK_OS),
			SectionSpec("technical", "DevOps & cloud", 0.35, ((MCQ_SINGLE, 0.45), (MCQ_MULTI, 0.25), (SCENARIO, 0.3)), role_topics),
			SectionSpec("code", "Shell & config", 0.15, ((FIND_BUG, 0.6), (CODE_OUTPUT, 0.4)), _CODE_TOPICS["bash"], "bash"),
		]
	if family == "security":
		return [
			_aptitude(0.20),
			SectionSpec("fundamentals", "Networking & OS", 0.30, ((MCQ_SINGLE, 0.5), (MCQ_MULTI, 0.2), (NUMERICAL, 0.3)), _NETWORK_OS),
			SectionSpec("technical", "Security", 0.35, ((MCQ_SINGLE, 0.45), (MCQ_MULTI, 0.25), (SCENARIO, 0.3)), role_topics),
			SectionSpec("code", "Vulnerable code", 0.15, ((FIND_BUG, 1.0),), ("SQL injection", "XSS", "path traversal", "insecure deserialisation", "missing authorisation check", "hard-coded secrets"), language),
		]
	if family == "core":
		return [
			_aptitude(),
			SectionSpec("fundamentals", "Electronics fundamentals", 0.35, ((MCQ_SINGLE, 0.5), (NUMERICAL, 0.35), (MCQ_MULTI, 0.15)), _CORE_FUNDAMENTALS),
			SectionSpec("technical", technical_title, 0.25, ((MCQ_SINGLE, 0.5), (MCQ_MULTI, 0.2), (SCENARIO, 0.3)), role_topics),
			SectionSpec("code", "C programming", 0.15, ((CODE_OUTPUT, 0.6), (FIND_BUG, 0.4)), _CODE_TOPICS["c"], "c"),
		]
	# software: Java / backend / frontend / full stack / QA / mobile / general SE
	return [
		_aptitude(),
		SectionSpec("fundamentals", "CS fundamentals", 0.25, ((MCQ_SINGLE, 0.55), (MCQ_MULTI, 0.2), (NUMERICAL, 0.25)), _CS_FUNDAMENTALS),
		SectionSpec("technical", technical_title, 0.30, ((MCQ_SINGLE, 0.5), (MCQ_MULTI, 0.2), (SCENARIO, 0.3)), role_topics),
		SectionSpec("code", "Code & pseudocode", 0.20, ((CODE_OUTPUT, 0.5), (FIND_BUG, 0.25), (PSEUDOCODE, 0.25)), _CODE_TOPICS.get(language, _CODE_TOPICS["python"]), language),
	]


def _apportion(total: int, weights: list[float], minimum: int = 0) -> list[int]:
	"""Split ``total`` by ``weights`` (largest remainder), each at least ``minimum``."""
	if not weights:
		return []
	scale = sum(weights) or 1.0
	raw = [total * w / scale for w in weights]
	counts = [max(minimum, math.floor(r)) for r in raw]
	while sum(counts) > total:
		index = max(range(len(counts)), key=lambda i: counts[i])
		counts[index] -= 1
	order = sorted(range(len(raw)), key=lambda i: raw[i] - math.floor(raw[i]), reverse=True)
	index = 0
	while sum(counts) < total:
		counts[order[index % len(order)]] += 1
		index += 1
	return counts


def make_slots(spec: SectionSpec, count: int, rng: random.Random) -> list[Slot]:
	"""``count`` question slots for one section: types, difficulties and topics."""
	if count <= 0:
		return []
	type_counts = _apportion(count, [w for _, w in spec.types])
	types = [t for (t, _), n in zip(spec.types, type_counts) for _ in range(n)]
	difficulty_counts = _apportion(count, [w for _, w in DIFFICULTY_MIX])
	difficulties = [d for (d, _), n in zip(DIFFICULTY_MIX, difficulty_counts) for _ in range(n)]
	rng.shuffle(types)
	rng.shuffle(difficulties)
	pools: dict[str, list[str]] = {}
	for question_type in dict.fromkeys(types):
		topics = list(dict(spec.type_topics).get(question_type) or spec.topics) or ["general"]
		rng.shuffle(topics)
		pools[question_type] = topics
	used: dict[str, int] = {}
	slots = []
	for i in range(count):
		pool = pools[types[i]]
		slots.append(Slot(types[i], difficulties[i], pool[used.get(types[i], 0) % len(pool)]))
		used[types[i]] = used.get(types[i], 0) + 1
	return slots


def build_oa_blueprint(
	role_key: str,
	role_title: str = "",
	*,
	role_topics: tuple[str, ...] = (),
	skills: tuple[str, ...] = (),
	total_questions: int | None = None,
	rng: random.Random | None = None,
) -> OABlueprint:
	"""The shape of one OA attempt for this role."""
	rng = rng or random.Random()
	total = total_questions if total_questions else rng.randint(MIN_QUESTIONS, MAX_QUESTIONS)
	family = oa_family(role_key, role_title)
	language = snippet_language(role_key, role_title, skills)
	if family == "core":
		language = "c"
	specs = _sections(family, role_title, tuple(role_topics) or ("core concepts of the role",), language)
	counts = _apportion(total, [spec.share for spec in specs], minimum=2)
	sections = [
		SectionPlan(spec.key, spec.title, count, spec.language, spec, make_slots(spec, count, rng))
		for spec, count in zip(specs, counts)
	]
	return OABlueprint(family, role_key, role_title, total, language, sections)


__all__ = [
	"CODE_OUTPUT",
	"CODE_TYPES",
	"DIFFICULTY_MIX",
	"FIND_BUG",
	"MAX_QUESTIONS",
	"MCQ_MULTI",
	"MCQ_SINGLE",
	"MIN_QUESTIONS",
	"NUMERICAL",
	"OABlueprint",
	"PSEUDOCODE",
	"QUESTION_TYPES",
	"SCENARIO",
	"SectionPlan",
	"SectionSpec",
	"Slot",
	"build_oa_blueprint",
	"make_slots",
	"oa_family",
]
