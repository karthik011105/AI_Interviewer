import Editor from "@monaco-editor/react";

const DSA_MONACO_THEME = "interview-simulator-dsa";

let themeConfigured = false;

function configureMonacoTheme(monaco) {
  if (themeConfigured) {
    return;
  }

  monaco.editor.defineTheme(DSA_MONACO_THEME, {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "comment", foreground: "7c93a7", fontStyle: "italic" },
      { token: "keyword", foreground: "ffb24c" },
      { token: "string", foreground: "9ce6b3" },
      { token: "number", foreground: "72d8c4" },
      { token: "type.identifier", foreground: "7fc8ff" },
      { token: "identifier", foreground: "d7f8ff" },
    ],
    colors: {
      "editor.background": "#09111d",
      "editor.foreground": "#d7f8ff",
      "editorLineNumber.foreground": "#4d6375",
      "editorLineNumber.activeForeground": "#8bb6d9",
      "editorCursor.foreground": "#ffb24c",
      "editor.selectionBackground": "#2d4c6f99",
      "editor.inactiveSelectionBackground": "#20374f66",
      "editorIndentGuide.background1": "#1d2b3a",
      "editorIndentGuide.activeBackground1": "#36526b",
      "editor.lineHighlightBackground": "#0f1a29",
      "editorGutter.background": "#09111d",
    },
  });

  themeConfigured = true;
}

function resolveMonacoLanguage(languageKey) {
  if (languageKey === "cpp") {
    return "cpp";
  }
  if (languageKey === "java") {
    return "java";
  }
  return "python";
}

export default function CodeEditor({
  value,
  onChange,
  readOnly = false,
  fileName = "solve.py",
  languageKey = "python",
  languageLabel = "Python",
  statusLabel = "Draft",
  placeholder = "Write your solve() implementation here.",
  headerActions = null,
  height = "480px",
  wrapLongLines = true,
}) {
  return (
    <section className="dsa-editor" aria-label="DSA code editor">
      <div className="dsa-editor__bar">
        <div>
          <span>{languageLabel}</span>
          <strong>{fileName}</strong>
        </div>
        <div className="dsa-editor__bar-controls">
          {headerActions}
          <span className="status-pill status-pill--neutral">{statusLabel}</span>
        </div>
      </div>
      <div className="dsa-editor__surface" style={{ minHeight: height }}>
        {!value?.trim() && placeholder ? <p className="dsa-editor__hint">{placeholder}</p> : null}
        <Editor
          className="dsa-editor__monaco"
          beforeMount={configureMonacoTheme}
          defaultLanguage={resolveMonacoLanguage(languageKey)}
          height={height}
          language={resolveMonacoLanguage(languageKey)}
          loading={<div className="dsa-editor__loading" style={{ minHeight: height }}>Loading editor...</div>}
          onChange={(nextValue) => onChange?.(nextValue ?? "")}
          options={{
            automaticLayout: true,
            contextmenu: true,
            cursorBlinking: "smooth",
            fontFamily: '"IBM Plex Mono", "Fira Code", monospace',
            fontLigatures: true,
            fontSize: 14,
            lineHeight: 22,
            minimap: { enabled: false },
            padding: { top: 16, bottom: 16 },
            quickSuggestions: !readOnly,
            readOnly,
            renderLineHighlight: "line",
            roundedSelection: true,
            scrollBeyondLastLine: false,
            smoothScrolling: true,
            tabSize: 4,
            wordWrap: wrapLongLines ? "on" : "off",
          }}
          path={`dsa/${fileName}`}
          theme={DSA_MONACO_THEME}
          value={value}
        />
      </div>
    </section>
  );
}
