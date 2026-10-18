"""The live interview needs uvicorn to have a WebSocket implementation.

Plain ``uvicorn`` ships without one. Every WebSocket test in this suite uses
TestClient, which runs the app in-process and never touches uvicorn's protocol
layer — so when ``websockets`` was missing from requirements.txt, the whole
suite stayed green while the real server rejected every /interview/ws handshake
("No supported WebSocket library detected"). This checks the thing TestClient
cannot: what uvicorn itself will use when it serves the app.
"""

from __future__ import annotations

from unittest import TestCase

from uvicorn.config import Config


class UvicornWebSocketSupportTests(TestCase):
	def test_uvicorn_resolves_a_websocket_protocol(self) -> None:
		config = Config(app="backend.main:app", ws="auto", lifespan="off")
		config.load()
		self.assertIsNotNone(
			config.ws_protocol_class,
			"uvicorn found no WebSocket library; install `websockets` "
			"(see backend/requirements.txt) or the interview socket will refuse every connection",
		)
