"""Split a streaming LLM response into speakable sentences.

The interview director streams prose first and then a JSON metadata tail behind
a sentinel line. Speech synthesis wants whole sentences as soon as they exist —
waiting for the full response is what makes the interviewer feel slow — but it
must never speak part of the sentinel or the JSON that follows it.

`SentenceAccumulator` is fed raw token deltas and yields complete sentences,
holding back any trailing text that could still turn out to be the start of the
sentinel.
"""

from __future__ import annotations

_DEFAULT_SENTINEL = "<<<META>>>"

# Trailing tokens that end in "." without ending a sentence. Compared
# case-insensitively against the last whitespace-delimited token.
_ABBREVIATIONS = frozenset(
	{
		"e.g.",
		"i.e.",
		"etc.",
		"vs.",
		"dr.",
		"mr.",
		"mrs.",
		"ms.",
		"prof.",
		"fig.",
		"approx.",
		"no.",
		"cf.",
		"al.",
		"inc.",
		"jr.",
		"sr.",
		"st.",
	}
)

_SENTENCE_ENDINGS = frozenset({".", "?", "!"})


class SentenceAccumulator:
	"""Accumulate streamed text and emit it one speakable sentence at a time.

	Args:
		min_chars: shortest chunk worth synthesizing on its own. Without this a
			reply beginning "Right." would spawn a separate TTS call for one word.
		max_chars: hard cap. If the model runs on without punctuation, emit at
			the last sensible break rather than buffering the whole reply.
		sentinel: marks the end of spoken prose. Text at or after it is never
			emitted as speech; it is collected in `tail` instead.
	"""

	__slots__ = ("_buffer", "_max_chars", "_min_chars", "_sentinel", "_sentinel_reached", "_tail")

	def __init__(
		self,
		*,
		min_chars: int = 60,
		max_chars: int = 240,
		sentinel: str = _DEFAULT_SENTINEL,
	) -> None:
		if min_chars < 1:
			raise ValueError("min_chars must be positive.")
		if max_chars < min_chars:
			raise ValueError("max_chars must be >= min_chars.")
		if not sentinel:
			raise ValueError("sentinel must be a non-empty string.")

		self._buffer = ""
		self._tail = ""
		self._min_chars = min_chars
		self._max_chars = max_chars
		self._sentinel = sentinel
		self._sentinel_reached = False

	@property
	def sentinel_reached(self) -> bool:
		"""True once the sentinel has been seen; prose is finished."""

		return self._sentinel_reached

	@property
	def tail(self) -> str:
		"""Everything streamed after the sentinel (the JSON metadata)."""

		return self._tail

	@property
	def pending(self) -> str:
		"""Buffered prose not yet emitted. Exposed for tests and diagnostics."""

		return self._buffer

	def feed(self, delta: str) -> list[str]:
		"""Absorb one streamed fragment and return any sentences now complete."""

		if not delta:
			return []

		if self._sentinel_reached:
			self._tail += delta
			return []

		self._buffer += delta

		marker = self._buffer.find(self._sentinel)
		if marker != -1:
			self._tail = self._buffer[marker + len(self._sentinel) :]
			self._buffer = self._buffer[:marker]
			self._sentinel_reached = True
			return self._drain(len(self._buffer), final=True)

		# The tail of the buffer may be a partial sentinel that the next delta
		# completes, so it is not safe to speak yet. Holding back one character
		# less than the sentinel guarantees a partial match is never emitted.
		safe_len = max(0, len(self._buffer) - (len(self._sentinel) - 1))
		return self._drain(safe_len, final=False)

	def flush(self) -> str | None:
		"""Return any remaining prose at end of stream, or None if empty."""

		if self._sentinel_reached:
			return None
		remainder = self._buffer.strip()
		self._buffer = ""
		return remainder or None

	# -- internals ---------------------------------------------------------

	def _drain(self, limit: int, *, final: bool) -> list[str]:
		chunks: list[str] = []
		while limit > 0 or final:
			cut = self._find_cut(limit, final=final)
			if cut is None:
				break
			chunk = self._buffer[:cut].strip()
			self._buffer = self._buffer[cut:]
			limit = max(0, limit - cut)
			if chunk:
				chunks.append(chunk)
			if final and not self._buffer:
				break

		if final:
			remainder = self._buffer.strip()
			self._buffer = ""
			if remainder:
				chunks.append(remainder)

		return chunks

	def _find_cut(self, limit: int, *, final: bool) -> int | None:
		"""Return the exclusive index to cut a chunk at, or None to keep waiting."""

		searchable = min(limit, len(self._buffer))
		if searchable <= 0:
			return None

		for index in range(searchable):
			char = self._buffer[index]

			if char == "\n":
				# An explicit line break always ends a chunk, regardless of length:
				# the model is signalling a hard boundary.
				if self._buffer[: index + 1].strip():
					return index + 1
				continue

			if char not in _SENTENCE_ENDINGS:
				continue

			candidate = self._buffer[: index + 1]
			if len(candidate.strip()) < self._min_chars:
				continue
			if char == "." and self._ends_with_abbreviation(candidate):
				continue

			# Require the following character so "3.14" or "Node.js" is not split
			# mid-token. At the very end of the stream there is nothing to wait for.
			if index + 1 < len(self._buffer):
				if not self._buffer[index + 1].isspace():
					continue
			elif not final:
				return None

			return index + 1

		if searchable >= self._max_chars:
			return self._force_cut(searchable)

		return None

	def _force_cut(self, searchable: int) -> int | None:
		"""Cut an over-long run at the latest comma or space before the cap.

		The search window is bounded by max_chars, not by the whole safe region:
		scanning further would happily return a break point past the cap.
		"""

		cap = min(searchable, self._max_chars)
		window = self._buffer[:cap]
		for breaker in (", ", ",", " "):
			position = window.rfind(breaker)
			if position >= self._min_chars:
				return position + len(breaker)
		return cap

	@staticmethod
	def _ends_with_abbreviation(candidate: str) -> bool:
		token = candidate.rsplit(None, 1)[-1] if candidate.split() else ""
		return token.casefold() in _ABBREVIATIONS


__all__ = ["SentenceAccumulator"]
