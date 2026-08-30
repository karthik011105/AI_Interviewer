const PHASE_COPY = {
	setup: { label: "Not started", hint: "Start the interview when you are ready." },
	connecting: { label: "Connecting", hint: "Opening the interview stream." },
	thinking: { label: "Thinking", hint: "The interviewer is deciding what to ask." },
	speaking: { label: "Interviewer speaking", hint: "Start talking any time to interrupt." },
	listening: { label: "Listening", hint: "Answer out loud, or switch to typing." },
	transcribing: { label: "Transcribing", hint: "Turning your answer into text." },
	clarifying: { label: "Clarifying", hint: "The interviewer is answering your doubt." },
	complete: { label: "Round complete", hint: "" },
};

function Waveform({ active }) {
	return (
		<span className={`mic-bar__wave${active ? " mic-bar__wave--active" : ""}`} aria-hidden="true">
			{Array.from({ length: 9 }, (_, index) => (
				<span key={index} style={{ animationDelay: `${index * 70}ms` }} />
			))}
		</span>
	);
}

export default function MicControlBar({
	phase,
	inputMode,
	captureReady,
	typedAnswer,
	canAnswer,
	ttsPlaying,
	onInterrupt,
	onFinishAnswer,
	onTypedChange,
	onSubmitTyped,
	onToggleInputMode,
	onEndRound,
}) {
	const copy = PHASE_COPY[phase] || PHASE_COPY.listening;
	const typing = inputMode === "typed" || !captureReady;

	return (
		<div className="mic-bar">
			<div className="mic-bar__status">
				<span
					className={`mic-bar__orb mic-bar__orb--${phase}`}
					aria-hidden="true"
				/>
				<Waveform active={phase === "listening" && !typing} />
				<span className="mic-bar__labels">
					<strong>{copy.label}</strong>
					{copy.hint ? <small>{copy.hint}</small> : null}
				</span>
			</div>

			{typing ? (
				<form
					className="mic-bar__typed"
					onSubmit={(event) => {
						event.preventDefault();
						if (typedAnswer.trim()) onSubmitTyped();
					}}
				>
					<textarea
						className="mic-bar__textarea"
						rows={2}
						value={typedAnswer}
						placeholder="Type your answer, then press Send"
						onChange={(event) => onTypedChange(event.target.value)}
						onKeyDown={(event) => {
							if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
								event.preventDefault();
								if (typedAnswer.trim()) onSubmitTyped();
							}
						}}
						disabled={!canAnswer}
					/>
					<div className="mic-bar__actions">
						<button
							type="submit"
							className="primary-button"
							disabled={!canAnswer || !typedAnswer.trim()}
						>
							Send
						</button>
						{captureReady ? (
							<button type="button" className="secondary-button" onClick={onToggleInputMode}>
								Use mic
							</button>
						) : null}
					</div>
				</form>
			) : (
				<div className="mic-bar__actions">
					{ttsPlaying ? (
						<button type="button" className="primary-button" onClick={onInterrupt}>
							Interrupt and answer
						</button>
					) : (
						<button
							type="button"
							className="primary-button"
							onClick={onFinishAnswer}
							disabled={!canAnswer}
						>
							Finish answer
						</button>
					)}
					<button type="button" className="secondary-button" onClick={onToggleInputMode}>
						Type instead
					</button>
				</div>
			)}

			<button
				type="button"
				className="mic-bar__end action-row__button"
				onClick={onEndRound}
				disabled={phase === "complete"}
			>
				End round
			</button>
		</div>
	);
}
