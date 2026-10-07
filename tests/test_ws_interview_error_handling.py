"""The interview WebSocket is the one flow no middleware protects.

Every middleware in this application is a ``BaseHTTPMiddleware`` subclass, and
those never see a websocket scope. So the catch-all exception handler added for
HTTP requests does not apply to ``/interview/ws``, and neither do the request
metrics. The handler itself is the only thing that can tell a candidate what
went wrong, or record that anything went wrong at all.

Before the catch-all in ``interview_websocket``, anything other than a clean
disconnect or a ``WebSocketInterviewError`` escaped the handler entirely: the
socket had been accepted, no frame was sent, ``close()`` was never called, and
the browser saw an abrupt 1006 with nothing to show. Verified for
``DatabaseClientError`` (an Atlas blip), ``ConcurrentUpdateError`` (two tabs
racing one round), and a plain ``ValueError``.
"""

from __future__ import annotations

import asyncio
from unittest import TestCase
from unittest.mock import patch

from fastapi import WebSocketDisconnect
from prometheus_client import REGISTRY

from backend.api import auth as auth_module
from backend.api import ws_interview as ws
from backend.database.db_errors import ConcurrentUpdateError, DatabaseClientError


class _FakeWebSocket:
    """Records what the server sent instead of sending it.

    Starlette's TestClient websocket portal blocks on receive once the server
    side is gone, so the handler is driven directly.
    """

    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[dict] = []
        self.closed: tuple[int, str] | None = None
        self.headers: dict[str, str] = {}
        self.query_params: dict[str, str] = {}

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def send_bytes(self, payload: bytes) -> None:
        self.sent.append({"type": "bytes", "length": len(payload)})

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)

    async def receive(self) -> dict:
        return {"type": "websocket.disconnect"}


_USER = auth_module.AuthenticatedUser(
    user_id="ws-test-user", email="ws@example.com", raw_user={"id": "ws-test-user"}
)


def _counter(round_type: str, outcome: str) -> float:
    value = REGISTRY.get_sample_value(
        "interview_ws_sessions_total", {"round": round_type, "outcome": outcome}
    )
    return 0.0 if value is None else value


class WebSocketErrorReportingTests(TestCase):
    def _run_with_setup_failure(self, error: BaseException) -> _FakeWebSocket:
        socket = _FakeWebSocket()
        with patch.object(ws, "_authenticate_websocket_user", return_value=_USER):
            with patch.object(ws, "_load_parent_session", side_effect=error):
                asyncio.run(ws.interview_websocket(socket, "session-1", "hr"))
        return socket

    def test_an_unexpected_error_still_tells_the_candidate_something(self) -> None:
        for label, error in (
            ("database unreachable", DatabaseClientError("Could not connect")),
            ("concurrent round update", ConcurrentUpdateError("stale state_version")),
            ("an ordinary bug", ValueError("unexpected internal bug")),
        ):
            with self.subTest(case=label):
                socket = self._run_with_setup_failure(error)

                self.assertTrue(socket.accepted)
                error_frames = [
                    frame for frame in socket.sent if frame.get("type") == "error"
                ]
                self.assertEqual(
                    len(error_frames), 1, "exactly one error frame should be sent"
                )
                self.assertEqual(error_frames[0]["code"], "internal_error")
                self.assertIsNotNone(
                    socket.closed, "the socket must be closed, not abandoned"
                )
                self.assertEqual(socket.closed[0], 1011)

    def test_the_error_frame_does_not_leak_the_exception_detail(self) -> None:
        """The detail belongs in the log. A candidate mid-interview should not
        be shown a connection string or a stack frame."""

        socket = self._run_with_setup_failure(
            DatabaseClientError("mongodb://user:hunter2@cluster0.example.net")
        )

        body = str(socket.sent)
        self.assertNotIn("hunter2", body)
        self.assertNotIn("mongodb://", body)

    def test_an_unexpected_error_is_logged_with_a_traceback(self) -> None:
        """This handler is the only place a failed interview can be observed,
        so the log line has to carry the exception."""

        socket = _FakeWebSocket()
        with patch.object(ws, "_authenticate_websocket_user", return_value=_USER):
            with patch.object(
                ws, "_load_parent_session", side_effect=ValueError("boom")
            ):
                with self.assertLogs(ws._LOGGER, level="ERROR") as captured:
                    asyncio.run(ws.interview_websocket(socket, "session-1", "hr"))

        joined = "\n".join(captured.output)
        self.assertIn("session-1", joined)
        self.assertIn("ValueError", joined)
        self.assertIn("boom", joined)

    def test_a_rejection_still_uses_the_policy_close_code(self) -> None:
        """WebSocketInterviewError is something the caller can act on, so it
        keeps 1008 and its own message rather than being flattened into the
        generic internal error."""

        socket = self._run_with_setup_failure(
            ws.WebSocketInterviewError("Session not found.")
        )

        self.assertEqual(socket.closed[0], 1008)
        error_frames = [frame for frame in socket.sent if frame.get("type") == "error"]
        self.assertEqual(error_frames[0]["code"], "session_error")
        self.assertIn("Session not found.", error_frames[0]["message"])

    def test_a_client_disconnect_is_not_reported_as_an_error(self) -> None:
        socket = self._run_with_setup_failure(WebSocketDisconnect(code=1001))

        error_frames = [frame for frame in socket.sent if frame.get("type") == "error"]
        self.assertEqual(error_frames, [])
        self.assertIsNone(socket.closed)

    def test_an_unsupported_round_is_refused_before_accepting(self) -> None:
        """Nothing should be accepted for a round that does not exist."""

        socket = _FakeWebSocket()
        asyncio.run(ws.interview_websocket(socket, "session-1", "not-a-round"))

        self.assertFalse(socket.accepted)
        self.assertIsNotNone(socket.closed)
        self.assertEqual(socket.closed[0], 1008)


class WebSocketMetricsTests(TestCase):
    """The HTTP request metrics cannot see this route, so the counter here is
    the only signal that interviews are failing."""

    def test_an_error_increments_the_error_outcome(self) -> None:
        before = _counter("technical", "error")

        socket = _FakeWebSocket()
        with patch.object(ws, "_authenticate_websocket_user", return_value=_USER):
            with patch.object(
                ws, "_load_parent_session", side_effect=ValueError("boom")
            ):
                asyncio.run(ws.interview_websocket(socket, "session-2", "technical"))

        self.assertEqual(_counter("technical", "error"), before + 1)

    def test_a_rejection_and_an_error_are_counted_separately(self) -> None:
        """Otherwise a spike in 'my session was not found' would be
        indistinguishable from a spike in real failures."""

        before_rejected = _counter("hr", "rejected")
        before_error = _counter("hr", "error")

        socket = _FakeWebSocket()
        with patch.object(ws, "_authenticate_websocket_user", return_value=_USER):
            with patch.object(
                ws,
                "_load_parent_session",
                side_effect=ws.WebSocketInterviewError("Session not found."),
            ):
                asyncio.run(ws.interview_websocket(socket, "session-3", "hr"))

        self.assertEqual(_counter("hr", "rejected"), before_rejected + 1)
        self.assertEqual(_counter("hr", "error"), before_error)

    def test_a_client_disconnect_has_its_own_outcome(self) -> None:
        before = _counter("hr", "client_disconnect")

        socket = _FakeWebSocket()
        with patch.object(ws, "_authenticate_websocket_user", return_value=_USER):
            with patch.object(
                ws, "_load_parent_session", side_effect=WebSocketDisconnect(code=1001)
            ):
                asyncio.run(ws.interview_websocket(socket, "session-4", "hr"))

        self.assertEqual(_counter("hr", "client_disconnect"), before + 1)

    def test_the_round_label_cannot_grow_without_bound(self) -> None:
        """An unbounded Prometheus label is what makes a metrics endpoint fall
        over. The route rejects anything outside this set before accepting, so
        the label is closed."""

        self.assertEqual(ws._VALID_ROUNDS, {"hr", "technical", "project_discussion"})


if __name__ == "__main__":
    import unittest

    unittest.main()
