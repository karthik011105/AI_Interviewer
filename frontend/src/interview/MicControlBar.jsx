const PHASE_COPY = {
	setup: { label: "Not started", hint: "Start the interview when you are ready." },
	connecting: { label: "Connecting", hint: "Opening the interview stream." },
	thinking: { label: "Thinking", hint: "The interviewer is deciding what to ask." },
	speaking: { label: "Interviewer speaking", hint: "Tap the mic to interrupt and answer." },
	listening: { label: "Your turn", hint: "Tap the mic to answer, or switch to typing." },
	transcribing: { label: "Transcribing", hint: "Turning your answer into text." },
	clarifying: { label: "Clarifying", hint: "The interviewer is answering your doubt." },
	complete: { label: "Round complete", hint: "" },
};

function MicIcon() {
	return (
		<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
			<rect x="9" y="3" width="6" height="11" rx="3" />
			<path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
		</svg>
	);
}

function SendIcon() {
	return (
		<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
			<path d="M5 12h14M13 6l6 6-6 6" />
		</svg>
	);
}

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
	recording,
	onStartRecording,
	onSendRecording,
	onTypedChange,
	onSubmitTyped,
	onToggleInputMode,
	onEndRound,
}) {
	const baseCopy = PHASE_COPY[phase] || PHASE_COPY.listening;
	const copy = recording
		? { label: "Recording", hint: "Speak your answer, then tap the arrow to send." }
		: baseCopy;
	const typing = inputMode === "typed" || !captureReady;

	return (
		<div className="mic-bar">
			<div className="mic-bar__status">
				<span
					className={`mic-bar__orb mic-bar__orb--${phase}`}
					aria-hidden="true"
				/>
				<Waveform active={recording && !typing} />
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
					<button
						type="button"
						className={`mic-bar__round mic-bar__mic${recording ? " mic-bar__mic--recording" : ""}`}
						onClick={onStartRecording}
						disabled={!canAnswer || recording}
						aria-label={ttsPlaying ? "Interrupt and answer" : "Start answering"}
						title={ttsPlaying ? "Interrupt and answer" : "Start answering"}
					>
						<MicIcon />
					</button>
					<button
						type="button"
						className="mic-bar__round mic-bar__send"
						onClick={onSendRecording}
						disabled={!recording}
						aria-label="Send answer"
						title="Send answer"
					>
						<SendIcon />
					</button>
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
