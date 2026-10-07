import { useCallback, useEffect, useRef } from "react";

import { MicVAD } from "@ricky0123/vad-web";

import { VAD_ASSET_OPTIONS } from "./vadAssets.js";

export function float32ToInt16(input) {
	const output = new Int16Array(input.length);
	for (let index = 0; index < input.length; index += 1) {
		const clamped = Math.max(-1, Math.min(1, input[index]));
		output[index] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
	}
	return output;
}

/**
 * Microphone capture with client-side voice activity detection.
 *
 * MicVAD buffers a whole utterance and hands it over at end of speech, so the
 * server receives one complete utterance per binary frame rather than a stream
 * of slices. Turn boundaries are therefore explicit signals, not something the
 * server re-derives from audio energy.
 *
 * `getPhase` is a function rather than a value because MicVAD captures its
 * callbacks once: reading a prop directly here would see whatever the phase was
 * when the microphone started, forever.
 */
export function useMicVad({ enabled, getPhase, onSpeechStart, onUtterance, onFrame, onReady, onFailure, autoStart = true }) {
	const vadRef = useRef(null);
	const startedRef = useRef(false);

	const handlers = useRef({ getPhase, onSpeechStart, onUtterance, onFrame });
	handlers.current = { getPhase, onSpeechStart, onUtterance, onFrame };

	const stop = useCallback(() => {
		const vad = vadRef.current;
		vadRef.current = null;
		startedRef.current = false;
		if (vad) {
			try {
				vad.destroy();
			} catch {
				// already torn down
			}
		}
	}, []);

	useEffect(() => {
		let cancelled = false;

		async function start() {
			if (!enabled || startedRef.current) return;
			startedRef.current = true;

			try {
				const vad = await MicVAD.new({
					...VAD_ASSET_OPTIONS,
					onSpeechStart: () => {
						handlers.current.onSpeechStart?.(handlers.current.getPhase?.());
					},
					onSpeechEnd: (audio) => {
						const phase = handlers.current.getPhase?.();
						handlers.current.onUtterance?.(float32ToInt16(audio).buffer, phase);
					},
					positiveSpeechThreshold: 0.8,
					negativeSpeechThreshold: 0.65,
					preSpeechPadFrames: 5,
					minSpeechFrames: 3,
					// Every 16 kHz frame while listening, speech or not: push-to-talk
					// records everything between the two taps.
					onFrameProcessed: (_probs, frame) => {
						if (frame) handlers.current.onFrame?.(frame);
					},
					// Pausing hands over the utterance in progress instead of
					// discarding it, so "send" never drops the last sentence.
					submitUserSpeechOnPause: true,
				});

				if (cancelled) {
					try {
						vad.destroy();
					} catch {
						// nothing to clean up
					}
					return;
				}

				vadRef.current = vad;
				if (autoStart) vad.start();
				onReady?.();
			} catch (error) {
				startedRef.current = false;
				onFailure?.(error);
			}
		}

		start();
		return () => {
			cancelled = true;
		};
		// onReady/onFailure are intentionally excluded: re-running this would tear
		// down and re-request the microphone on every render.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [enabled]);

	useEffect(() => stop, [stop]);

	// Push-to-talk: resume() starts listening; pause() stops and synchronously
	// emits any speech in progress through onUtterance.
	const resume = useCallback(() => vadRef.current?.start(), []);
	const pause = useCallback(() => vadRef.current?.pause(), []);

	return { stop, pause, resume };
}
