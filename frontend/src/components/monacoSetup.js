/**
 * Bundle Monaco instead of fetching it from a CDN.
 *
 * Without loader.config(), @monaco-editor/react downloads the editor from
 * cdn.jsdelivr.net at runtime. That broke the DSA editor wherever the CDN was
 * blocked or unreachable, and forced the CSP to trust a third-party origin for
 * script execution across the whole app.
 *
 * Only the core editor and the three languages the DSA round supports are
 * imported, rather than "monaco-editor" itself, which pulls in every language
 * and the TypeScript/JSON/CSS/HTML language services. Python, C++ and Java are
 * tokenizer-only languages, so the base editor worker is the only one needed.
 * Imported only by CodeEditor, which only the lazily loaded DSA page uses, so
 * none of this is in the main bundle.
 */
import { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor/esm/vs/editor/editor.api.js";
import "monaco-editor/esm/vs/basic-languages/cpp/cpp.contribution.js";
import "monaco-editor/esm/vs/basic-languages/java/java.contribution.js";
import "monaco-editor/esm/vs/basic-languages/python/python.contribution.js";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker.js?worker";

self.MonacoEnvironment = {
	getWorker() {
		return new EditorWorker();
	},
};

loader.config({ monaco });
