"""Hard-skill extraction with a pretrained JobBERT model.

Uses ``jjzha/jobbert_knowledge_extraction`` (Zhang et al., NAACL 2022): BERT
continued-pretrained on 3.2M job-posting sentences, fine-tuned on SkillSpan to
tag "knowledge" spans - tools, languages, frameworks ("Python", "Docker",
"REST APIs"). It finds skills the hand-written role keyword lists don't name.

The model is only loaded when it is already on disk, so tests and CI never
trigger a 431 MB download. Fetch it once with
``python scripts/download_models.py``. Without it, callers get an empty list
and fall back to keyword matching.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from functools import lru_cache
from typing import Any

_LOGGER = logging.getLogger(__name__)

SKILL_MODEL_NAME = os.getenv("SKILL_MODEL_NAME", "jjzha/jobbert_knowledge_extraction")
SKILL_MODEL_FILES = ["*.json", "*.txt", "model.safetensors"]

# BERT reads at most 512 sub-word tokens; ~120 words per chunk stays well under.
_CHUNK_WORDS = 120
_MIN_SCORE = 0.5


def _disabled() -> bool:
	return os.getenv("DISABLE_SKILL_EXTRACTOR", "").strip().lower() in {"1", "true", "yes"}


# lru_cache does not stop two threads (the post-upload prewarm and a quick
# role selection) from loading the 431 MB model at the same time.
_LOAD_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _load_pipeline() -> Any | None:
	with _LOAD_LOCK:
		return _load_pipeline_unlocked()


def _load_pipeline_unlocked() -> Any | None:
	if _disabled():
		return None
	try:
		from huggingface_hub import snapshot_download
		from transformers import pipeline

		path = snapshot_download(SKILL_MODEL_NAME, allow_patterns=SKILL_MODEL_FILES, local_files_only=True)
		return pipeline("token-classification", model=path, aggregation_strategy="first", device=-1)
	except Exception as exc:  # model not downloaded, or transformers missing
		_LOGGER.info("Skill extractor unavailable (%s); using keyword matching only.", exc.__class__.__name__)
		return None


def skill_extractor_available() -> bool:
	return _load_pipeline() is not None


def warmup_skill_extractor() -> bool:
	"""Load the model and run it once at startup. The first inference after a
	load is several times slower than later ones, and without this the first
	user to choose a role pays for both."""
	nlp = _load_pipeline()
	if nlp is None:
		return False
	try:
		nlp("Python and Docker")
	except Exception:
		_LOGGER.warning("Skill extractor warm-up failed.", exc_info=True)
	return True


def _chunks(text: str) -> list[str]:
	chunks: list[str] = []
	current: list[str] = []
	for line in text.splitlines():
		words = line.split()
		if not words:
			continue
		if current and len(current) + len(words) > _CHUNK_WORDS:
			chunks.append(" ".join(current))
			current = []
		current.extend(words[: _CHUNK_WORDS * 2])
		current[-1] += ";"
	if current:
		chunks.append(" ".join(current))
	return chunks


# Words the model glues onto a skill ("git experience", "mongodb schemas").
_FILLER_WORDS = {
	"experience", "skills", "skill", "knowledge", "technology", "technologies",
	"development", "tools", "framework", "frameworks", "schemas", "schema",
	"projects", "project", "basics", "fundamentals", "proficiency",
	"apis", "applications", "apps", "services",
}
# Degree names and headings it sometimes tags ("Computer Science", "Education").
_NOT_A_SKILL = {"science", "engineering", "education", "degree", "university", "college", "b.tech", "bachelor"}


def _clean(span: str) -> list[str]:
	"""One model span -> zero or more tidy skills ("AWS, REST" -> aws, rest)."""
	skills = []
	for part in re.split(r"[,;•|]", span.lower()):
		words = [w for w in re.sub(r"\s+", " ", part).strip(" .:()[]{}-–*\\\"'").split(" ") if w]
		if _NOT_A_SKILL & set(words):
			continue
		while len(words) > 1 and words[-1] in _FILLER_WORDS:
			words.pop()
		while len(words) > 1 and words[0] in _FILLER_WORDS:
			words.pop(0)
		if len(words) == 1 and words[0] in _FILLER_WORDS - {"apis"}:
			continue
		skill = " ".join(words)
		if 2 <= len(skill) <= 40 and re.search(r"[a-z]", skill):
			skills.append(skill)
	return skills


@lru_cache(maxsize=64)
def extract_skills(text: str) -> tuple[str, ...]:
	"""Distinct hard skills named in ``text``, in order of first mention.
	Empty when the model is unavailable."""

	nlp = _load_pipeline()
	if nlp is None or not (text or "").strip():
		return ()
	try:
		results = nlp(_chunks(text))
	except Exception:
		_LOGGER.warning("Skill extraction failed.", exc_info=True)
		return ()
	if results and isinstance(results[0], dict):  # a single chunk returns a flat list
		results = [results]
	seen: dict[str, None] = {}
	for entities in results:
		for entity in entities:
			if float(entity.get("score", 0)) < _MIN_SCORE:
				continue
			for skill in _clean(str(entity.get("word", ""))):
				seen.setdefault(skill, None)
	return tuple(seen)
