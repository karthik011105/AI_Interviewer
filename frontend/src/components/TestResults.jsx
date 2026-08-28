function formatMetricLabel(value, suffix = "") {
  if (value == null || value === "") {
    return "Not reported";
  }
  return `${value}${suffix}`;
}

export default function TestResults({ executionResults, title = "Execution results", emptyLabel = "Run code to inspect sample or hidden-test results." }) {
  if (!executionResults) {
    return (
      <section className="glass-panel dsa-results" aria-label={title}>
        <div className="panel-head panel-head--tight">
          <div>
            <p className="section-kicker">Results</p>
            <h2>{title}</h2>
          </div>
        </div>
        <p className="empty-state">{emptyLabel}</p>
      </section>
    );
  }

  const caseResults = Array.isArray(executionResults.case_results) ? executionResults.case_results : [];
  const passedCount = Number(executionResults.passed_count || 0);
  const totalCount = Number(executionResults.total_count || caseResults.length || 0);

  return (
    <section className="glass-panel dsa-results" aria-label={title}>
      <div className="panel-head panel-head--tight">
        <div>
          <p className="section-kicker">Results</p>
          <h2>{title}</h2>
        </div>
        <span className={`status-pill status-pill--${passedCount === totalCount && totalCount > 0 ? "online" : "warn"}`}>
          {passedCount}/{totalCount} passed
        </span>
      </div>
      <div className="dsa-results__summary">
        <article className="dsa-results__metric">
          <span>Status</span>
          <strong>{executionResults.status || "Unknown"}</strong>
        </article>
        <article className="dsa-results__metric">
          <span>Time</span>
          <strong>{formatMetricLabel(executionResults.time_seconds, "s")}</strong>
        </article>
        <article className="dsa-results__metric">
          <span>Memory</span>
          <strong>{formatMetricLabel(executionResults.memory_kb, " KB")}</strong>
        </article>
      </div>

      {executionResults.compile_output ? (
        <p className="error-banner">Compile output: {executionResults.compile_output}</p>
      ) : null}
      {executionResults.stderr ? (
        <p className="error-banner">Runtime output: {executionResults.stderr}</p>
      ) : null}

      <div className="dsa-results__cases">
        {caseResults.map((caseResult) => (
          <article key={`${caseResult.case_index}-${caseResult.passed ? "ok" : "bad"}`} className={`dsa-case-card ${caseResult.passed ? "dsa-case-card--pass" : "dsa-case-card--fail"}`}>
            <div className="dsa-case-card__head">
              <strong>Case {caseResult.case_index}</strong>
              <span>{caseResult.passed ? "Pass" : "Fail"}</span>
            </div>
            {caseResult.error ? <p className="dsa-case-card__error">{caseResult.error}</p> : null}
            <div className="dsa-case-card__io">
              <div>
                <span>Expected</span>
                <pre>{caseResult.expected_output || "(empty)"}</pre>
              </div>
              <div>
                <span>Actual</span>
                <pre>{caseResult.actual_output || "(empty)"}</pre>
              </div>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
