// Display helpers for assessment questions. Generated questions carry their
// code separately; older bank questions embed it in ``` fences in the prompt.

const FENCE = /```([a-zA-Z0-9+#]*)\n?([\s\S]*?)```/;

const LANGUAGE_LABELS = {
	java: "Java",
	python: "Python",
	javascript: "JavaScript",
	js: "JavaScript",
	typescript: "TypeScript",
	ts: "TypeScript",
	cpp: "C++",
	c: "C",
	sql: "SQL",
	bash: "Shell",
	pseudocode: "Pseudocode",
	jsx: "React",
	tsx: "React",
};

const SELECT_ALL = /\s*\(select all that apply\)\s*$/i;

export function splitQuestion(question) {
	// The hint under the question already says it.
	const prompt = String(question?.prompt || "").replace(question?.multi_select ? SELECT_ALL : /$^/, "");
	if (question?.code) {
		return { text: prompt, code: question.code, language: question.code_language || "" };
	}
	const match = prompt.match(FENCE);
	if (!match) return { text: prompt, code: "", language: "" };
	return {
		text: prompt.replace(FENCE, " ").replace(/\s+/g, " ").trim(),
		code: match[2].replace(/^\n+|\n+$/g, ""),
		language: match[1] || "",
	};
}

export function languageLabel(language) {
	return LANGUAGE_LABELS[String(language || "").toLowerCase()] || "Code";
}

/** "A, C" for a list of option ids. */
export function formatOptionIds(ids) {
	return (ids || []).map((id) => String(id).toUpperCase()).join(", ");
}
