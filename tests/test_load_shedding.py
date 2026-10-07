"""Phase 2.10: one slow dependency must not take the whole worker down with it.

Two defects, both measured before fixing:

* The groq SDK retries on its own (``max_retries=2``) underneath our retry loop,
  so every failing call was multiplied by three — 2 configured attempts made 6
  HTTP requests, the production defaults up to 12 (~4 minutes at a 20 s
  timeout), and a 429 was retried by the SDK before our fail-fast saw it.
* ``parse_resume_upload`` is ``async def`` but ran the whole parse — PDF, Groq
  extraction, embedding match — inline on the event loop, so one upload froze
  every live interview socket in the process.
"""

from __future__ import annotations

import asyncio
import dataclasses
import http.server
import threading
import time
from io import BytesIO
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from starlette.datastructures import Headers, UploadFile

from backend import config
from backend.api import routes_resume
from backend.api.auth import AuthenticatedUser
from backend.nlp import groq_client


class _CountingServer:
    """A local stand-in for the Groq API that fails every request."""

    def __init__(self, status: int) -> None:
        self.requests = 0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                outer.requests += 1
                self.rfile.read(int(self.headers.get("content-length", 0)))
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"failing on purpose"}}')

            def log_message(self, *_args) -> None:
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_port}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _settings(base_url: str, max_retries: int):
    return dataclasses.replace(
        config.get_settings().groq,
        api_key="test-key",
        api_base_url=base_url,
        timeout_seconds=2.0,
        max_retries=max_retries,
        backoff_base_seconds=0.01,
    )


class GroqRetryAmplificationTests(TestCase):
    def setUp(self) -> None:
        groq_client.get_groq_client.cache_clear()
        groq_client.get_async_groq_client.cache_clear()

    def _run(self, status: int, max_retries: int) -> int:
        server = _CountingServer(status)
        try:
            with self.assertRaises(Exception):
                groq_client.create_chat_completion(
                    settings=_settings(server.url, max_retries),
                    model="m",
                    temperature=0,
                    messages=[{"role": "user", "content": "x"}],
                )
            return server.requests
        finally:
            server.close()

    def test_each_configured_attempt_is_exactly_one_request(self) -> None:
        self.assertEqual(self._run(500, max_retries=1), 2)
        self.assertEqual(self._run(500, max_retries=0), 1)

    def test_a_rate_limit_fails_fast_on_the_first_request(self) -> None:
        """Retrying a 429 burns quota that is already exhausted."""

        self.assertEqual(self._run(429, max_retries=3), 1)

    def test_the_streaming_client_is_not_amplified_either(self) -> None:
        server = _CountingServer(500)

        async def consume() -> None:
            async for _ in groq_client.stream_chat_completion(
                settings=_settings(server.url, 1),
                model="m",
                temperature=0,
                messages=[{"role": "user", "content": "x"}],
            ):
                pass

        try:
            with self.assertRaises(Exception):
                asyncio.run(consume())
            self.assertEqual(server.requests, 2)
        finally:
            server.close()


class ResumeUploadOffTheLoopTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        config.reset_settings()

    def tearDown(self) -> None:
        config.reset_settings()

    async def test_a_slow_parse_does_not_block_the_event_loop(self) -> None:
        """The symptom that matters: other coroutines (interview sockets) keep
        running while a resume is being parsed."""

        def slow_parse(*_args, **_kwargs):
            time.sleep(0.4)  # stands in for PDF + Groq + embeddings
            return {"ok": True}

        gaps: list[float] = []
        stop = asyncio.Event()

        async def heartbeat() -> None:
            last = time.monotonic()
            while not stop.is_set():
                await asyncio.sleep(0.02)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        upload = UploadFile(
            file=BytesIO(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"),
            filename="resume.pdf",
            headers=Headers({"content-type": "application/pdf"}),
        )
        user = AuthenticatedUser(user_id="u1", email="u@example.com", raw_user={})

        ticker = asyncio.create_task(heartbeat())
        try:
            with patch.object(routes_resume, "_parse_resume_request", slow_parse):
                response = await routes_resume.parse_resume_upload(
                    resume_file=upload,
                    session_id=None,
                    user_id=None,
                    role_selected=None,
                    persist_resume=False,
                    run_role_matching=False,
                    use_groq_profiles=False,
                    persist_role_matches=False,
                    max_roles=5,
                    persist_interview_contexts=False,
                    current_user=user,
                )
        finally:
            stop.set()
            await ticker

        self.assertTrue(response["ok"])
        self.assertLess(
            max(gaps), 0.2, f"event loop stalled for {max(gaps):.2f}s during the parse"
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
