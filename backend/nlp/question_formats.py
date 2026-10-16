"""Question formats for the technical round.

Explanation-only questions ("explain X", "what is Y") let a candidate who has
memorised definitions score as well as one who can actually use the skill.
Real technical screens also hand you a short piece of code and ask what it
prints, ask you to spot a bug, or give a small calculation. So each of the
round's targets gets a format, and the mix depends on the role:

  software (Java, backend, frontend, ...): code output x2, find the bug,
                                            concept x2, scenario
  ml / data science                       : numerical x2, code output,
                                            concept x2, scenario
  data analyst                            : SQL result x2, numerical,
                                            concept x2, scenario
  devops / cloud                          : config issue x2, concept x2,
                                            scenario x2
  security                                : find the vulnerability x2,
                                            concept x2, scenario x2

Anything shown (code, a tiny table, the numbers of a problem) goes in the
turn's ``snippet`` and is displayed, never spoken: the interviewer's words are
read aloud by TTS.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

CONCEPT = "concept"
CODE_OUTPUT = "code_output"
FIND_BUG = "find_bug"
NUMERICAL = "numerical"
SQL_OUTPUT = "sql_output"
CONFIG_ISSUE = "config_issue"
VULNERABILITY = "vulnerability"
SCENARIO = "scenario"

SNIPPET_FORMATS = frozenset({CODE_OUTPUT, FIND_BUG, NUMERICAL, SQL_OUTPUT, CONFIG_ISSUE, VULNERABILITY})

# Six targets per round (TECHNICAL_TARGET_COUNT). Snippet questions are
# interleaved so two code reads never come back to back.
_FORMAT_MIX: dict[str, tuple[str, ...]] = {
	"software": (CONCEPT, CODE_OUTPUT, SCENARIO, FIND_BUG, CONCEPT, CODE_OUTPUT),
	"ml": (CONCEPT, NUMERICAL, CODE_OUTPUT, CONCEPT, NUMERICAL, SCENARIO),
	"data": (CONCEPT, SQL_OUTPUT, NUMERICAL, CONCEPT, SQL_OUTPUT, SCENARIO),
	"devops": (CONCEPT, CONFIG_ISSUE, SCENARIO, CONCEPT, CONFIG_ISSUE, SCENARIO),
	"security": (CONCEPT, VULNERABILITY, SCENARIO, CONCEPT, VULNERABILITY, SCENARIO),
}

_FAMILY_HINTS: tuple[tuple[str, frozenset[str]], ...] = (
	("security", frozenset({"security", "cyber", "cybersecurity", "soc", "pentest", "penetration"})),
	("ml", frozenset({"ml", "ai", "machine", "learning", "scientist", "nlp", "vision", "llm", "deep"})),
	("data", frozenset({"data", "analyst", "analytics", "bi", "etl", "warehouse"})),
	("devops", frozenset({"devops", "cloud", "sre", "platform", "infrastructure", "reliability"})),
)


def _tokens(*values: str) -> set[str]:
	return set(re.split(r"[^a-z0-9+#]+", " ".join(values).casefold())) - {""}


def role_family(role_key: str | None, role_title: str | None = "") -> str:
	"""software | ml | data | devops | security."""
	tokens = _tokens(role_key or "", role_title or "")
	# "Data engineer" and "data scientist" are not analyst roles.
	if "data" in tokens and "scientist" in tokens:
		return "ml"
	if "data" in tokens and "engineer" in tokens and "analyst" not in tokens:
		return "software"
	for family, hints in _FAMILY_HINTS:
		if tokens & hints:
			return family
	return "software"


def format_mix(role_key: str | None, role_title: str | None, count: int) -> list[str]:
	"""Formats for the round's targets, in order."""
	mix = _FORMAT_MIX[role_family(role_key, role_title)]
	return [mix[index % len(mix)] for index in range(max(0, count))]


_MATHS_HINTS = frozenset({
	"machine", "learning", "ml", "statistics", "statistical", "probability", "regression", "classification",
	"model", "models", "neural", "deep", "metrics", "evaluation", "pandas", "numpy", "pytorch", "tensorflow",
	"scikit", "sklearn", "data", "analytics", "optimisation", "optimization", "gradient", "nlp", "vision",
})


def place_numerical(formats: list[str], focus_skills: list[str]) -> list[str]:
	"""Move numerical slots onto the most maths-friendly targets.

	A calculation fits "Model evaluation" or "NumPy"; it doesn't fit "Django".
	The mix fixes how many numerical questions there are, this decides where.
	"""
	formats = list(formats)
	numeric_slots = [i for i, f in enumerate(formats) if f == NUMERICAL]
	if not numeric_slots:
		return formats

	def maths_score(index: int) -> int:
		return len(_tokens(focus_skills[index]) & _MATHS_HINTS)

	best = sorted(range(len(formats)), key=lambda i: (-maths_score(i), i))[: len(numeric_slots)]
	for slot in numeric_slots:
		if slot in best or maths_score(slot) > 0:
			continue
		swap = next((i for i in best if formats[i] != NUMERICAL and maths_score(i) > 0), None)
		if swap is not None:
			formats[slot], formats[swap] = formats[swap], formats[slot]
	return formats


# (language, words that mean the candidate works in it). Checked against the
# role first, then the candidate's skills.
_LANGUAGES: tuple[tuple[str, frozenset[str]], ...] = (
	("java", frozenset({"java", "spring", "springboot", "kotlin"})),
	("python", frozenset({"python", "django", "flask", "fastapi", "pandas", "numpy", "pytorch", "tensorflow"})),
	("typescript", frozenset({"typescript"})),
	("javascript", frozenset({"javascript", "js", "react", "node", "nodejs", "vue", "angular", "frontend", "express"})),
	("cpp", frozenset({"c++", "cpp"})),
	("csharp", frozenset({"c#", "csharp", ".net", "dotnet"})),
	("go", frozenset({"go", "golang"})),
)


def snippet_language(role_key: str | None, role_title: str | None, skills: Iterable[str] = ()) -> str:
	"""The language code snippets should be written in for this candidate."""
	family = role_family(role_key, role_title)
	role_tokens = _tokens(role_key or "", role_title or "")
	for language, hints in _LANGUAGES:
		if role_tokens & hints:
			return language
	if family in {"ml", "data"}:
		return "python"
	skill_tokens = _tokens(*[str(s) for s in skills])
	for language, hints in _LANGUAGES:
		if skill_tokens & hints:
			return language
	return "python"


# What a good "what does this print?" question in each language turns on:
# behaviour people get wrong, not syntax trivia.
_TRICKY_AREAS = {
	"java": "== vs equals on Strings, Integer caching (127 vs 128), pass-by-value of object references, "
	"a finally block overriding return, ArrayList.remove(int) vs remove(Object), mutating a HashMap key, "
	"static vs instance fields, integer division and overflow",
	"python": "mutable default arguments, list aliasing and shallow copies, late-binding closures in loops, "
	"is vs ==, generator exhaustion, slicing, dict and set behaviour, integer vs float division",
	"javascript": "== vs ===, var hoisting and closures in loops, this binding, the event loop order of "
	"setTimeout vs Promise, default array sort, truthy and falsy values",
	"typescript": "== vs ===, closures in loops, this binding, the event loop order of setTimeout vs Promise, "
	"structural typing surprises, default array sort",
	"cpp": "copies vs references, object slicing, virtual dispatch, integer overflow, pre vs post increment, "
	"vector iterator invalidation",
	"csharp": "value vs reference types, string immutability, LINQ deferred execution, boxing, integer division",
	"go": "slices sharing a backing array, closures over loop variables, nil interfaces, map iteration order, "
	"defer evaluation order",
}

_SQL_HINTS = frozenset({"sql", "database", "databases", "mysql", "postgresql", "postgres", "sqlite", "query", "queries", "joins"})


def target_format_and_language(question_format: str, focus_skill: str, default_language: str) -> tuple[str, str]:
	"""The snippet language for one target, and its format adjusted to it.

	A "Database modelling and SQL" target shows SQL even in a Java round, and
	a "Python" target in a Java round shows Python. A code-output question on
	SQL becomes "what does this query return?".
	"""
	tokens = _tokens(focus_skill)
	if tokens & _SQL_HINTS:
		return (SQL_OUTPUT if question_format == CODE_OUTPUT else question_format), "sql"
	for language, hints in _LANGUAGES:
		if tokens & hints:
			return question_format, language
	return question_format, default_language


_LANGUAGE_NAMES = {
	"java": "Java", "python": "Python", "typescript": "TypeScript", "javascript": "JavaScript",
	"cpp": "C++", "csharp": "C#", "go": "Go",
}


def format_instruction(question_format: str, language: str) -> str:
	"""What the interviewer must do for a target of this format."""
	lang = _LANGUAGE_NAMES.get(language, language)
	which_language = (
		f"Write it in {lang}, unless the focus skill is itself another language, SQL or a tool with its own "
		"syntax - then use that."
	)
	tricky = _TRICKY_AREAS.get(language)
	tricky_hint = f" Good {lang} material: {tricky}." if tricky else ""
	shown = (
		' Put what you show in the JSON "snippet" field, never in your spoken words; '
		'say something like "Have a look at the {thing} on your screen" instead.'
	)
	instructions = {
		CONCEPT: "Ask a conceptual question: why, when, how does it work, or compare two approaches. No snippet.",
		SCENARIO: "Give a short, realistic work situation and ask what they would check or do first, and why. No snippet.",
		CODE_OUTPUT: (
			"Show a short snippet (5 to 12 lines, complete and runnable, standard library only, deterministic "
			"output) built around one behaviour of the focus skill that people commonly get wrong - not a "
			"trivial loop - and ask what it prints and why. The first ideal point must be the exact output, "
			f'written as "Output: ...". {which_language}{tricky_hint}' + shown.format(thing="code")
		),
		FIND_BUG: (
			"Show a short snippet (5 to 12 lines) with exactly one realistic bug related to the focus skill - "
			"the kind that passes a quick read and fails in production - and ask what is wrong and how to fix "
			f"it. Ideal points: the bug, the fix, why it happens. {which_language}" + shown.format(thing="code")
		),
		NUMERICAL: (
			"Give a small numerical problem from this role's core maths that a fresher can solve on paper in "
			"under two minutes - for example precision, recall or F1 from a 2x2 confusion matrix, one gradient "
			"descent step, accuracy on an imbalanced set, a probability, a mean and standard deviation - framed "
			"around the focus skill where that is natural. Show the numbers as plain text with language "
			'"text". The first ideal point must be the exact answer with the working, written as "Answer: ...".'
			+ shown.format(thing="problem")
		),
		SQL_OUTPUT: (
			"Show a tiny table (at most 5 rows, as aligned plain text) and one SQL query on it that exercises "
			"the focus skill (GROUP BY, JOIN, NULL handling, window function...), with language \"sql\", and "
			'ask what the query returns. The first ideal point must be the exact result, written as "Result: ...".'
			+ shown.format(thing="table and query")
		),
		CONFIG_ISSUE: (
			"Show a short Dockerfile, YAML or shell snippet (5 to 12 lines) with exactly one realistic problem "
			"related to the focus skill, and ask what is wrong and what would happen. Ideal points: the problem, "
			"its effect, the fix." + shown.format(thing="file")
		),
		VULNERABILITY: (
			"Show a short snippet (5 to 12 lines) with exactly one security vulnerability related to the focus "
			"skill (injection, unsafe deserialisation, missing auth check...), and ask what the risk is and how "
			f"to fix it. Ideal points: the vulnerability, how it is exploited, the fix. {which_language}"
			+ shown.format(thing="code")
		),
	}
	return instructions.get(question_format, instructions[CONCEPT])


__all__ = [
	"CODE_OUTPUT",
	"CONCEPT",
	"FIND_BUG",
	"NUMERICAL",
	"SCENARIO",
	"SNIPPET_FORMATS",
	"SQL_OUTPUT",
	"format_instruction",
	"format_mix",
	"place_numerical",
	"target_format_and_language",
	"role_family",
	"snippet_language",
]
