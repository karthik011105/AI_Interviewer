"""Direct tests for the DSA static safety gate.

The gate (``code_executor.safety_gate``) is a defense-in-depth PRE-FILTER, not
the sandbox. The real isolation boundary is Judge0, which runs every submission
in an ephemeral container. So the gate's job is to reject the obviously
dangerous before it reaches the sandbox — and, just as importantly, to NOT
reject legitimate solutions, because a false positive fails a real candidate on
a correct answer.

Two false-positive classes were found and fixed, and each of these tests exists
to keep a fix from being quietly reverted:

* Python: ``list.remove`` / ``set.remove`` / ``str.replace`` were blocked
  because the gate matched attribute-call *names* on any object. Their only
  dangerous forms (``os.remove``, ``Path.replace``) require a banned import.
* C++: ``std::sprintf`` / ``std::sscanf`` were blocked because ``"sprintf("``
  contains ``"printf("`` under naive substring matching, and ``printf`` /
  ``scanf`` were on the token list despite doing nothing a sandbox must contain.

The security direction is tested too: every dangerous form must still be
rejected, so the false-positive fixes cannot be mistaken for a licence to weaken
the gate.
"""

from __future__ import annotations

from unittest import TestCase

from backend.dsa.code_executor import SafetyViolationError, safety_gate


class PythonGateFalsePositiveTests(TestCase):
    def test_common_container_and_string_methods_are_allowed(self) -> None:
        legitimate = {
            "list.remove in a loop": (
                "def solve():\n"
                "    nums = [1, 2, 2, 3]\n"
                "    while 2 in nums:\n"
                "        nums.remove(2)\n"
                "    print(nums)\n"
            ),
            "set.remove": (
                "def solve():\n"
                "    s = {1, 2, 3}\n"
                "    s.remove(2)\n"
                "    print(sorted(s))\n"
            ),
            "str.replace": "def solve():\n    print('a b c'.replace(' ', ''))\n",
            "replace inside a comprehension": (
                "def solve():\n"
                "    words = ['ab', 'ba']\n"
                "    print([w.replace('a', 'x') for w in words])\n"
            ),
        }
        for label, code in legitimate.items():
            with self.subTest(case=label):
                # Must not raise.
                safety_gate(code, "python")


class PythonGateStillBlocksDangerousCode(TestCase):
    """The false-positive fix removed ``remove``/``replace`` from the
    attribute-call denylist on the argument that their dangerous forms require
    a banned import. That argument is only valid if the imports really are
    still banned, so these pin it."""

    def _assert_blocked(self, code: str) -> None:
        with self.assertRaises(SafetyViolationError):
            safety_gate(code, "python")

    def test_os_remove_is_blocked_at_the_import(self) -> None:
        self._assert_blocked("import os\ndef solve():\n    os.remove('/tmp/x')\n")

    def test_from_os_import_remove_is_blocked(self) -> None:
        self._assert_blocked(
            "from os import remove\ndef solve():\n    remove('/tmp/x')\n"
        )

    def test_pathlib_unlink_is_blocked_at_the_import(self) -> None:
        self._assert_blocked(
            "import pathlib\ndef solve():\n    pathlib.Path('/x').unlink()\n"
        )

    def test_shutil_rmtree_is_blocked_at_the_import(self) -> None:
        self._assert_blocked("import shutil\ndef solve():\n    shutil.rmtree('/')\n")

    def test_subprocess_is_blocked(self) -> None:
        self._assert_blocked(
            "import subprocess\ndef solve():\n    subprocess.run(['ls'])\n"
        )

    def test_dangerous_builtins_are_blocked(self) -> None:
        for builtin in ("eval", "exec", "compile", "open", "__import__"):
            with self.subTest(builtin=builtin):
                self._assert_blocked(
                    f"def solve():\n    {builtin}('x')\n"
                )

    def test_the_still_listed_attribute_calls_are_blocked(self) -> None:
        """These names have no common builtin collision, so they stay on the
        denylist as cheap belt-and-suspenders."""

        for attr, code in (
            ("system", "def solve():\n    x.system('ls')\n"),
            ("write_text", "def solve():\n    p.write_text('x')\n"),
            ("rmtree", "def solve():\n    x.rmtree('/')\n"),
            ("unlink", "def solve():\n    p.unlink()\n"),
        ):
            with self.subTest(attr=attr):
                self._assert_blocked(code)


class CppGateFalsePositiveTests(TestCase):
    def test_in_memory_string_formatting_is_allowed(self) -> None:
        allowed = {
            "sprintf": 'void solve() { char b[16]; std::sprintf(b, "%d", 5); }',
            "snprintf": 'void solve() { char b[16]; std::snprintf(b, 16, "%d", 5); }',
            "sscanf": 'void solve() { int x; std::sscanf("5", "%d", &x); }',
            "plain cin/cout": "void solve() { long a; std::cin >> a; std::cout << a; }",
        }
        for label, code in allowed.items():
            with self.subTest(case=label):
                safety_gate(code, "cpp")

    def test_genuinely_dangerous_cpp_is_still_blocked(self) -> None:
        for label, code in {
            "system": 'void solve() { system("ls"); }',
            "popen": 'void solve() { popen("ls", "r"); }',
            "fork": "void solve() { fork(); }",
            "socket": "void solve() { socket(1, 2, 3); }",
            "freopen": 'void solve() { freopen("f", "r", stdin); }',
            "fstream include": "#include <fstream>\nvoid solve() {}",
            "thread include": "#include <thread>\nvoid solve() {}",
        }.items():
            with self.subTest(case=label):
                with self.assertRaises(SafetyViolationError):
                    safety_gate(code, "cpp")

    def test_a_main_function_is_refused(self) -> None:
        """The harness supplies main(); a submission that declares its own would
        collide with it."""

        with self.assertRaises(SafetyViolationError):
            safety_gate("int main() { return 0; }", "cpp")


class JavaGateTests(TestCase):
    def test_legitimate_java_is_allowed(self) -> None:
        safety_gate(
            "static void solve() {\n"
            "    java.util.Scanner sc = new java.util.Scanner(System.in);\n"
            "    System.out.println(sc.nextLong());\n"
            "}",
            "java",
        )

    def test_dangerous_java_is_blocked(self) -> None:
        for label, code in {
            "Runtime.getRuntime": "static void solve() { Runtime.getRuntime().exec(\"ls\"); }",
            "ProcessBuilder": "static void solve() { new ProcessBuilder(\"ls\"); }",
            "java.io.File": "static void solve() { new java.io.File(\"/x\"); }",
            "java.net": "static void solve() { java.net.Socket s; }",
            "System.exit": "static void solve() { System.exit(0); }",
        }.items():
            with self.subTest(case=label):
                with self.assertRaises(SafetyViolationError):
                    safety_gate(code, "java")

    def test_a_main_class_is_refused(self) -> None:
        with self.assertRaises(SafetyViolationError):
            safety_gate("class Main { static void solve() {} }", "java")


class GateSourceSizeTests(TestCase):
    def test_an_oversized_submission_is_rejected(self) -> None:
        # The default source cap is 50k characters; well past it.
        with self.assertRaises(SafetyViolationError):
            safety_gate("def solve():\n    x = '" + "a" * 60000 + "'\n", "python")

    def test_unparseable_python_is_reported_as_a_violation_not_a_crash(self) -> None:
        with self.assertRaises(SafetyViolationError):
            safety_gate("def solve(:\n    this is not python\n", "python")


if __name__ == "__main__":
    import unittest

    unittest.main()
