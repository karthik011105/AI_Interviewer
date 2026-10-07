"""Judge0 execution helpers for DSA submissions."""

from __future__ import annotations

import ast
import base64
import binascii
import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from backend.config import get_settings
from backend.metrics import judge0_request_duration_seconds, judge0_requests_total

_HARNESS_START_MARKER = "__INTERVIEW_SIMULATOR_RESULTS_START__"
_HARNESS_END_MARKER = "__INTERVIEW_SIMULATOR_RESULTS_END__"

# Judge0 status IDs that indicate execution is complete (terminal).
# IDs 1 (In Queue) and 2 (Processing) are non-terminal — we must poll.
# IDs 3–14 are all terminal (Accepted, Wrong Answer, TLE, runtime errors, etc.).
_JUDGE0_TERMINAL_STATUS_IDS: frozenset[int] = frozenset(range(3, 15))
# Poll up to 30 times with 1-second intervals = 30 seconds max.
# This gives local Judge0 workers extra headroom when the compile queue is busy.
_POLL_MAX_ATTEMPTS = 30
_POLL_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class DSALanguageDefinition:
	key: str
	label: str
	file_name: str


_SUPPORTED_DSA_LANGUAGES: tuple[DSALanguageDefinition, ...] = (
	DSALanguageDefinition(key="python", label="Python 3", file_name="solve.py"),
	DSALanguageDefinition(key="cpp", label="C++17", file_name="solve.cpp"),
	DSALanguageDefinition(key="java", label="Java 17", file_name="Main.java"),
)

_DSA_LANGUAGE_ALIASES = {
	"py": "python",
	"python": "python",
	"python3": "python",
	"c++": "cpp",
	"cpp": "cpp",
	"cxx": "cpp",
	"cplusplus": "cpp",
	"java": "java",
	"jav": "java",
}

# Substring-matched against the lowercased C++ source. Every token here is one
# that genuinely reaches outside the sandbox: file redirection, process spawning,
# or networking. Judge0 is the real isolation boundary; this is a pre-filter.
#
# `printf(` and `scanf(` were deliberately REMOVED. They are not security
# relevant — they do console I/O against the same stdin/stdout the harness
# already feeds, exactly like cin/cout — and because the match is a naive
# substring test they collided with the safe, in-memory string-formatting
# family: `sprintf(`, `snprintf(`, and `fprintf(` all contain `printf(`, and
# `sscanf(` contains `scanf(`. A candidate formatting into a buffer with
# `std::sprintf` had a correct solution rejected for using a function that
# cannot escape anything.
_CPP_FORBIDDEN_TOKENS = {
	"#include <filesystem>",
	"#include <fstream>",
	"#include <thread>",
	"freopen(",
	"popen(",
	"system(",
	"fork(",
	"socket(",
}

_JAVA_FORBIDDEN_TOKENS = {
	"processbuilder",
	"runtime.getruntime",
	"java.io.file",
	"java.nio.file",
	"java.net.",
	"files.",
	"paths.",
	"system.exit(",
}

_FORBIDDEN_MODULES = {
	"ctypes",
	"multiprocessing",
	"os",
	"pathlib",
	"pickle",
	"shutil",
	"signal",
	"socket",
	"subprocess",
	"threading",
	"tempfile",
}

_FORBIDDEN_BUILTINS = {
	"compile",
	"eval",
	"exec",
	"open",
	"__import__",
}

# Attribute-call names blocked regardless of the object they are called on,
# because the static gate cannot do type inference to tell os.system(x) from
# a harmless method of the same name.
#
# IMPORTANT: this is a DEFENSE-IN-DEPTH pre-filter, not the sandbox. The real
# isolation boundary is Judge0, which runs every submission in an ephemeral
# container. So the only cost of a false NEGATIVE here is that a submission
# reaches a sandbox that is already designed to contain it; the cost of a false
# POSITIVE is that a candidate's correct solution is rejected outright.
#
# `remove` and `replace` were deliberately REMOVED from this set. They are the
# two names here that collide with everyday DSA operations — `list.remove(x)`,
# `set.remove(x)`, `str.replace(a, b)`, `bytes.replace(...)` — and blocking them
# rejected a large fraction of legitimate solutions ("remove duplicates",
# "clean a string"). Their only DANGEROUS forms are `os.remove` / `Path.replace`
# and similar, all of which require importing `os`, `pathlib`, or `shutil` — and
# every one of those modules is already in _FORBIDDEN_MODULES. You cannot obtain
# a dangerous object to call `.remove`/`.replace` on without a banned import, so
# these two entries added nothing but false positives.
#
# The names kept below have no common builtin-type collision, so they stay as
# cheap belt-and-suspenders against a dangerous call slipping through on an
# object obtained some way the module bans did not anticipate.
_FORBIDDEN_ATTRIBUTE_CALLS = {
	"popen",
	"rmdir",
	"rmtree",
	"system",
	"unlink",
	"write_bytes",
	"write_text",
}


class CodeExecutionError(RuntimeError):
	"""Raised when Judge0 submission or result parsing fails."""


class SafetyViolationError(CodeExecutionError):
	"""Raised when user code contains disallowed operations."""


@dataclass(frozen=True, slots=True)
class NormalizedTestCase:
	input_text: str
	output_text: str


@dataclass(frozen=True, slots=True)
class CaseExecutionResult:
	case_index: int
	passed: bool
	expected_output: str
	actual_output: str
	error: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
	status: str
	passed_count: int
	total_count: int
	case_results: tuple[CaseExecutionResult, ...]
	stdout: str
	stderr: str | None
	compile_output: str | None
	time_seconds: float | None
	memory_kb: int | None
	raw_status_id: int | None
	raw_status_description: str | None


def list_supported_dsa_languages() -> tuple[DSALanguageDefinition, ...]:
	return _SUPPORTED_DSA_LANGUAGES


def normalize_dsa_language(language: str | None) -> str:
	normalized = str(language or "python").strip().casefold()
	resolved = _DSA_LANGUAGE_ALIASES.get(normalized, normalized)
	if resolved not in {definition.key for definition in _SUPPORTED_DSA_LANGUAGES}:
		raise SafetyViolationError("Unsupported DSA language. Choose python, cpp, or java.")
	return resolved


def _extract_python_solve_body(python_starter_code: str | None) -> list[str]:
	if not python_starter_code:
		return []

	lines = str(python_starter_code).splitlines()
	body: list[str] = []
	inside_solve = False
	body_indent: int | None = None

	for line in lines:
		stripped = line.strip()
		if not inside_solve:
			if stripped.startswith("def solve"):
				inside_solve = True
			continue

		if not stripped:
			if body and body[-1] != "":
				body.append("")
			continue

		indent = len(line) - len(line.lstrip())
		if body_indent is None:
			body_indent = indent
		if indent < body_indent:
			break
		body.append(line[body_indent:].rstrip())

	while body and body[-1] == "":
		body.pop()
	return body


def _clean_comment_text(value: str) -> str:
	return " ".join(part for part in str(value or "").replace("#", " ").split())


def _extract_todo_comment(body_lines: Sequence[str]) -> str | None:
	for line in body_lines:
		stripped = line.strip()
		if stripped.startswith("#") and "todo" in stripped.casefold():
			return _clean_comment_text(stripped)
	return None


def _comment_block(prefix: str, title: str, value: str | None) -> list[str]:
	text = str(value or "").strip()
	if not text:
		return []
	block = [f"{prefix} {title}"]
	for line in text.splitlines():
		block.append(f"{prefix} {line.rstrip()}")
	return block


def _translate_cpp_line(line: str) -> list[str] | None:
	int_input_match = re.fullmatch(r"([A-Za-z_]\w*)\s*=\s*int\(input\(\)\.strip\(\)\)", line)
	if int_input_match:
		name = int_input_match.group(1)
		return [f"int {name} = 0;", f"cin >> {name};"]

	scalar_vars_match = re.fullmatch(r"([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)+)\s*=\s*map\(int,\s*input\(\)\.split\(\)\)", line)
	if scalar_vars_match:
		variable_names = [part.strip() for part in scalar_vars_match.group(1).split(",")]
		declaration = ", ".join(f"int {name}" if index == 0 else name for index, name in enumerate(variable_names))
		reads = " >> ".join(variable_names)
		return [f"{declaration};", f"cin >> {reads};"]

	string_input_match = re.fullmatch(r"([A-Za-z_]\w*)\s*=\s*input\(\)\.strip\(\)", line)
	if string_input_match:
		name = string_input_match.group(1)
		return [f"string {name};", f"cin >> {name};"]

	list_input_match = re.fullmatch(r"([A-Za-z_]\w*)\s*=\s*list\(map\(int,\s*input\(\)\.split\(\)\)\)", line)
	if list_input_match:
		name = list_input_match.group(1)
		return [f"vector<int> {name};", f"// TODO: read the integer list for {name} from stdin."]

	sorted_list_match = re.fullmatch(r"([A-Za-z_]\w*)\s*=\s*sorted\(map\(int,\s*input\(\)\.split\(\)\)\)", line)
	if sorted_list_match:
		name = sorted_list_match.group(1)
		return [f"vector<int> {name};", f"// TODO: read, then sort {name}."]

	matrix_match = re.fullmatch(r"([A-Za-z_]\w*)\s*=\s*\[list\(map\(int,\s*input\(\)\.split\(\)\)\) for _ in range\((\d+)\)\]", line)
	if matrix_match:
		name = matrix_match.group(1)
		rows = matrix_match.group(2)
		return [
			f"vector<vector<int>> {name}({rows}, vector<int>({rows}));",
			f"for (int row = 0; row < {rows}; ++row) {{",
			f"    for (int col = 0; col < {rows}; ++col) {{",
			f"        cin >> {name}[row][col];",
			"    }",
			"}",
		]

	list_annotation_match = re.fullmatch(r"([A-Za-z_]\w*)\s*:\s*list\[str\]\s*=\s*\[\]", line)
	if list_annotation_match:
		name = list_annotation_match.group(1)
		return [f"vector<string> {name};"]

	return None


def _translate_java_line(line: str) -> list[str] | None:
	int_input_match = re.fullmatch(r"([A-Za-z_]\w*)\s*=\s*int\(input\(\)\.strip\(\)\)", line)
	if int_input_match:
		name = int_input_match.group(1)
		return [f"int {name} = scanner.nextInt();"]

	scalar_vars_match = re.fullmatch(r"([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)+)\s*=\s*map\(int,\s*input\(\)\.split\(\)\)", line)
	if scalar_vars_match:
		variable_names = [part.strip() for part in scalar_vars_match.group(1).split(",")]
		return [f"int {name} = scanner.nextInt();" for name in variable_names]

	string_input_match = re.fullmatch(r"([A-Za-z_]\w*)\s*=\s*input\(\)\.strip\(\)", line)
	if string_input_match:
		name = string_input_match.group(1)
		return [f"String {name} = scanner.next();"]

	list_input_match = re.fullmatch(r"([A-Za-z_]\w*)\s*=\s*list\(map\(int,\s*input\(\)\.split\(\)\)\)", line)
	if list_input_match:
		name = list_input_match.group(1)
		return [f"List<Integer> {name} = new ArrayList<>();", f"// TODO: read the integer list for {name} from stdin."]

	sorted_list_match = re.fullmatch(r"([A-Za-z_]\w*)\s*=\s*sorted\(map\(int,\s*input\(\)\.split\(\)\)\)", line)
	if sorted_list_match:
		name = sorted_list_match.group(1)
		return [f"List<Integer> {name} = new ArrayList<>();", f"// TODO: read, then sort {name}."]

	matrix_match = re.fullmatch(r"([A-Za-z_]\w*)\s*=\s*\[list\(map\(int,\s*input\(\)\.split\(\)\)\) for _ in range\((\d+)\)\]", line)
	if matrix_match:
		name = matrix_match.group(1)
		rows = matrix_match.group(2)
		return [
			f"int[][] {name} = new int[{rows}][{rows}];",
			f"for (int row = 0; row < {rows}; row += 1) {{",
			f"    for (int col = 0; col < {rows}; col += 1) {{",
			f"        {name}[row][col] = scanner.nextInt();",
			"    }",
			"}",
		]

	list_annotation_match = re.fullmatch(r"([A-Za-z_]\w*)\s*:\s*list\[str\]\s*=\s*\[\]", line)
	if list_annotation_match:
		name = list_annotation_match.group(1)
		return [f"List<String> {name} = new ArrayList<>();"]

	return None


def _build_problem_aware_body(language_key: str, body_lines: Sequence[str]) -> list[str]:
	translated_lines: list[str] = []
	for line in body_lines:
		stripped = line.strip()
		if not stripped or stripped == "pass":
			continue
		if stripped.startswith("#"):
			continue

		translated = _translate_cpp_line(stripped) if language_key == "cpp" else _translate_java_line(stripped)
		if translated:
			translated_lines.extend(translated)
			continue

		translated_lines.append(f"// Python starter step: {_clean_comment_text(stripped)}")
	return translated_lines


def build_editor_starter_code(
	language: str,
	*,
	prompt_title: str | None = None,
	python_starter_code: str | None = None,
	example_input: str | None = None,
	example_output: str | None = None,
) -> str:
	language_key = normalize_dsa_language(language)
	if language_key == "python":
		if python_starter_code and str(python_starter_code).strip():
			return str(python_starter_code)
		title_line = f"# {prompt_title}\n" if prompt_title else ""
		return (
			f"{title_line}def solve() -> None:\n"
			"    # TODO: read from stdin and write to stdout\n"
			"    pass\n\n\n"
			"if __name__ == \"__main__\":\n"
			"    solve()\n"
		)

	body_lines = _extract_python_solve_body(python_starter_code)
	todo_comment = _extract_todo_comment(body_lines) or "TODO: finish the solve() implementation."
	problem_aware_lines = _build_problem_aware_body(language_key, body_lines)
	if language_key == "cpp":
		lines: list[str] = []
		if prompt_title:
			lines.append(f"// {prompt_title}")
		lines.extend(_comment_block("//", "Example input:", example_input))
		lines.extend(_comment_block("//", "Expected output:", example_output))
		lines.append("void solve() {")
		if problem_aware_lines:
			for line in problem_aware_lines:
				lines.append(f"    {line}" if line else "")
		else:
			lines.append("    // TODO: read from cin and write to cout.")
		lines.append(f"    // {todo_comment}")
		lines.append("    // Do not declare main(); the runner provides it.")
		lines.append("}")
		return "\n".join(lines) + "\n"

	lines = []
	if prompt_title:
		lines.append(f"// {prompt_title}")
	lines.extend(_comment_block("//", "Example input:", example_input))
	lines.extend(_comment_block("//", "Expected output:", example_output))
	lines.append("static void solve() throws Exception {")
	lines.append("    Scanner scanner = new Scanner(System.in);")
	if problem_aware_lines:
		for line in problem_aware_lines:
			lines.append(f"    {line}" if line else "")
	else:
		lines.append("    // TODO: read from System.in and write to System.out.")
	lines.append(f"    // {todo_comment}")
	lines.append("    // Do not declare Main or main(); the runner provides them.")
	lines.append("}")
	return "\n".join(lines) + "\n"


def _normalize_output_text(value: str) -> str:
	return value.replace("\r\n", "\n").strip()


def _coerce_test_cases(raw_cases: Sequence[object]) -> tuple[NormalizedTestCase, ...]:
	normalized: list[NormalizedTestCase] = []
	for raw_case in raw_cases:
		input_text: str | None = None
		output_text: str | None = None
		if isinstance(raw_case, Mapping):
			input_text = str(raw_case.get("input_text") or "")
			output_text = str(raw_case.get("output_text") or "")
		else:
			input_value = getattr(raw_case, "input_text", None)
			output_value = getattr(raw_case, "output_text", None)
			if input_value is not None:
				input_text = str(input_value)
			if output_value is not None:
				output_text = str(output_value)
		if not input_text or not output_text:
			raise CodeExecutionError("Each test case must define non-empty input_text and output_text values.")
		normalized.append(NormalizedTestCase(input_text=input_text, output_text=output_text))
	return tuple(normalized)


def safety_gate(code: str, language: str = "python") -> None:
	settings = get_settings().judge0
	if len(code) > settings.max_source_characters:
		raise SafetyViolationError(
			f"Code exceeds the configured source limit of {settings.max_source_characters} characters."
		)

	language_key = normalize_dsa_language(language)
	if language_key == "cpp":
		lowered = code.casefold()
		if "int main" in lowered or " main(" in lowered:
			raise SafetyViolationError("C++ submissions must define solve() and must not declare main().")
		for token in _CPP_FORBIDDEN_TOKENS:
			if token in lowered:
				raise SafetyViolationError(f"C++ submissions cannot use {token} in this DSA runner.")
		return
	if language_key == "java":
		lowered = code.casefold()
		if "class main" in lowered or " static void main" in lowered:
			raise SafetyViolationError("Java submissions must define solve() and must not declare Main or main().")
		for token in _JAVA_FORBIDDEN_TOKENS:
			if token in lowered:
				raise SafetyViolationError(f"Java submissions cannot use {token} in this DSA runner.")
		return

	try:
		tree = ast.parse(code)
	except SyntaxError as exc:
		raise SafetyViolationError(f"Code could not be parsed: {exc.msg}.") from exc

	for node in ast.walk(tree):
		if isinstance(node, ast.Import):
			for alias in node.names:
				module_name = alias.name.split(".", 1)[0]
				if module_name in _FORBIDDEN_MODULES:
					raise SafetyViolationError(f"Importing {module_name} is not allowed in DSA submissions.")
		elif isinstance(node, ast.ImportFrom):
			module_name = str(node.module or "").split(".", 1)[0]
			if module_name in _FORBIDDEN_MODULES:
				raise SafetyViolationError(f"Importing {module_name} is not allowed in DSA submissions.")
		elif isinstance(node, ast.Call):
			if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_BUILTINS:
				raise SafetyViolationError(f"Calling {node.func.id} is not allowed in DSA submissions.")
			if isinstance(node.func, ast.Attribute) and node.func.attr in _FORBIDDEN_ATTRIBUTE_CALLS:
				raise SafetyViolationError(
					f"Calling attribute {node.func.attr} is not allowed in DSA submissions."
				)


def _build_python_harness(code: str, case_payload: list[dict[str, str]]) -> str:
	encoded_user_code = json.dumps(code)
	encoded_cases = json.dumps(case_payload)
	return f"""import contextlib
import io
import json
import sys
import traceback

USER_CODE = {encoded_user_code}
TEST_CASES = {encoded_cases}


def _normalize_output(value: str) -> str:
	return value.replace("\\r\\n", "\\n").strip()


results = []

try:
	namespace = {{"__name__": "__judge__"}}
	exec(USER_CODE, namespace)
	solve = namespace.get("solve")
	if not callable(solve):
		raise RuntimeError("Submitted code must define a solve() function.")

	for case_index, case in enumerate(TEST_CASES, start=1):
		original_stdin = sys.stdin
		stdout_buffer = io.StringIO()
		error_text = None
		try:
			sys.stdin = io.StringIO(case["input_text"])
			with contextlib.redirect_stdout(stdout_buffer):
				solve()
		except Exception:
			error_text = traceback.format_exc(limit=4)
		finally:
			sys.stdin = original_stdin

		actual_output = stdout_buffer.getvalue()
		passed = error_text is None and _normalize_output(actual_output) == _normalize_output(case["output_text"])
		results.append(
			{{
				"case_index": case_index,
				"passed": passed,
				"expected_output": case["output_text"],
				"actual_output": actual_output,
				"error": error_text,
			}}
		)
except Exception:
	results.append(
		{{
			"case_index": 0,
			"passed": False,
			"expected_output": "",
			"actual_output": "",
			"error": traceback.format_exc(limit=6),
		}}
	)

print("{_HARNESS_START_MARKER}")
print(json.dumps(results))
print("{_HARNESS_END_MARKER}")
"""


def _build_cpp_harness(code: str, case_payload: list[dict[str, str]]) -> str:
	case_lines = ",\n        ".join(
		"{" + json.dumps(case["input_text"]) + ", " + json.dumps(case["output_text"]) + "}"
		for case in case_payload
	)
	return f"""#include <bits/stdc++.h>
using namespace std;

{code}

namespace judge {{
struct TestCase {{
	string input_text;
	string output_text;
}};

string normalize_output(const string& value) {{
	string normalized = value;
	while (!normalized.empty() && (normalized.back() == '\\n' || normalized.back() == '\\r' || normalized.back() == ' ' || normalized.back() == '\\t')) {{
		normalized.pop_back();
	}}
	return normalized;
}}

string escape_json(const string& value) {{
	ostringstream escaped;
	for (unsigned char ch : value) {{
		switch (ch) {{
			case '\\\\': escaped << "\\\\\\\\"; break;
			case '\"': escaped << "\\\\\\""; break;
			case '\\b': escaped << "\\\\b"; break;
			case '\\f': escaped << "\\\\f"; break;
			case '\\n': escaped << "\\\\n"; break;
			case '\\r': escaped << "\\\\r"; break;
			case '\\t': escaped << "\\\\t"; break;
			default:
				if (ch < 0x20) {{
					const char* hex = "0123456789abcdef";
					escaped << "\\\\u00" << hex[(ch >> 4) & 0xF] << hex[ch & 0xF];
				}} else {{
					escaped << static_cast<char>(ch);
				}}
		}}
	}}
	return escaped.str();
}}
}}  // namespace judge

int main() {{
	const vector<judge::TestCase> test_cases = {{
		{case_lines}
	}};
	ostringstream results;
	results << "[";
	for (size_t index = 0; index < test_cases.size(); ++index) {{
		if (index > 0) {{
			results << ",";
		}}
		const auto& test_case = test_cases[index];
		istringstream input_stream(test_case.input_text);
		ostringstream output_stream;
		string error_text;
		streambuf* original_cin = cin.rdbuf(input_stream.rdbuf());
		streambuf* original_cout = cout.rdbuf(output_stream.rdbuf());
		try {{
			solve();
		}} catch (const exception& exc) {{
			error_text = exc.what();
		}} catch (...) {{
			error_text = "Unknown exception";
		}}
		cin.rdbuf(original_cin);
		cout.rdbuf(original_cout);
		const string actual_output = output_stream.str();
		const bool passed = error_text.empty() && judge::normalize_output(actual_output) == judge::normalize_output(test_case.output_text);
		results << "{{"
				<< "\\\"case_index\\\":" << (index + 1) << ","
				<< "\\\"passed\\\":" << (passed ? "true" : "false") << ","
				<< "\\\"expected_output\\\":\\\"" << judge::escape_json(test_case.output_text) << "\\\"," 
				<< "\\\"actual_output\\\":\\\"" << judge::escape_json(actual_output) << "\\\"," 
				<< "\\\"error\\\":";
		if (error_text.empty()) {{
			results << "null";
		}} else {{
			results << "\\\"" << judge::escape_json(error_text) << "\\\"";
		}}
		results << "}}";
	}}
	results << "]";
	cout << "{_HARNESS_START_MARKER}\\n";
	cout << results.str() << "\\n";
	cout << "{_HARNESS_END_MARKER}\\n";
	return 0;
}}
"""


def _build_java_harness(code: str, case_payload: list[dict[str, str]]) -> str:
	case_lines = ",\n            ".join(
		f"new TestCase({json.dumps(case['input_text'])}, {json.dumps(case['output_text'])})"
		for case in case_payload
	)
	return f"""import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

public class Main {{
{code}

	private static final String HARNESS_START_MARKER = "{_HARNESS_START_MARKER}";
	private static final String HARNESS_END_MARKER = "{_HARNESS_END_MARKER}";

	private static final class TestCase {{
		final String inputText;
		final String outputText;

		TestCase(String inputText, String outputText) {{
			this.inputText = inputText;
			this.outputText = outputText;
		}}
	}}

	private static String normalizeOutput(String value) {{
		return value.replace("\\r\\n", "\\n").trim();
	}}

	private static String escapeJson(String value) {{
		StringBuilder escaped = new StringBuilder();
		for (int index = 0; index < value.length(); index += 1) {{
			char ch = value.charAt(index);
			switch (ch) {{
				case '\\\\': escaped.append("\\\\\\\\"); break;
				case '\"': escaped.append("\\\\\\""); break;
				case '\\b': escaped.append("\\\\b"); break;
				case '\\f': escaped.append("\\\\f"); break;
				case '\\n': escaped.append("\\\\n"); break;
				case '\\r': escaped.append("\\\\r"); break;
				case '\\t': escaped.append("\\\\t"); break;
				default:
					if (ch < 0x20) {{
						escaped.append(String.format("\\\\u%04x", (int) ch));
					}} else {{
						escaped.append(ch);
					}}
			}}
		}}
		return escaped.toString();
	}}

	public static void main(String[] args) throws Exception {{
		List<TestCase> testCases = Arrays.asList(
			{case_lines}
		);
		StringBuilder results = new StringBuilder("[");
		InputStream originalIn = System.in;
		PrintStream originalOut = System.out;

		for (int index = 0; index < testCases.size(); index += 1) {{
			if (index > 0) {{
				results.append(',');
			}}
			TestCase testCase = testCases.get(index);
			ByteArrayInputStream inputStream = new ByteArrayInputStream(testCase.inputText.getBytes(StandardCharsets.UTF_8));
			ByteArrayOutputStream outputStream = new ByteArrayOutputStream();
			PrintStream captureOut = new PrintStream(outputStream, true, StandardCharsets.UTF_8.name());
			String errorText = null;

			try {{
				System.setIn(inputStream);
				System.setOut(captureOut);
				solve();
			}} catch (Throwable throwable) {{
				StringWriter writer = new StringWriter();
				throwable.printStackTrace(new PrintWriter(writer));
				errorText = writer.toString();
			}} finally {{
				captureOut.flush();
				System.setIn(originalIn);
				System.setOut(originalOut);
			}}

			String actualOutput = outputStream.toString(StandardCharsets.UTF_8.name());
			boolean passed = errorText == null && normalizeOutput(actualOutput).equals(normalizeOutput(testCase.outputText));
			results.append("{{")
				.append("\\\"case_index\\\":").append(index + 1).append(',')
				.append("\\\"passed\\\":").append(passed ? "true" : "false").append(',')
				.append("\\\"expected_output\\\":\\\"").append(escapeJson(testCase.outputText)).append("\\\",")
				.append("\\\"actual_output\\\":\\\"").append(escapeJson(actualOutput)).append("\\\",")
				.append("\\\"error\\\":");
			if (errorText == null) {{
				results.append("null");
			}} else {{
				results.append("\\\"").append(escapeJson(errorText)).append("\\\"");
			}}
			results.append("}}");
		}}
		results.append(']');

		System.out.println(HARNESS_START_MARKER);
		System.out.println(results.toString());
		System.out.println(HARNESS_END_MARKER);
	}}
}}
"""


def build_harness(code: str, test_cases: Sequence[object], language: str = "python") -> str:
	normalized_cases = _coerce_test_cases(test_cases)
	case_payload = [asdict(test_case) for test_case in normalized_cases]
	language_key = normalize_dsa_language(language)
	if language_key == "python":
		return _build_python_harness(code, case_payload)
	if language_key == "cpp":
		return _build_cpp_harness(code, case_payload)
	return _build_java_harness(code, case_payload)


def _open_json_request(
	url: str,
	*,
	method: str,
	payload: Mapping[str, Any] | None = None,
) -> Any:
	settings = get_settings().judge0
	request_body = None
	headers = {"Accept": "application/json"}
	if payload is not None:
		request_body = json.dumps(payload).encode("utf-8")
		headers["Content-Type"] = "application/json"
	# Judge0 runs with authentication enabled (see docker-compose.judge0.yml).
	# The header name must match AUTHN_HEADER there. Left unset, requests go out
	# unauthenticated, which is only viable against a Judge0 that has authn
	# disabled -- so this stays optional rather than required, to avoid breaking
	# an existing local instance that was set up without a token.
	if settings.auth_token:
		headers[settings.auth_header] = settings.auth_token

	request = urllib_request.Request(url, data=request_body, headers=headers, method=method)
	started_at = time.monotonic()
	try:
		with urllib_request.urlopen(request, timeout=settings.request_timeout_seconds) as response:
			charset = response.headers.get_content_charset() or "utf-8"
			body = response.read().decode(charset)
	except urllib_error.HTTPError as exc:
		judge0_requests_total.labels(outcome="error").inc()
		judge0_request_duration_seconds.observe(time.monotonic() - started_at)
		body = exc.read().decode("utf-8", errors="replace")
		raise CodeExecutionError(
			f"Judge0 request failed with HTTP {exc.code}: {body.strip() or exc.reason}."
		) from exc
	except urllib_error.URLError as exc:
		judge0_requests_total.labels(outcome="error").inc()
		judge0_request_duration_seconds.observe(time.monotonic() - started_at)
		raise CodeExecutionError(f"Judge0 is unavailable: {exc.reason}.") from exc

	judge0_requests_total.labels(outcome="success").inc()
	judge0_request_duration_seconds.observe(time.monotonic() - started_at)

	try:
		parsed = json.loads(body)
	except json.JSONDecodeError as exc:
		raise CodeExecutionError("Judge0 returned a non-JSON response.") from exc
	return parsed


def judge0_health_check() -> dict[str, Any]:
	settings = get_settings().judge0
	url = f"{settings.api_base_url}/languages"
	try:
		payload = _open_json_request(url, method="GET")
	except CodeExecutionError as exc:
		return {
			"available": False,
			"base_url": settings.api_base_url,
			"detail": str(exc),
		}
	return {
		"available": True,
		"base_url": settings.api_base_url,
		"detail": "Judge0 responded to the languages probe.",
		"language_count": len(payload) if isinstance(payload, list) else None,
	}


def _resolve_judge0_language_id(language: str) -> int:
	settings = get_settings().judge0
	language_key = normalize_dsa_language(language)
	if language_key == "cpp":
		return settings.cpp_language_id
	if language_key == "java":
		return settings.java_language_id
	return settings.python_language_id


def _get_judge0_status_id(response: Mapping[str, Any]) -> int | None:
	raw_status = response.get("status")
	if isinstance(raw_status, Mapping):
		status_id = raw_status.get("id")
		if isinstance(status_id, int):
			return status_id
	return None


_BASE64_RESULT_FIELDS: tuple[str, ...] = ("stdout", "stderr", "compile_output", "message")


def _decode_base64_result_fields(response: Mapping[str, Any]) -> dict[str, Any]:
	"""Decode the base64-encoded text fields of a Judge0 submission result.

	Judge0 returns these fields base64-encoded when the request asks for
	base64_encoded=true. Everything downstream (parse_judge0_result, the harness
	marker extraction) expects plain strings, so normalise them back here.
	"""
	decoded = dict(response)
	for field in _BASE64_RESULT_FIELDS:
		raw_value = decoded.get(field)
		if not isinstance(raw_value, str) or not raw_value:
			continue
		try:
			decoded[field] = base64.b64decode(raw_value, validate=False).decode("utf-8", errors="replace")
		except (ValueError, binascii.Error):
			# Leave the value untouched if Judge0 ever hands back plain text.
			continue
	return decoded


def _poll_submission(token: str) -> dict[str, Any]:
	"""Poll GET /submissions/{token} until Judge0 reports a terminal status.

	This is the reliable path for Judge0 CE with separate server and worker
	Docker containers. The wait=true synchronous POST path can return the
	execution result with stdout omitted in some CE v1.13.x builds, causing
	the harness markers to go missing. Polling by token always returns the
	full result once the worker writes it to the database.
	"""
	settings = get_settings().judge0
	# Poll with base64_encoded=true. With base64_encoded=false, Judge0 CE rejects the
	# whole GET with HTTP 400 ("some attributes for this submission cannot be converted
	# to UTF-8") whenever stdout/stderr/compile_output contain bytes it will not serialise
	# as plain text -- which happens routinely for compile errors, because GCC quotes
	# identifiers with U+2018/U+2019. That 400 was being swallowed by the retry loop below,
	# so a compile error that Judge0 reported in ~1s surfaced as a 30s "did not finish
	# executing" timeout. Requesting base64 always succeeds; we decode back to text here so
	# callers still see plain strings.
	poll_url = f"{settings.api_base_url}/submissions/{urllib_parse.quote(token, safe='')}?base64_encoded=true"
	last_result: dict[str, Any] = {}
	for _ in range(_POLL_MAX_ATTEMPTS):
		time.sleep(_POLL_INTERVAL_SECONDS)
		try:
			response = _open_json_request(poll_url, method="GET")
		except CodeExecutionError:
			continue
		if not isinstance(response, Mapping):
			continue
		last_result = _decode_base64_result_fields(response)
		if _get_judge0_status_id(last_result) in _JUDGE0_TERMINAL_STATUS_IDS:
			return last_result
	if not last_result:
		raise CodeExecutionError(f"Judge0 polling timed out after {_POLL_MAX_ATTEMPTS} attempts for token {token!r}.")
	last_status = last_result.get("status")
	last_status_description = ""
	if isinstance(last_status, Mapping):
		last_status_description = str(last_status.get("description") or "").strip()
	detail = f" Last Judge0 status: {last_status_description}." if last_status_description else ""
	raise CodeExecutionError(
		"Judge0 did not finish executing the submission before the backend timeout."
		f"{detail}"
	)


def _submit_harness(harness_code: str, *, language: str) -> dict[str, Any]:
	settings = get_settings().judge0
	# POST without wait=true to get a submission token immediately.
	# wait=true is unreliable in Judge0 CE v1.13.x with separate server+worker
	# containers: the synchronous response occasionally omits stdout even when
	# execution completed. Polling by token via GET always returns the full result.
	query = urllib_parse.urlencode({"base64_encoded": "false"})
	url = f"{settings.api_base_url}/submissions?{query}"
	payload = {
		"language_id": _resolve_judge0_language_id(language),
		"source_code": harness_code,
		"stdin": "",
		"cpu_time_limit": settings.cpu_time_limit_seconds,
		"wall_time_limit": settings.wall_time_limit_seconds,
		"memory_limit": settings.memory_limit_kb,
		"enable_per_process_and_thread_time_limit": True,
		"enable_per_process_and_thread_memory_limit": True,
	}
	response = _open_json_request(url, method="POST", payload=payload)
	if not isinstance(response, Mapping):
		raise CodeExecutionError("Judge0 returned an unexpected submission payload.")
	token = str(response.get("token") or "").strip()
	if not token:
		raise CodeExecutionError("Judge0 submission did not return a token. Cannot poll for results.")
	return _poll_submission(token)


def _extract_case_results(stdout: str) -> tuple[list[CaseExecutionResult], str]:
	if _HARNESS_START_MARKER not in stdout or _HARNESS_END_MARKER not in stdout:
		return [], stdout
	prefix, remainder = stdout.split(_HARNESS_START_MARKER, 1)
	results_blob, suffix = remainder.split(_HARNESS_END_MARKER, 1)
	try:
		parsed_results = json.loads(results_blob.strip())
	except json.JSONDecodeError as exc:
		raise CodeExecutionError("Judge0 output did not contain valid harness JSON results.") from exc
	if not isinstance(parsed_results, list):
		raise CodeExecutionError("Judge0 output did not contain a list of case results.")
	case_results: list[CaseExecutionResult] = []
	for raw_result in parsed_results:
		if not isinstance(raw_result, dict):
			raise CodeExecutionError("Harness returned a malformed case result entry.")
		case_results.append(
			CaseExecutionResult(
				case_index=int(raw_result.get("case_index") or 0),
				passed=bool(raw_result.get("passed")),
				expected_output=str(raw_result.get("expected_output") or ""),
				actual_output=str(raw_result.get("actual_output") or ""),
				error=str(raw_result.get("error")) if raw_result.get("error") else None,
			)
		)
	residual_stdout = f"{prefix}{suffix}".strip()
	return case_results, residual_stdout


def parse_judge0_result(response: Mapping[str, Any]) -> ExecutionResult:
	stdout = str(response.get("stdout") or "")
	stderr = str(response.get("stderr") or "") or None
	compile_output = str(response.get("compile_output") or "") or None
	raw_status = response.get("status")
	raw_status_id: int | None = None
	raw_status_description: str | None = None
	if isinstance(raw_status, Mapping):
		status_id = raw_status.get("id")
		if isinstance(status_id, int):
			raw_status_id = status_id
		raw_status_description = str(raw_status.get("description") or "") or None

	case_results, residual_stdout = _extract_case_results(stdout)
	passed_count = sum(1 for case_result in case_results if case_result.passed)
	return ExecutionResult(
		status=raw_status_description or "unknown",
		passed_count=passed_count,
		total_count=len(case_results),
		case_results=tuple(case_results),
		stdout=residual_stdout,
		stderr=stderr,
		compile_output=compile_output,
		time_seconds=float(response["time"]) if response.get("time") not in {None, ""} else None,
		memory_kb=int(response["memory"]) if response.get("memory") not in {None, ""} else None,
		raw_status_id=raw_status_id,
		raw_status_description=raw_status_description,
	)


def serialize_execution_result(result: ExecutionResult) -> dict[str, Any]:
	return {
		"status": result.status,
		"passed_count": result.passed_count,
		"total_count": result.total_count,
		"case_results": [asdict(case_result) for case_result in result.case_results],
		"stdout": result.stdout,
		"stderr": result.stderr,
		"compile_output": result.compile_output,
		"time_seconds": result.time_seconds,
		"memory_kb": result.memory_kb,
		"raw_status_id": result.raw_status_id,
		"raw_status_description": result.raw_status_description,
	}


def _execute_cases(code: str, test_cases: Sequence[object], *, language: str = "python") -> ExecutionResult:
	language_key = normalize_dsa_language(language)
	safety_gate(code, language_key)
	harness_code = build_harness(code, test_cases, language_key)
	judge0_response = _submit_harness(harness_code, language=language_key)
	return parse_judge0_result(judge0_response)


def run_sample(code: str, visible_examples: Sequence[object], *, language: str = "python") -> dict[str, Any]:
	return serialize_execution_result(_execute_cases(code, visible_examples, language=language))


def run_submission(code: str, hidden_test_cases: Sequence[object], *, language: str = "python") -> dict[str, Any]:
	return serialize_execution_result(_execute_cases(code, hidden_test_cases, language=language))
