import { test } from "node:test";
import assert from "node:assert/strict";

import { formatOptionIds, languageLabel, splitQuestion } from "./assessmentQuestion.js";

test("generated questions keep their code separate", () => {
	const parts = splitQuestion({ prompt: "What is printed?", code: "print(1)", code_language: "python" });
	assert.deepEqual(parts, { text: "What is printed?", code: "print(1)", language: "python" });
});

test("older bank questions have their fenced code pulled out", () => {
	const parts = splitQuestion({ prompt: "What does this print? ```java\nint x = 5;\nSystem.out.println(x);\n```" });
	assert.equal(parts.text, "What does this print?");
	assert.equal(parts.code, "int x = 5;\nSystem.out.println(x);");
	assert.equal(languageLabel(parts.language), "Java");
});

test("select-all questions don't repeat the hint in the text", () => {
	const parts = splitQuestion({ prompt: "Which are true? (Select all that apply)", multi_select: true });
	assert.equal(parts.text, "Which are true?");
});

test("labels and answer lists", () => {
	assert.equal(languageLabel("pseudocode"), "Pseudocode");
	assert.equal(languageLabel("unknown"), "Code");
	assert.equal(formatOptionIds(["a", "c"]), "A, C");
});
