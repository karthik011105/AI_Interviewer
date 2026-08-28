from __future__ import annotations

from unittest import TestCase

from backend.dsa.code_executor import judge0_health_check, run_sample, run_submission


SIMPLE_CASES = (
	{"input_text": "2 3\n", "output_text": "5"},
	{"input_text": "10 7\n", "output_text": "17"},
)


PYTHON_CODE = """
def solve() -> None:
	left, right = map(int, input().split())
	print(left + right)
""".strip()


CPP_CODE = """
void solve() {
	long long left = 0;
	long long right = 0;
	if (!(cin >> left >> right)) {
		return;
	}
	cout << (left + right) << "\\n";
}
""".strip()


JAVA_CODE = """
static void solve() throws Exception {
	Scanner scanner = new Scanner(System.in);
	long left = scanner.nextLong();
	long right = scanner.nextLong();
	System.out.println(left + right);
}
""".strip()


class DsaMultilanguageExecutionTests(TestCase):
	def setUp(self) -> None:
		health = judge0_health_check()
		if not health.get("available"):
			self.skipTest(str(health.get("detail") or "Judge0 is unavailable"))

	def test_run_sample_supports_python_cpp_and_java(self) -> None:
		for language, code in (("python", PYTHON_CODE), ("cpp", CPP_CODE), ("java", JAVA_CODE)):
			with self.subTest(language=language):
				result = run_sample(code, SIMPLE_CASES, language=language)
				self.assertEqual(result["passed_count"], result["total_count"], msg=str(result))

	def test_run_submission_supports_python_cpp_and_java(self) -> None:
		for language, code in (("python", PYTHON_CODE), ("cpp", CPP_CODE), ("java", JAVA_CODE)):
			with self.subTest(language=language):
				result = run_submission(code, SIMPLE_CASES, language=language)
				self.assertEqual(result["passed_count"], result["total_count"], msg=str(result))