// Typing Maya's words out as she says them.
//
// The model writes a question much faster than it is spoken, so showing each
// sentence the moment it arrives put the whole question on screen before the
// voice had said it. Instead every sentence is revealed while its own audio
// plays: the server sends one audio chunk per sentence, in order, and the
// audio player reports when each chunk starts and how long it lasts.

/** Reading-speed typing for sentences that never get audio (voice off or failed). */
export const FALLBACK_CHARS_PER_SECOND = 32;
/** How long after the turn ends to wait for late audio before typing without it. */
export const NO_AUDIO_GRACE_MS = 1500;

function clamp(value, low, high) {
	return Math.min(high, Math.max(low, value));
}

/**
 * What of an interviewer turn should be on screen at time `now` (ms, same
 * clock as the schedule: performance.now()).
 *
 * turn.sentences  sentences as streamed
 * turn.speech     { [sentenceIndex]: { start, duration } } from the audio player
 * turn.endedAt    when the server finished the turn (null while streaming)
 * turn.revealAll  the candidate cut in or answered: show everything
 *
 * Returns { text, spokenText, done }: `text` is what is typed so far,
 * `spokenText` only whole sentences (for screen readers), `done` once
 * everything is shown.
 */
export function revealState(turn, now) {
	const sentences = turn?.sentences || [];
	const full = String(turn?.text || sentences.join(" "));
	if (turn?.role !== "interviewer" || !sentences.length || turn.revealAll) {
		return { text: full, spokenText: full, done: true };
	}

	const typed = [];
	const spoken = [];
	let previousEnd = null;
	for (let index = 0; index < sentences.length; index += 1) {
		const sentence = sentences[index];
		let slot = turn.speech?.[index];
		if (!slot && turn.endedAt != null) {
			// No audio is coming for this sentence: type it at reading speed,
			// after the one before it.
			const start = Math.max(previousEnd ?? 0, turn.endedAt + NO_AUDIO_GRACE_MS);
			slot = { start, duration: (sentence.length / FALLBACK_CHARS_PER_SECOND) * 1000 };
		}
		if (!slot) {
			// Not spoken yet: it, and everything after it, stays hidden.
			return { text: typed.join(" "), spokenText: spoken.join(" "), done: false };
		}
		const progress = slot.duration > 0 ? clamp((now - slot.start) / slot.duration, 0, 1) : now >= slot.start ? 1 : 0;
		const shown = Math.round(progress * sentence.length);
		if (shown > 0) typed.push(sentence.slice(0, shown));
		if (progress < 1) {
			return { text: typed.join(" "), spokenText: spoken.join(" "), done: false };
		}
		spoken.push(sentence);
		previousEnd = slot.start + slot.duration;
	}
	// Everything has been said: show the server's final wording of the turn.
	return { text: full, spokenText: full, done: true };
}
