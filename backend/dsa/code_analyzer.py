"""Static analysis helpers for submitted DSA code."""

from __future__ import annotations

import ast
import re
from typing import Any, Mapping

from backend.database.db_errors import OptimalityStatus

_LANGUAGE_ALIASES = {
	"python": "python",
	"py": "python",
	"cpp": "cpp",
	"c++": "cpp",
	"cplusplus": "cpp",
	"cxx": "cpp",
	"java": "java",
}

_STOPWORDS = {
	"array",
	"based",
	"code",
	"current",
	"edge",
	"input",
	"need",
	"problem",
	"return",
	"should",
	"solve",
	"using",
	"value",
	"values",
}

_HASH_STRUCTURES = {"dict", "set", "unordered_map", "unordered_set", "hashmap", "hashset", "map"}


def _problem_value(problem: Any, name: str, default: Any = None) -> Any:
	if isinstance(problem, Mapping):
		return problem.get(name, default)
	return getattr(problem, name, default)


def _coerce_string_tuple(value: Any) -> tuple[str, ...]:
	if value is None:
		return ()
	if isinstance(value, (list, tuple, set)):
		return tuple(str(item or "").strip() for item in value if str(item or "").strip())
	text = str(value or "").strip()
	return (text,) if text else ()


def _normalize_language(language: str | None) -> str:
	return _LANGUAGE_ALIASES.get(str(language or "python").strip().casefold(), "python")


def _extract_problem_keywords(problem: Any) -> set[str]:
	fragments = [
		*(_coerce_string_tuple(_problem_value(problem, "tags"))),
		*(_coerce_string_tuple(_problem_value(problem, "hints"))),
		str(_problem_value(problem, "title", "") or ""),
	]
	keywords: set[str] = set()
	for fragment in fragments:
		for token in re.findall(r"[a-zA-Z_]{3,}", fragment.casefold().replace("-", "_")):
			if token in _STOPWORDS:
				continue
			keywords.add(token)
	return keywords


def _call_name(node: ast.AST) -> str:
	if isinstance(node, ast.Name):
		return node.id
	if isinstance(node, ast.Attribute):
		base = _call_name(node.value)
		return f"{base}.{node.attr}" if base else node.attr
	return ""


def _last_call_segment(name: str) -> str:
	return name.rsplit(".", 1)[-1]


def _is_empty_guard(test_node: ast.AST) -> bool:
	try:
		expression = ast.unparse(test_node).replace(" ", "").casefold()
	except Exception:
		return False
	return expression.startswith("not") or "len(" in expression and any(token in expression for token in ("==0", "<=1", "<1"))


def _function_has_early_return(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
	body = list(node.body)
	if len(body) <= 1:
		return False
	for statement in body[:-1]:
		if isinstance(statement, ast.Return):
			return True
		if isinstance(statement, ast.If) and any(isinstance(child, ast.Return) for child in statement.body):
			return True
	return False


class _PythonAnalyzer(ast.NodeVisitor):
	def __init__(self) -> None:
		self.max_loop_depth = 0
		self._loop_depth = 0
		self.uses_recursion = False
		self.recursive_functions: set[str] = set()
		self.current_functions: list[str] = []
		self.data_structures: set[str] = set()
		self.algorithm_signals: set[str] = set()
		self.uses_memoization = False
		self.uses_sort = False
		self.uses_binary_search = False
		self.handles_empty_input = False
		self.has_early_return = False
		self.edge_case_signals: set[str] = set()

	def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
		if _function_has_early_return(node):
			self.has_early_return = True
		for decorator in node.decorator_list:
			if _last_call_segment(_call_name(decorator)) in {"cache", "lru_cache"}:
				self.uses_memoization = True
		self.current_functions.append(node.name)
		self.generic_visit(node)
		self.current_functions.pop()

	visit_AsyncFunctionDef = visit_FunctionDef

	def visit_For(self, node: ast.For) -> None:
		self._loop_depth += 1
		self.max_loop_depth = max(self.max_loop_depth, self._loop_depth)
		self.generic_visit(node)
		self._loop_depth -= 1

	visit_AsyncFor = visit_For

	def visit_While(self, node: ast.While) -> None:
		self._loop_depth += 1
		self.max_loop_depth = max(self.max_loop_depth, self._loop_depth)
		if _is_empty_guard(node.test):
			self.handles_empty_input = True
			self.edge_case_signals.add("empty_input_guard")
		self.generic_visit(node)
		self._loop_depth -= 1

	def visit_comprehension(self, node: ast.comprehension) -> None:
		self._loop_depth += 1
		self.max_loop_depth = max(self.max_loop_depth, self._loop_depth)
		self.generic_visit(node)
		self._loop_depth -= 1

	def visit_Call(self, node: ast.Call) -> None:
		name = _call_name(node.func)
		last_segment = _last_call_segment(name).casefold()
		if last_segment == "sorted" or name.endswith(".sort"):
			self.uses_sort = True
			self.algorithm_signals.add("sorting")
		if last_segment in {"bisect", "bisect_left", "bisect_right"}:
			self.uses_binary_search = True
			self.algorithm_signals.add("binary_search")
		if last_segment in {"set", "dict", "list", "tuple"}:
			self.data_structures.add(last_segment)
		if self.current_functions and last_segment == self.current_functions[-1].casefold():
			self.uses_recursion = True
			self.recursive_functions.add(self.current_functions[-1])
		self.generic_visit(node)

	def visit_Assign(self, node: ast.Assign) -> None:
		target_names = {
			str(target.id).casefold()
			for target in node.targets
			if isinstance(target, ast.Name)
		}
		if target_names & {"memo", "cache", "dp"}:
			self.uses_memoization = True
			self.algorithm_signals.add("memoization")
		self.generic_visit(node)

	def visit_Dict(self, node: ast.Dict) -> None:
		self.data_structures.add("dict")
		self.generic_visit(node)

	def visit_Set(self, node: ast.Set) -> None:
		self.data_structures.add("set")
		self.generic_visit(node)

	def visit_List(self, node: ast.List) -> None:
		self.data_structures.add("list")
		self.generic_visit(node)

	def visit_Tuple(self, node: ast.Tuple) -> None:
		self.data_structures.add("tuple")
		self.generic_visit(node)

	def visit_If(self, node: ast.If) -> None:
		if _is_empty_guard(node.test):
			self.handles_empty_input = True
			self.edge_case_signals.add("empty_input_guard")
		if any(isinstance(statement, ast.Return) for statement in node.body):
			self.has_early_return = True
		self.generic_visit(node)


def _extract_cpp_java_data_structures(code_text: str) -> set[str]:
	patterns = {
		"vector": r"\bvector\s*<",
		"arraylist": r"\barraylist\s*<",
		"list": r"\blist\s*<",
		"map": r"\b(?:map|unordered_map|hashmap|treemap)\s*<",
		"set": r"\b(?:set|unordered_set|hashset|treeset)\s*<",
		"queue": r"\b(?:queue|deque|arraydeque)\s*<",
		"stack": r"\bstack\s*<",
		"heap": r"\b(?:priority_queue|priorityqueue)\s*<",
	}
	found: set[str] = set()
	for label, pattern in patterns.items():
		if re.search(pattern, code_text):
			found.add(label)
	return found


def _heuristic_loop_depth(code_text: str) -> int:
	loop_count = len(re.findall(r"\b(?:for|while)\b", code_text))
	if loop_count <= 0:
		return 0
	if loop_count == 1:
		return 1
	if loop_count == 2:
		return 2
	return 3


def _derive_complexity(
	*,
	loop_depth: int,
	uses_sort: bool,
	uses_binary_search: bool,
	uses_recursion: bool,
	uses_memoization: bool,
	efficient_data_structure_signal: bool,
) -> tuple[str, str, str]:
	if uses_recursion and not uses_memoization and loop_depth >= 1:
		time_complexity = "O(2^n)"
	elif uses_binary_search and loop_depth <= 1 and not uses_sort:
		time_complexity = "O(log n)"
	elif uses_sort and loop_depth <= 1:
		time_complexity = "O(n log n)"
	else:
		time_complexity = {
			0: "O(1)",
			1: "O(n)",
			2: "O(n^2)",
		}.get(min(loop_depth, 2), "O(n^3)")

	if uses_memoization or uses_recursion or efficient_data_structure_signal:
		space_complexity = "O(n)"
	else:
		space_complexity = "O(1)"

	if time_complexity in {"O(2^n)", "O(n^3)"}:
		brute_force_likelihood = "high"
	elif time_complexity == "O(n^2)" and not uses_sort and not efficient_data_structure_signal:
		brute_force_likelihood = "high"
	elif time_complexity == "O(n^2)":
		brute_force_likelihood = "medium"
	elif time_complexity == "O(n log n)" and not efficient_data_structure_signal:
		brute_force_likelihood = "medium"
	else:
		brute_force_likelihood = "low"

	return time_complexity, space_complexity, brute_force_likelihood


def _infer_optimality(
	*,
	problem_keywords: set[str],
	brute_force_likelihood: str,
	uses_sort: bool,
	uses_binary_search: bool,
	data_structures: set[str],
	loop_depth: int,
) -> str:
	if brute_force_likelihood == "high":
		return OptimalityStatus.NO.value
	if "binary_search" in problem_keywords or {"binary", "search"}.issubset(problem_keywords):
		return OptimalityStatus.YES.value if uses_binary_search else OptimalityStatus.UNKNOWN.value
	if {"two_pointers", "sliding_window"} & problem_keywords:
		return OptimalityStatus.YES.value if loop_depth <= 1 else OptimalityStatus.UNKNOWN.value
	if {"hashing", "hashmap", "set", "map"} & problem_keywords:
		return OptimalityStatus.YES.value if data_structures & _HASH_STRUCTURES else OptimalityStatus.UNKNOWN.value
	if "sorting" in problem_keywords:
		return OptimalityStatus.YES.value if uses_sort else OptimalityStatus.UNKNOWN.value
	if brute_force_likelihood == "low":
		return OptimalityStatus.UNKNOWN.value
	return OptimalityStatus.UNKNOWN.value


def _build_strategy_summary(
	*,
	uses_sort: bool,
	uses_binary_search: bool,
	uses_recursion: bool,
	uses_memoization: bool,
	data_structures: set[str],
	loop_depth: int,
) -> str:
	parts: list[str] = []
	if uses_binary_search:
		parts.append("binary search")
	if uses_sort:
		parts.append("sorting")
	if data_structures & _HASH_STRUCTURES:
		parts.append("hash-based lookup")
	if uses_memoization:
		parts.append("memoization")
	if uses_recursion:
		parts.append("recursion")
	if not parts:
		parts.append("iterative scanning" if loop_depth <= 1 else "nested iteration")
	return ", ".join(parts)


def _build_summary(
	*,
	strategy_summary: str,
	time_complexity: str,
	space_complexity: str,
	brute_force_likelihood: str,
	optimality_guess: str,
) -> str:
	optimality_text = {
		OptimalityStatus.YES.value: "looks near-optimal",
		OptimalityStatus.NO.value: "still looks brute-force",
		OptimalityStatus.UNKNOWN.value: "has uncertain optimality",
	}.get(optimality_guess, "has uncertain optimality")
	return (
		f"Detected {strategy_summary}; estimated {time_complexity} time and {space_complexity} space. "
		f"Brute-force likelihood is {brute_force_likelihood} and the solution {optimality_text}."
	)


def analyze_submission_code(
	code: str,
	*,
	language: str = "python",
	problem: Any | None = None,
) -> dict[str, Any]:
	"""Return a deterministic best-effort analysis for a DSA submission."""

	resolved_language = _normalize_language(language)
	problem_keywords = _extract_problem_keywords(problem)
	code_text = str(code or "")
	lowered_code = code_text.casefold()

	if resolved_language == "python":
		try:
			tree = ast.parse(code_text or "")
			analyzer = _PythonAnalyzer()
			analyzer.visit(tree)
			data_structures = set(analyzer.data_structures)
			loop_depth = analyzer.max_loop_depth
			uses_sort = analyzer.uses_sort
			uses_binary_search = analyzer.uses_binary_search or all(token in lowered_code for token in ("left", "right", "mid"))
			uses_recursion = analyzer.uses_recursion
			uses_memoization = analyzer.uses_memoization
			handles_empty_input = analyzer.handles_empty_input
			has_early_return = analyzer.has_early_return
			edge_case_signals = set(analyzer.edge_case_signals)
			analysis_mode = "ast"
		except SyntaxError as exc:
			data_structures = set()
			loop_depth = _heuristic_loop_depth(lowered_code)
			uses_sort = "sorted(" in lowered_code or ".sort(" in lowered_code
			uses_binary_search = "bisect" in lowered_code
			uses_recursion = False
			uses_memoization = False
			handles_empty_input = "if not" in lowered_code
			has_early_return = "return" in lowered_code
			edge_case_signals = {"syntax_fallback"}
			analysis_mode = f"python_fallback:{exc.msg}"
	else:
		data_structures = _extract_cpp_java_data_structures(lowered_code)
		loop_depth = _heuristic_loop_depth(lowered_code)
		uses_sort = any(token in lowered_code for token in ("sort(", "arrays.sort", "collections.sort", "std::sort"))
		uses_binary_search = any(token in lowered_code for token in ("binary_search", "lower_bound", "upper_bound", "collections.binarysearch", "arrays.binarysearch"))
		uses_recursion = bool(re.search(r"\bsolve\s*\(", lowered_code) and lowered_code.count("solve(") > 1)
		uses_memoization = any(token in lowered_code for token in ("memo", "cache", "dp"))
		handles_empty_input = any(token in lowered_code for token in ("size()==0", "isempty()", "empty()", "length==0", "if(!"))
		has_early_return = "return" in lowered_code
		edge_case_signals = {"empty_input_guard"} if handles_empty_input else set()
		analysis_mode = "heuristic"

	efficient_data_structure_signal = bool(data_structures & _HASH_STRUCTURES or {"deque", "queue", "stack", "heap"} & data_structures)
	time_complexity, space_complexity, brute_force_likelihood = _derive_complexity(
		loop_depth=loop_depth,
		uses_sort=uses_sort,
		uses_binary_search=uses_binary_search,
		uses_recursion=uses_recursion,
		uses_memoization=uses_memoization,
		efficient_data_structure_signal=efficient_data_structure_signal,
	)
	optimality_guess = _infer_optimality(
		problem_keywords=problem_keywords,
		brute_force_likelihood=brute_force_likelihood,
		uses_sort=uses_sort,
		uses_binary_search=uses_binary_search,
		data_structures=data_structures,
		loop_depth=loop_depth,
	)
	strategy_summary = _build_strategy_summary(
		uses_sort=uses_sort,
		uses_binary_search=uses_binary_search,
		uses_recursion=uses_recursion,
		uses_memoization=uses_memoization,
		data_structures=data_structures,
		loop_depth=loop_depth,
	)
	algorithm_signals = sorted(
		{
			*(data_structures & _HASH_STRUCTURES and {"hashing"} or set()),
			*(uses_sort and {"sorting"} or set()),
			*(uses_binary_search and {"binary_search"} or set()),
			*(uses_recursion and {"recursion"} or set()),
			*(uses_memoization and {"memoization"} or set()),
		}
	)
	return {
		"language": resolved_language,
		"analysis_mode": analysis_mode,
		"max_loop_nesting_depth": loop_depth,
		"uses_recursion": uses_recursion,
		"uses_memoization": uses_memoization,
		"data_structures_used": sorted(data_structures),
		"algorithm_signals": algorithm_signals,
		"handles_empty_input": handles_empty_input,
		"has_early_return": has_early_return,
		"edge_case_signals": sorted(edge_case_signals),
		"estimated_time_complexity": time_complexity,
		"estimated_space_complexity": space_complexity,
		"brute_force_likelihood": brute_force_likelihood,
		"optimality_guess": optimality_guess,
		"efficient_data_structure_signal": efficient_data_structure_signal,
		"strategy_summary": strategy_summary,
		"analysis_summary": _build_summary(
			strategy_summary=strategy_summary,
			time_complexity=time_complexity,
			space_complexity=space_complexity,
			brute_force_likelihood=brute_force_likelihood,
			optimality_guess=optimality_guess,
		),
	}


__all__ = ["analyze_submission_code"]
