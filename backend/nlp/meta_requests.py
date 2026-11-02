"""Spot a candidate asking about the question instead of answering it.

"Can you repeat that?" or "What do you mean?" is not an answer: scoring it
as one marks the candidate down, and handing it to the interviewer model let
it move on to the next topic (the technical round did; HR happened not to).
The engine answers these itself - the same question again, or a short
clarification - and the next thing the candidate says is the answer.
"""

from __future__ import annotations

import re
from typing import Literal

MetaRequest = Literal["repeat", "clarify"]

# Longer than this, it is an answer that happens to use one of the phrases
# ("I'd repeat the request with backoff ...").
_MAX_WORDS = 14

_CLARIFY = re.compile(
	r"\b("
	r"rephrase|reword|clarify|explain the question|"
	r"what do you mean|what does (that|it) mean|"
	r"(don't|do not|didn't|did not) (understand|get) (the question|what you)|"
	r"what (is|was) the question|not sure what you('re| are) asking|"
	r"in (simpler|simple|other) words"
	r")\b"
)

_REPEAT = re.compile(
	r"\b("
	r"repeat|say (that|it) again|come again|pardon|once more|one more time|"
	r"(didn't|did not|couldn't|could not|can't|cannot) (catch|hear) (that|it|you|the question)|"
	r"(didn't|did not) get (that|the question)|missed (that|it|the question)|"
	r"ask (that|it|the question) again"
	r")\b"
)


def classify_meta_request(text: str) -> MetaRequest | None:
	"""Return "repeat" or "clarify" when the utterance is a request about the
	question, None when it should be treated as an answer."""

	normalized = " ".join(str(text or "").replace("’", "'").casefold().split())
	if not normalized or len(normalized.split()) > _MAX_WORDS:
		return None
	if _CLARIFY.search(normalized):
		return "clarify"
	if _REPEAT.search(normalized):
		return "repeat"
	return None


__all__ = ["MetaRequest", "classify_meta_request"]
