"""Check the answer key of generated assessment questions before use.

A wrong answer key is worse than no question: the candidate answers correctly
and is told they are wrong. So every generated question is checked where it
can be:

- code output / pseudocode: the code (or a Python translation of the
  pseudocode) is run on Judge0 and its real output must match an option;
- numerical: the model gives an arithmetic expression for the answer, which
  is evaluated here (no eval(): a small AST calculator) and must match an
  option's number.

For real code, Judge0's output is the truth: if it matches a different
option than the model claimed, the key is corrected. For a numerical formula
or a pseudocode translation, the model wrote both the key and the check, so a
disagreement means one of them is wrong and the question is rejected - and
agreement only proves the two are consistent, which is why those questions
also get a second model's opinion (``oa_generator.cross_check``).
Conceptual questions can't be executed and stay "unverified".
"""

from __future__ import annotations

import ast
import logging
import math
import operator
import re
from collections.abc import Callable, Mapping
from typing import Any

_LOGGER = logging.getLogger(__name__)

VERIFIED = "verified"
UNVERIFIED = "unverified"
REJECTED = "rejected"

RUNNABLE_LANGUAGES = frozenset({"python", "java", "cpp", "c", "javascript", "bash"})

# ---------------------------------------------------------------------------
# Safe arithmetic
# ---------------------------------------------------------------------------

_BINARY = {
	ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
	ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS: dict[str, Callable[..., float]] = {
	"comb": math.comb, "perm": math.perm, "factorial": math.factorial, "sqrt": math.sqrt, "log": math.log,
	"log2": math.log2, "log10": math.log10, "exp": math.exp, "ceil": math.ceil, "floor": math.floor,
	"gcd": math.gcd, "lcm": math.lcm, "round": round, "abs": abs, "min": min, "max": max, "pow": pow,
}
_CONSTANTS = {"pi": math.pi, "e": math.e}


class UnsafeExpression(ValueError):
	pass


def safe_eval(expression: str) -> float:
	"""Evaluate a plain arithmetic expression; anything else raises."""
	text = str(expression or "").strip().replace("^", "**").replace("math.", "")
	if not text or len(text) > 300:
		raise UnsafeExpression("empty or too long")
	tree = ast.parse(text, mode="eval")

	def visit(node: ast.AST) -> Any:
		if isinstance(node, ast.Expression):
			return visit(node.body)
		if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
			return node.value
		if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
			left, right = visit(node.left), visit(node.right)
			if isinstance(node.op, ast.Pow) and abs(right) > 100:
				raise UnsafeExpression("exponent too large")
			return _BINARY[type(node.op)](left, right)
		if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
			return _UNARY[type(node.op)](visit(node.operand))
		if isinstance(node, ast.Name) and node.id in _CONSTANTS:
			return _CONSTANTS[node.id]
		if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS and not node.keywords:
			args = [visit(arg) for arg in node.args]
			if node.func.id in {"factorial", "comb", "perm"} and any(abs(a) > 1000 for a in args):
				raise UnsafeExpression("argument too large")
			return _FUNCTIONS[node.func.id](*args)
		raise UnsafeExpression(f"not allowed: {type(node).__name__}")

	value = visit(tree)
	return float(value)


_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)?|-?\.\d+")


def parse_number(text: str) -> float | None:
	"""The first number in an option ("₹1,250", "37.5%", "3/8", "45 km/h")."""
	match = _NUMBER.search(str(text or ""))
	if not match:
		return None
	raw = match.group(0).replace(",", "").replace(" ", "")
	try:
		if "/" in raw:
			top, bottom = raw.split("/", 1)
			return float(top) / float(bottom) if float(bottom) else None
		return float(raw)
	except ValueError:
		return None


def _close(a: float, b: float) -> bool:
	return abs(a - b) <= max(1e-6, 0.006 * abs(b), 0.006 * abs(a)) or round(a, 2) == round(b, 2)


# ---------------------------------------------------------------------------
# Output matching
# ---------------------------------------------------------------------------


def normalize_output(text: str) -> str:
	"""Whitespace-insensitive form, so "1 2 3" matches a program printing 1\\n2\\n3."""
	return re.sub(r"\s+", " ", str(text or "").replace("\\n", "\n")).strip()


def _matching_options(value_text: str, options: list[str]) -> list[int]:
	target = normalize_output(value_text)
	return [i for i, option in enumerate(options) if normalize_output(option) == target]


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_numerical(question: Mapping[str, Any]) -> tuple[str, list[int] | None]:
	expression = str(question.get("verify_expression") or "").strip()
	if not expression:
		return UNVERIFIED, None
	try:
		value = safe_eval(expression)
	except (UnsafeExpression, SyntaxError, ArithmeticError, ValueError, TypeError) as exc:
		_LOGGER.info("Numerical check expression unusable (%s): %s", exc, expression[:80])
		return UNVERIFIED, None
	options = list(question.get("options") or [])
	matches = [i for i, option in enumerate(options) if (n := parse_number(option)) is not None and _close(n, value)]
	# The model wrote both the key and the formula: only agreement counts.
	if len(matches) != 1 or matches != sorted(question.get("correct") or []):
		return REJECTED, None
	return VERIFIED, matches


def check_program_output(
	question: Mapping[str, Any],
	run: Callable[[str, str], Mapping[str, Any]],
) -> tuple[str, list[int] | None]:
	"""Run the code (or the pseudocode's Python translation) and match its output."""
	if question.get("type") == "pseudocode":
		code, language = str(question.get("verify_python") or ""), "python"
	else:
		code, language = str(question.get("code") or ""), str(question.get("language") or "")
	if not code.strip() or language not in RUNNABLE_LANGUAGES:
		return UNVERIFIED, None
	try:
		result = run(code, language)
	except Exception as exc:  # Judge0 down or unreachable: can't check, don't reject
		_LOGGER.info("Judge0 unavailable for an answer check: %s", exc)
		return UNVERIFIED, None
	status = result.get("status") if isinstance(result.get("status"), Mapping) else {}
	stdout = str(result.get("stdout") or "")
	error_text = f"{result.get('stderr') or ''}{result.get('compile_output') or ''}"
	if int(status.get("id") or 0) != 3:  # 3 = Accepted
		if "ModuleNotFoundError" in error_text or "ImportError" in error_text:
			return UNVERIFIED, None  # e.g. numpy isn't installed on Judge0
		if _environment_failure(status, error_text):
			return UNVERIFIED, None  # the sandbox failed, not the question
		return REJECTED, None
	matches = _matching_options(stdout, list(question.get("options") or []))
	if len(matches) != 1:
		return REJECTED, None
	if question.get("type") == "pseudocode" and matches != sorted(question.get("correct") or []):
		return REJECTED, None  # the translation is the model's too: no re-keying
	return VERIFIED, matches


# Judge0 under Docker Desktop can't start a JVM with per-process memory
# limits on ("Could not reserve enough space for ... object heap"), and
# "Internal Error" is the sandbox itself. Neither says the question is wrong.
_ENVIRONMENT_ERRORS = ("Error occurred during initialization of VM", "Could not reserve enough space")


def _environment_failure(status: Mapping[str, Any], error_text: str) -> bool:
	return int(status.get("id") or 0) == 13 or any(marker in error_text for marker in _ENVIRONMENT_ERRORS)


def verify_question(
	question: Mapping[str, Any],
	run: Callable[[str, str], Mapping[str, Any]] | None = None,
) -> tuple[str, list[int] | None]:
	"""(status, corrected answer indices or None)."""
	question_type = question.get("type")
	if question_type == "numerical":
		return check_numerical(question)
	if question_type in {"code_output", "pseudocode"} and run is not None:
		return check_program_output(question, run)
	return UNVERIFIED, None


__all__ = [
	"REJECTED",
	"RUNNABLE_LANGUAGES",
	"UNVERIFIED",
	"VERIFIED",
	"normalize_output",
	"parse_number",
	"safe_eval",
	"verify_question",
]
