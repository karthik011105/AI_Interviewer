import { CheckCircle2, XCircle } from "lucide-react";

function formatTime(seconds) {
	const value = Number(seconds);
	return Number.isFinite(value) ? `${value.toFixed(2)} s` : null;
}

function formatMemory(kb) {
	const value = Number(kb);
	return Number.isFinite(value) ? `${Math.round(value / 1024)} MB` : null;
}

/**
 * Test results after a run or submit. Nothing is shown before the first run
 * (an empty "0/0 passed / Unknown / Not reported" card was just noise).
 * Passing tests are one line each; a failing test opens up to show expected
 * against actual output, since that is the only case where it matters.
 */
export default function TestResults({ executionResults }) {
	if (!executionResults) return null;

	const cases = Array.isArray(executionResults.case_results) ? executionResults.case_results : [];
	const passed = Number(executionResults.passed_count || 0);
	const total = Number(executionResults.total_count || cases.length || 0);
	const allPassed = total > 0 && passed === total;
	// A fresh problem can carry an empty result object; that is "not run yet".
	if (!total && !cases.length && !executionResults.compile_output && !executionResults.stderr) return null;
	const meta = [executionResults.status, formatTime(executionResults.time_seconds), formatMemory(executionResults.memory_kb)]
		.filter(Boolean)
		.join(" · ");

	return (
		<section className="pp-card tr" aria-label="Test results">
			<header className={`tr__head ${allPassed ? "tr__head--ok" : "tr__head--fail"}`}>
				{allPassed ? <CheckCircle2 size={20} /> : <XCircle size={20} />}
				<div>
					<h3>
						{passed} of {total} tests passed
					</h3>
					{meta ? <p>{meta}</p> : null}
				</div>
			</header>

			{executionResults.compile_output ? (
				<pre className="tr__error">{executionResults.compile_output}</pre>
			) : null}
			{executionResults.stderr ? <pre className="tr__error">{executionResults.stderr}</pre> : null}

			{cases.length ? (
				<ol className="tr__list">
					{cases.map((item) => (
						<li key={`${item.case_index}-${item.passed}`} className={`tr__case ${item.passed ? "tr__case--ok" : "tr__case--fail"}`}>
							<div className="tr__case-row">
								<span className="tr__mark" aria-hidden="true">{item.passed ? "✓" : "✗"}</span>
								<span>Test {item.case_index}</span>
								{item.passed ? <code className="tr__out">{item.actual_output || "(empty)"}</code> : null}
							</div>
							{!item.passed ? (
								<div className="tr__diff">
									{item.error ? <p className="tr__case-error">{item.error}</p> : null}
									<div>
										<span>Expected</span>
										<pre>{item.expected_output || "(empty)"}</pre>
									</div>
									<div>
										<span>Your output</span>
										<pre>{item.actual_output || "(empty)"}</pre>
									</div>
								</div>
							) : null}
						</li>
					))}
				</ol>
			) : null}
		</section>
	);
}
