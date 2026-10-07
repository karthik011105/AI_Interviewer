import { useCallback, useEffect, useRef } from "react";

/**
 * Gapless playback of streamed prompt audio.
 *
 * The backend sends one complete, independently decodable container per
 * sentence rather than one blob per question, so each chunk is decoded on
 * arrival and scheduled against a running cursor. That is what lets the
 * candidate hear the first sentence while the rest is still being synthesized.
 */
export function useAudioPlayback({ onError } = {}) {
	const contextRef = useRef(null);
	const cursorRef = useRef(0);
	const endedRef = useRef(false);
	const activeRef = useRef(false);
	const sourcesRef = useRef(new Set());

	/** Nudge a suspended context awake without ever blocking the caller.
	 *
	 * `resume()` does not settle at all when there is no output device, so it
	 * must never be awaited on the path that decodes and schedules audio -
	 * doing that silently swallows every chunk. Anything scheduled while the
	 * context is still suspended simply starts playing once it wakes.
	 */
	const nudgeResume = useCallback((context) => {
		if (context && context.state === "suspended") {
			Promise.resolve(context.resume()).catch(() => {});
		}
	}, []);

	const ensureContext = useCallback(() => {
		const existing = contextRef.current;
		if (existing && existing.state !== "closed") {
			nudgeResume(existing);
			return existing;
		}

		const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
		if (!AudioContextCtor) {
			onError?.("This browser does not support audio playback.");
			return null;
		}

		let context;
		try {
			context = new AudioContextCtor();
		} catch {
			// No output device, or the browser refused the context. Audio is an
			// enhancement here - the transcript carries the interview - so this
			// must not stop the round from running.
			onError?.("Audio output is unavailable; the interview will run as text.");
			return null;
		}

		contextRef.current = context;
		cursorRef.current = context.currentTime;
		nudgeResume(context);
		return context;
	}, [nudgeResume, onError]);

	const stop = useCallback(() => {
		sourcesRef.current.forEach((source) => {
			source.onended = null;
			try {
				source.stop();
			} catch {
				// already stopped
			}
			try {
				source.disconnect();
			} catch {
				// already disconnected
			}
		});
		sourcesRef.current.clear();

		endedRef.current = true;
		activeRef.current = false;
		const context = contextRef.current;
		if (context && context.state !== "closed") {
			cursorRef.current = context.currentTime;
		}
	}, []);

	const beginTurn = useCallback(() => {
		endedRef.current = false;
		activeRef.current = true;
	}, []);

	const endTurn = useCallback(() => {
		endedRef.current = true;
	}, []);

	const enqueue = useCallback(
		async (arrayBuffer) => {
			if (!arrayBuffer || arrayBuffer.byteLength === 0) return;

			const context = ensureContext();
			if (!context) return;

			let audioBuffer;
			try {
				// decodeAudioData detaches the buffer, so hand it a copy.
				audioBuffer = await context.decodeAudioData(arrayBuffer.slice(0));
			} catch {
				onError?.("Question audio playback failed.");
				return;
			}

			// A chunk can still be in flight when the candidate barges in; playing
			// it would talk over them.
			if (endedRef.current && !activeRef.current) return;

			const source = context.createBufferSource();
			source.buffer = audioBuffer;
			source.connect(context.destination);
			source.onended = () => {
				sourcesRef.current.delete(source);
				try {
					source.disconnect();
				} catch {
					// already disconnected
				}
			};

			const startAt = Math.max(context.currentTime, cursorRef.current || 0);
			sourcesRef.current.add(source);
			source.start(startAt);
			cursorRef.current = startAt + audioBuffer.duration;
			activeRef.current = true;
		},
		[ensureContext, onError],
	);

	useEffect(
		() => () => {
			stop();
			const context = contextRef.current;
			if (context && context.state !== "closed") {
				context.close().catch(() => {});
			}
			contextRef.current = null;
		},
		[stop],
	);

	return { ensureContext, enqueue, stop, beginTurn, endTurn };
}
