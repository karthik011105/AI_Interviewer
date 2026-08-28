"""Shared Groq client creation and retry/backoff helpers."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from functools import lru_cache
from importlib import import_module
from typing import Any

from backend.config import GroqSettings

_LOGGER = logging.getLogger(__name__)


class GroqClientError(RuntimeError):
	"""Base error for shared Groq client helpers."""


class GroqDependencyError(GroqClientError):
	"""Raised when the Groq SDK is unavailable."""


class GroqCompletionError(GroqClientError):
	"""Raised when a Groq completion fails after retry/backoff."""


class GroqRateLimitError(GroqCompletionError):
	"""Raised when Groq rejects a request because quota or rate limits were exceeded."""


def _is_rate_limit_error(exc: Exception) -> bool:
	text = str(exc).casefold()
	return any(
		hint in text
		for hint in (
			"429",
			"rate_limit_exceeded",
			"rate limit",
			"limit_reached",
			"tokens per day",
			"tpm",
		)
	)


def _load_groq_sdk() -> Any:
	try:
		return import_module("groq")
	except ModuleNotFoundError as exc:
		raise GroqDependencyError(
			"groq SDK is not installed. Install it with `pip install groq`."
		) from exc


@lru_cache(maxsize=4)
def get_groq_client(api_key: str, api_base_url: str) -> Any:
	"""Return a cached Groq client for the configured API key and base URL."""

	groq_module = _load_groq_sdk()
	return groq_module.Groq(api_key=api_key, base_url=api_base_url)


def create_chat_completion(
	*,
	settings: GroqSettings,
	model: str,
	temperature: float,
	messages: Sequence[Mapping[str, Any]],
	timeout: float | None = None,
	response_format: Mapping[str, Any] | None = None,
) -> Any:
	"""Create a Groq chat completion with shared retry/backoff behavior."""

	client = get_groq_client(settings.api_key, settings.api_base_url)
	request_timeout = settings.timeout_seconds if timeout is None else timeout
	max_attempts = settings.max_retries + 1
	last_error: Exception | None = None

	request_kwargs: dict[str, Any] = {
		"model": model,
		"temperature": temperature,
		"messages": list(messages),
		"timeout": request_timeout,
	}
	if response_format is not None:
		request_kwargs["response_format"] = dict(response_format)

	for attempt in range(1, max_attempts + 1):
		try:
			return client.chat.completions.create(**request_kwargs)
		except Exception as exc:
			last_error = exc
			if _is_rate_limit_error(exc):
				raise GroqRateLimitError(f"Groq rate limit reached: {exc}") from exc
			if attempt >= max_attempts:
				break

			sleep_seconds = settings.backoff_base_seconds * (2 ** (attempt - 1))
			_LOGGER.warning(
				"Groq completion attempt %s/%s failed: %s. Retrying in %.2fs.",
				attempt,
				max_attempts,
				exc,
				sleep_seconds,
			)
			time.sleep(sleep_seconds)

	message = f"Groq completion failed after {max_attempts} attempt(s)."
	if last_error is not None:
		message = f"{message} Last error: {last_error}"
		raise GroqCompletionError(message) from last_error
	raise GroqCompletionError(message)


__all__ = [
	"GroqClientError",
	"GroqCompletionError",
	"GroqDependencyError",
	"GroqRateLimitError",
	"create_chat_completion",
	"get_groq_client",
]