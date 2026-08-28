"""Named Entity Recognition for resume text.

Two-layer extraction:
  Primary  — spaCy ``en_core_web_sm`` when available (PERSON, ORG, GPE, DATE).
  Fallback — curated regex patterns for technologies, skills, and organisations
             when spaCy is not installed or the model is missing.

Skill validation cross-checks Groq-extracted skills against what is actually
present in the raw resume text so downstream modules know which extractions
are text-confirmed vs inferred.

Public API
----------
extract_entities(text)                             -> NERExtractionResult
validate_skills(groq_skills, raw_text)             -> SkillValidationResult
ner_health()                                       -> dict[str, Any]
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Technology / skill regex patterns
# ---------------------------------------------------------------------------

_TECH_REGEXES: list[tuple[str, re.Pattern[str]]] = [
	("programming_language", re.compile(
		r"\b(Python|Java|JavaScript|TypeScript|C\+\+|C#|Kotlin|Swift|Go|Golang|Rust|Scala|"
		r"MATLAB|PHP|Ruby|Dart|Perl|Julia|Haskell|Elixir|Clojure|Groovy|Lua|R)\b",
	)),
	("web_framework", re.compile(
		r"\b(React|Angular|Vue\.?js|Next\.?js|Nuxt\.?js|Django|FastAPI|Flask|"
		r"Spring Boot|Spring|Node\.?js|Express\.?js|Express|Laravel|Rails|"
		r"ASP\.NET|Svelte|Remix|Gatsby)\b",
	)),
	("ml_ai", re.compile(
		r"\b(TensorFlow|PyTorch|Keras|scikit-learn|sklearn|OpenCV|NLTK|spaCy|"
		r"Hugging Face|HuggingFace|BERT|GPT|Transformers|LangChain|LlamaIndex|"
		r"XGBoost|LightGBM|CatBoost|Stable Diffusion|CLIP|Whisper|"
		r"Reinforcement Learning|RAG|embeddings?|vector database)\b",
		re.IGNORECASE,
	)),
	("database", re.compile(
		r"\b(PostgreSQL|MySQL|MongoDB|Redis|SQLite|Cassandra|DynamoDB|"
		r"Elasticsearch|BigQuery|Snowflake|Supabase|Firebase|Oracle|"
		r"MariaDB|CockroachDB|Neo4j|InfluxDB|Pinecone|Weaviate|ChromaDB)\b",
	)),
	("devops_cloud", re.compile(
		r"\b(Docker|Kubernetes|K8s|AWS|GCP|Azure|Terraform|Ansible|Jenkins|"
		r"GitHub Actions|GitLab CI|CI/CD|Helm|ArgoCD|Prometheus|Grafana|"
		r"Nginx|Apache|Linux|Bash|Shell|PowerShell|Pulumi)\b",
	)),
	("data_tools", re.compile(
		r"\b(Pandas|NumPy|Matplotlib|Seaborn|Plotly|Spark|Hadoop|Kafka|"
		r"Airflow|Apache Airflow|dbt|Tableau|Power BI|Looker|Databricks|"
		r"Jupyter|Colab|Streamlit)\b",
	)),
	("mobile", re.compile(
		r"\b(Flutter|React Native|Android|iOS|Xcode|Kotlin|Swift|"
		r"Jetpack Compose|Retrofit|Coroutines|Room)\b",
	)),
	("testing", re.compile(
		r"\b(Pytest|JUnit|Jest|Mocha|Cypress|Selenium|Playwright|"
		r"TestNG|unittest|PyTest|Postman|Insomnia)\b",
		re.IGNORECASE,
	)),
]

# Patterns for educational institutions and organisations (Indian context included)
_ORG_PATTERNS: list[re.Pattern[str]] = [
	re.compile(
		r"\b(IIT|NIT|BITS|VIT|SRM|Amrita|Manipal|IIIT|IISC|IISER|"
		r"Anna University|Bangalore University|Osmania|Jadavpur|"
		r"Institute of Technology|School of Engineering|College of Engineering|"
		r"University of|Technical University)\b",
		re.IGNORECASE,
	),
	re.compile(
		r"\b(Google|Microsoft|Amazon|Meta|Apple|IBM|Infosys|TCS|Wipro|"
		r"HCL|Accenture|Cognizant|Capgemini|Oracle|Salesforce|Adobe|"
		r"Flipkart|Swiggy|Zomato|PayTM|BYJU|Razorpay|Freshworks|"
		r"Atlassian|ThoughtWorks|Zoho)\b",
	),
]

# Common CS skill keywords (token-level match as supplement to regex)
_SKILL_KEYWORDS: frozenset[str] = frozenset({
	"algorithms", "data structures", "oop", "object-oriented", "rest", "api",
	"graphql", "microservices", "agile", "scrum", "git", "github", "gitlab",
	"sql", "nosql", "networking", "security", "cryptography", "blockchain",
	"computer vision", "natural language processing", "nlp",
	"machine learning", "deep learning", "reinforcement learning",
	"distributed systems", "system design", "design patterns", "solid",
	"unit testing", "integration testing", "ci/cd", "devops",
	"cloud computing", "serverless", "containerization",
})

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ExtractedEntity:
	"""A single named entity found in resume text."""

	text: str
	label: str    # PERSON | ORG | GPE | DATE | TECH | SKILL
	start: int    # character offset in source text
	end: int
	source: str   # spacy | regex | keyword


@dataclass
class NERExtractionResult:
	"""Named entity extraction result for one resume text."""

	entities: list[ExtractedEntity]
	persons: list[str]
	organizations: list[str]
	locations: list[str]
	technologies: list[str]
	skills: list[str]
	ner_mode: str            # spacy | regex
	entity_count: int


@dataclass
class SkillValidationResult:
	"""Cross-validation of Groq-extracted skills vs raw resume text."""

	confirmed: list[str]        # in both Groq output and raw text
	unconfirmed: list[str]      # in Groq output but absent from raw text
	extra_found: list[str]      # found by NER/regex but absent from Groq output
	coverage_ratio: float       # confirmed / total_groq_skills (0–1)
	validation_mode: str        # exact | fuzzy


# ---------------------------------------------------------------------------
# spaCy loader (optional)
# ---------------------------------------------------------------------------

_SPACY_NLP: Any | None = None
_SPACY_ATTEMPTED = False


def _load_spacy() -> Any | None:
	global _SPACY_NLP, _SPACY_ATTEMPTED  # noqa: PLW0603
	if _SPACY_ATTEMPTED:
		return _SPACY_NLP
	_SPACY_ATTEMPTED = True
	try:
		spacy = import_module("spacy")
		_SPACY_NLP = spacy.load("en_core_web_sm")
		_LOGGER.info("spaCy en_core_web_sm loaded for NER.")
		return _SPACY_NLP
	except Exception as exc:
		_LOGGER.info("spaCy NER unavailable (%s); using regex fallback.", exc)
		return None


# ---------------------------------------------------------------------------
# Regex-based extraction (always available)
# ---------------------------------------------------------------------------


def _extract_with_regex(text: str) -> NERExtractionResult:
	entities: list[ExtractedEntity] = []
	technologies: list[str] = []
	seen: set[str] = set()

	for label, pattern in _TECH_REGEXES:
		for m in pattern.finditer(text):
			tech = m.group().strip()
			key = tech.lower()
			if key in seen:
				continue
			seen.add(key)
			technologies.append(tech)
			entities.append(ExtractedEntity(
				text=tech, label="TECH",
				start=m.start(), end=m.end(), source="regex",
			))

	organizations: list[str] = []
	for pattern in _ORG_PATTERNS:
		for m in pattern.finditer(text):
			org = m.group().strip()
			key = org.lower()
			if key in seen:
				continue
			seen.add(key)
			organizations.append(org)
			entities.append(ExtractedEntity(
				text=org, label="ORG",
				start=m.start(), end=m.end(), source="regex",
			))

	# Keyword-based skills
	text_lower = text.lower()
	skills: list[str] = []
	for kw in _SKILL_KEYWORDS:
		if kw in text_lower:
			key = kw.lower()
			if key not in seen:
				seen.add(key)
				skills.append(kw)

	return NERExtractionResult(
		entities=entities,
		persons=[],
		organizations=organizations,
		locations=[],
		technologies=technologies,
		skills=skills,
		ner_mode="regex",
		entity_count=len(entities),
	)


# ---------------------------------------------------------------------------
# spaCy-based extraction
# ---------------------------------------------------------------------------


def _extract_with_spacy(text: str, nlp: Any) -> NERExtractionResult:
	try:
		doc = nlp(text[:10_000])  # cap for performance
	except Exception as exc:
		_LOGGER.warning("spaCy processing failed: %s. Falling back to regex.", exc)
		return _extract_with_regex(text)

	entities: list[ExtractedEntity] = []
	persons: list[str] = []
	organizations: list[str] = []
	locations: list[str] = []
	seen_spans: set[str] = set()

	for ent in doc.ents:
		key = ent.text.strip().lower()
		if not key or key in seen_spans:
			continue
		seen_spans.add(key)

		entity_label = ent.label_
		std_label = {
			"PERSON": "PERSON",
			"ORG": "ORG",
			"GPE": "GPE",
			"LOC": "GPE",
			"DATE": "DATE",
			"PRODUCT": "TECH",
			"WORK_OF_ART": "TECH",
			"LANGUAGE": "SKILL",
		}.get(entity_label)
		if std_label is None:
			continue

		e = ExtractedEntity(
			text=ent.text.strip(),
			label=std_label,
			start=ent.start_char,
			end=ent.end_char,
			source="spacy",
		)
		entities.append(e)
		if std_label == "PERSON":
			persons.append(e.text)
		elif std_label == "ORG":
			organizations.append(e.text)
		elif std_label == "GPE":
			locations.append(e.text)

	# Supplement with regex for technology names (spaCy often misses these)
	regex_result = _extract_with_regex(text)
	spacy_tech_keys = {e.text.lower() for e in entities if e.label == "TECH"}
	for e in regex_result.entities:
		if e.label in ("TECH", "SKILL") and e.text.lower() not in spacy_tech_keys:
			entities.append(e)

	technologies = [e.text for e in entities if e.label == "TECH"]
	skills = regex_result.skills

	return NERExtractionResult(
		entities=entities,
		persons=persons,
		organizations=organizations,
		locations=locations,
		technologies=technologies,
		skills=skills,
		ner_mode="spacy",
		entity_count=len(entities),
	)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_entities(text: str) -> NERExtractionResult:
	"""
	Extract named entities from resume text.

	Tries spaCy first; falls back to regex patterns if spaCy is unavailable.

	Parameters
	----------
	text:
		Raw resume text (from PyMuPDF extraction).

	Returns
	-------
	NERExtractionResult
	"""
	if not text or not text.strip():
		return NERExtractionResult(
			entities=[], persons=[], organizations=[], locations=[],
			technologies=[], skills=[], ner_mode="regex", entity_count=0,
		)

	nlp = _load_spacy()
	if nlp is not None:
		return _extract_with_spacy(text, nlp)
	return _extract_with_regex(text)


def validate_skills(
	groq_skills: list[str],
	raw_text: str,
) -> SkillValidationResult:
	"""
	Cross-validate Groq-extracted skills against the raw resume text.

	A skill is *confirmed* when the skill string appears verbatim (case-insensitive)
	in the raw text.  Skills in the text but not in the Groq list are flagged as
	*extra_found* (potential gaps in extraction).

	Parameters
	----------
	groq_skills:
		Skills list from the parsed resume JSON (Groq extraction output).
	raw_text:
		Raw plain text of the resume (before LLM processing).

	Returns
	-------
	SkillValidationResult
	"""
	text_lower = raw_text.lower()
	confirmed: list[str] = []
	unconfirmed: list[str] = []

	for skill in groq_skills:
		if skill.lower() in text_lower:
			confirmed.append(skill)
		else:
			unconfirmed.append(skill)

	# Find NER-detected technologies not already in Groq skills list
	ner_result = extract_entities(raw_text)
	groq_lower = {s.lower() for s in groq_skills}
	extra_found: list[str] = [
		t for t in ner_result.technologies + ner_result.skills
		if t.lower() not in groq_lower
	]

	total_groq = max(len(groq_skills), 1)
	coverage = round(len(confirmed) / total_groq, 4)

	return SkillValidationResult(
		confirmed=confirmed,
		unconfirmed=unconfirmed,
		extra_found=extra_found,
		coverage_ratio=coverage,
		validation_mode="exact",
	)


def ner_health() -> dict[str, Any]:
	"""Return NER subsystem status for health checks."""
	nlp = _load_spacy()
	return {
		"ner_mode": "spacy" if nlp is not None else "regex",
		"spacy_available": nlp is not None,
		"spacy_model": "en_core_web_sm" if nlp is not None else None,
		"tech_pattern_count": len(_TECH_REGEXES),
		"skill_keyword_count": len(_SKILL_KEYWORDS),
	}


__all__ = [
	"ExtractedEntity",
	"NERExtractionResult",
	"SkillValidationResult",
	"extract_entities",
	"validate_skills",
	"ner_health",
]
