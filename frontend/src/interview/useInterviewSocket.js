import { useCallback, useEffect, useRef } from "react";

import { SOCKET_ACTIONS } from "./interviewReducer.js";

const API_DEFAULT = "http://127.0.0.1:8000";

function trimSlash(value) {
	return String(value || "").replace(/\/+$/, "");
}

export function resolveInterviewSocketUrl(baseUrl, sessionId, roundType, accessToken) {
	const url = new URL(trimSlash(baseUrl || API_DEFAULT));
	const normalizedPath = url.pathname.replace(/\/+$/, "");
	url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
	url.pathname = `${normalizedPath}/interview/ws/${encodeURIComponent(sessionId)}/${encodeURIComponent(roundType)}`;
	url.search = "";
	url.hash = "";
	url.searchParams.set("access_token", accessToken);
	return url.toString();
}

/**
 * Socket lifecycle for a conversational round.
 *
 * Every JSON frame maps to a reducer action through SOCKET_ACTIONS, so this
 * hook stays a dispatcher and all interpretation lives in the pure reducer.
 * Binary frames are prompt audio and go straight to playback.
 */
export function useInterviewSocket({ dispatch, onAudio, onOpen, onClose }) {
	const socketRef = useRef(null);
	const manualCloseRef = useRef(false);
	const callbacks = useRef({ onAudio, onOpen, onClose });
	callbacks.current = { onAudio, onOpen, onClose };

	const close = useCallback(() => {
		manualCloseRef.current = true;
		const socket = socketRef.current;
		socketRef.current = null;
		if (socket && socket.readyState <= WebSocket.OPEN) {
			try {
				socket.close();
			} catch {
				// already closing
			}
		}
	}, []);

	const send = useCallback((payload) => {
		const socket = socketRef.current;
		if (!socket || socket.readyState !== WebSocket.OPEN) return false;
		try {
			socket.send(JSON.stringify(payload));
			return true;
		} catch {
			return false;
		}
	}, []);

	const sendBinary = useCallback((buffer) => {
		const socket = socketRef.current;
		if (!socket || socket.readyState !== WebSocket.OPEN) return false;
		try {
			socket.send(buffer);
			return true;
		} catch {
			return false;
		}
	}, []);

	const connect = useCallback(
		(url) => {
			close();
			manualCloseRef.current = false;
			dispatch({ type: "CONNECTION", status: "connecting" });

			const socket = new WebSocket(url);
			socket.binaryType = "arraybuffer";
			socketRef.current = socket;

			socket.onopen = () => {
				dispatch({ type: "CONNECTION", status: "open" });
				callbacks.current.onOpen?.();
			};

			socket.onmessage = (event) => {
				if (typeof event.data !== "string") {
					callbacks.current.onAudio?.(event.data);
					return;
				}

				let payload;
				try {
					payload = JSON.parse(event.data);
				} catch {
					dispatch({
						type: "NOTICE",
						error: "The interview socket returned malformed data.",
					});
					return;
				}

				const actionType = SOCKET_ACTIONS[payload.type];
				if (actionType) dispatch({ type: actionType, payload });
			};

			socket.onerror = () => {
				dispatch({ type: "NOTICE", error: "The interview connection hit a network error." });
			};

			socket.onclose = () => {
				socketRef.current = null;
				dispatch({ type: "CONNECTION", status: "closed" });
				callbacks.current.onClose?.(manualCloseRef.current);
			};

			return socket;
		},
		[close, dispatch],
	);

	useEffect(() => close, [close]);

	return { connect, close, send, sendBinary };
}
