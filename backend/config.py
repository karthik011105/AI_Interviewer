"""Environment-backed runtime settings for backend services."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

# .env lives at the project root (two levels up from this file: backend/config.py)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"

# Opt-in escape hatch for the old "the .env file wins" behaviour. Read from the
# real process environment *before* the .env file is loaded, so a .env file can
# never switch on its own override.
DOTENV_OVERRIDE_ENV_VAR = "DOTENV_OVERRIDE"
_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


def dotenv_override_enabled(env: Mapping[str, str] | None = None) -> bool:
	"""Return True when the real environment asks .env to beat inherited vars."""

	source = os.environ if env is None else env
	return (source.get(DOTENV_OVERRIDE_ENV_VAR) or "").strip().casefold() in _TRUTHY_VALUES


def load_project_dotenv() -> bool:
	"""Load the project-root ``.env`` into ``os.environ``.

	Real environment variables win by default. This is the precedence every
	deployment target assumes (Kubernetes, ECS, Heroku, docker-compose
	``environment:``): a ``.env`` file that ships alongside the application must
	never silently beat platform-supplied configuration.

	Set ``DOTENV_OVERRIDE=true`` in the shell to restore the previous local-dev
	behaviour, where ``.env`` overwrites already-set variables — useful when a
	stale value is stuck in your shell session.

	Returns True if a ``.env`` file was found and read. Safe to call repeatedly;
	this is the single place that decides .env precedence for the whole backend.
	"""

	try:
		from dotenv import load_dotenv as _load_dotenv
	except ModuleNotFoundError:
		return False  # python-dotenv not installed; fall back to raw os.environ

	return bool(
		_load_dotenv(dotenv_path=_ENV_FILE, override=dotenv_override_enabled())
	)


load_project_dotenv()


class ConfigurationError(RuntimeError):
	"""Raised when backend configuration is missing or invalid."""


def _read_int(
	source: Mapping[str, str],
	name: str,
	default: int,
	*,
	minimum: int | None = None,
) -> int:
	raw_value = (source.get(name) or "").strip()
	if not raw_value:
		value = default
	else:
		try:
			value = int(raw_value)
		except ValueError as exc:
			raise ConfigurationError(
				f"Environment variable {name} must be an integer."
			) from exc

	if minimum is not None and value < minimum:
		raise ConfigurationError(
			f"Environment variable {name} must be greater than or equal to {minimum}."
		)
	return value


def _read_float(
	source: Mapping[str, str],
	name: str,
	default: float,
	*,
	minimum: float | None = None,
) -> float:
	raw_value = (source.get(name) or "").strip()
	if not raw_value:
		value = default
	else:
		try:
			value = float(raw_value)
		except ValueError as exc:
			raise ConfigurationError(
				f"Environment variable {name} must be a number."
			) from exc

	if minimum is not None and value < minimum:
		raise ConfigurationError(
			f"Environment variable {name} must be greater than or equal to {minimum}."
		)
	return value


def _read_csv(
	source: Mapping[str, str],
	name: str,
	default: tuple[str, ...],
) -> tuple[str, ...]:
	raw_value = (source.get(name) or "").strip()
	if not raw_value:
		return default

	values = tuple(item.strip() for item in raw_value.split(",") if item.strip())
	return values or default


def _read_bool(
	source: Mapping[str, str],
	name: str,
	default: bool,
) -> bool:
	raw_value = (source.get(name) or "").strip()
	if not raw_value:
		return default

	normalized = raw_value.casefold()
	if normalized in {"1", "true", "yes", "on"}:
		return True
	if normalized in {"0", "false", "no", "off"}:
		return False

	raise ConfigurationError(
		f"Environment variable {name} must be a boolean (true/false)."
	)


@dataclass(frozen=True, slots=True)
class GroqSettings:
	"""Groq API settings used by LLM-backed services."""

	api_key: str
	api_base_url: str
	resume_parser_model: str
	answer_evaluator_model: str
	feedback_generator_model: str
	timeout_seconds: float
	max_retries: int
	backoff_base_seconds: float


@dataclass(frozen=True, slots=True)
class ResumeParsingSettings:
	"""Thresholds and limits for PDF resume parsing."""

	min_text_characters: int
	max_text_characters: int
	max_upload_bytes: int
	page_separator: str


@dataclass(frozen=True, slots=True)
class Judge0Settings:
	"""Settings for the self-hosted Judge0 execution service."""

	api_base_url: str
	# Authentication for the Judge0 instance. The header name must match
	# AUTHN_HEADER in docker-compose.judge0.yml. Optional so an existing local
	# Judge0 running without authentication keeps working.
	auth_header: str
	auth_token: str
	python_language_id: int
	cpp_language_id: int
	java_language_id: int
	request_timeout_seconds: float
	cpu_time_limit_seconds: float
	wall_time_limit_seconds: float
	memory_limit_kb: int
	max_source_characters: int
	max_case_output_characters: int


@dataclass(frozen=True, slots=True)
class CorsSettings:
	"""Allowed browser origins for the API.

	``allowed_origins`` defaults to the Vite dev server so local development
	keeps working with zero configuration. A real deployment must set
	CORS_ALLOWED_ORIGINS to its actual frontend domain(s) — without it, a
	browser running the production frontend cannot call this API at all.
	"""

	allowed_origins: tuple[str, ...]
	# Matches only http://localhost:<port> / http://127.0.0.1:<port> — useful
	# for local dev against any Vite port, harmless in production because no
	# real browser sends that Origin for a deployed page.
	allow_origin_regex: str


@dataclass(frozen=True, slots=True)
class MongoSettings:
	"""MongoDB connection settings."""
	uri: str
	database_name: str


@dataclass(frozen=True, slots=True)
class AuthSettings:
	"""Authentication, password-policy, and abuse-protection settings."""

	jwt_secret: str
	jwt_algorithm: str
	jwt_expiry_hours: int
	# Password policy. The maximum is a byte length, not a character count,
	# because bcrypt rejects secrets longer than 72 bytes outright — a 30
	# character emoji or CJK password already exceeds that.
	password_min_length: int
	password_max_bytes: int
	# Per-client-IP request budget applied across the auth routes.
	rate_limit_max_attempts: int
	rate_limit_window_seconds: int
	# Per-account backoff applied after consecutive failed sign-in attempts.
	login_max_failures: int
	login_lockout_seconds: int


@dataclass(frozen=True, slots=True)
class QuotaSettings:
	"""Per-user spend caps on the routes that cost real money.

	These exist to bound cost, not to stop abuse in the security sense — the auth
	throttles in AuthSettings do that. Every route below either calls a paid LLM
	API or consumes sandboxed compute, so an unbounded caller can run up a bill
	without doing anything that looks like an attack.

	All windows are one hour. Counters are per-process, with the same scaling
	caveat as the auth throttles (see backend/api/rate_limit.py).
	"""

	resume_parse_max_per_hour: int
	role_match_max_per_hour: int
	dsa_execution_max_per_hour: int
	voice_max_per_hour: int
	# Answer evaluations during a live interview round. Driven by WebSocket
	# messages rather than requests, so a client can send them in a tight loop.
	interview_turn_max_per_hour: int
	quota_window_seconds: int


@dataclass(frozen=True, slots=True)
class AppSettings:
	"""Top-level backend settings exposed to service modules."""

	groq: GroqSettings | None
	resume_parsing: ResumeParsingSettings
	judge0: Judge0Settings
	mongo: MongoSettings
	auth: AuthSettings
	quotas: QuotaSettings
	cors: CorsSettings
	allow_local_resume_path_api: bool

	@classmethod
	def from_env(cls, env: Mapping[str, str] | None = None) -> "AppSettings":
		source = env or os.environ
		resume_parsing = ResumeParsingSettings(
			min_text_characters=_read_int(
				source,
				"RESUME_MIN_TEXT_CHARACTERS",
				200,
				minimum=1,
			),
			max_text_characters=_read_int(
				source,
				"RESUME_MAX_TEXT_CHARACTERS",
				20000,
				minimum=200,
			),
			max_upload_bytes=_read_int(
				source,
				"RESUME_MAX_UPLOAD_BYTES",
				5 * 1024 * 1024,
				minimum=1024,
			),
			page_separator=source.get("RESUME_PAGE_SEPARATOR", "\n\n"),
		)

		if resume_parsing.max_text_characters < resume_parsing.min_text_characters:
			raise ConfigurationError(
				"RESUME_MAX_TEXT_CHARACTERS must be greater than or equal to "
				"RESUME_MIN_TEXT_CHARACTERS."
			)

		judge0_settings = Judge0Settings(
			api_base_url=(
				(source.get("JUDGE0_API_BASE_URL") or "http://127.0.0.1:2358")
				.strip()
				.rstrip("/")
			),
			auth_header=(
				(source.get("JUDGE0_AUTH_HEADER") or "X-Auth-Token").strip()
			),
			auth_token=(source.get("JUDGE0_AUTH_TOKEN") or "").strip(),
			python_language_id=_read_int(
				source,
				"JUDGE0_PYTHON_LANGUAGE_ID",
				71,
				minimum=1,
			),
			cpp_language_id=_read_int(
				source,
				"JUDGE0_CPP_LANGUAGE_ID",
				54,
				minimum=1,
			),
			java_language_id=_read_int(
				source,
				"JUDGE0_JAVA_LANGUAGE_ID",
				62,
				minimum=1,
			),
			request_timeout_seconds=_read_float(
				source,
				"JUDGE0_REQUEST_TIMEOUT_SECONDS",
				15.0,
				minimum=0.5,
			),
			cpu_time_limit_seconds=_read_float(
				source,
				"JUDGE0_CPU_TIME_LIMIT_SECONDS",
				2.0,
				minimum=0.5,
			),
			wall_time_limit_seconds=_read_float(
				source,
				"JUDGE0_WALL_TIME_LIMIT_SECONDS",
				4.0,
				minimum=1.0,
			),
			memory_limit_kb=_read_int(
				source,
				"JUDGE0_MEMORY_LIMIT_KB",
				262144,
				minimum=32768,
			),
			max_source_characters=_read_int(
				source,
				"JUDGE0_MAX_SOURCE_CHARACTERS",
				50000,
				minimum=1000,
			),
			max_case_output_characters=_read_int(
				source,
				"JUDGE0_MAX_CASE_OUTPUT_CHARACTERS",
				4000,
				minimum=256,
			),
		)

		groq_api_key = (source.get("GROQ_API_KEY") or "").strip()
		groq_settings: GroqSettings | None = None
		if groq_api_key:
			_raw_base = (
				(source.get("GROQ_API_BASE_URL") or "https://api.groq.com/openai/v1")
				.strip()
			)
			_base = _raw_base.rstrip("/")
			if _base.endswith("/openai/v1"):
				_base = _base[: -len("/openai/v1")]

			groq_settings = GroqSettings(
				api_key=groq_api_key,
				api_base_url=_base,
				resume_parser_model=(
					(source.get("GROQ_RESUME_MODEL") or "openai/gpt-oss-120b")
					.strip()
				),
				answer_evaluator_model=(
					(source.get("GROQ_EVALUATOR_MODEL") or "openai/gpt-oss-20b")
					.strip()
				),
				feedback_generator_model=(
					(source.get("GROQ_FEEDBACK_MODEL") or "openai/gpt-oss-20b")
					.strip()
				),
				timeout_seconds=_read_float(
					source,
					"GROQ_TIMEOUT_SECONDS",
					20.0,
					minimum=1.0,
				),
				max_retries=_read_int(
					source,
					"GROQ_MAX_RETRIES",
					3,
					minimum=0,
				),
				backoff_base_seconds=_read_float(
					source,
					"GROQ_BACKOFF_BASE_SECONDS",
					1.0,
					minimum=0.0,
				),
			)

		mongo_settings = MongoSettings(
			uri=(source.get("MONGO_URI") or "mongodb://localhost:27017").strip(),
			database_name=(source.get("MONGO_DB_NAME") or "interview_simulator").strip(),
		)

		auth_jwt_secret = (source.get("AUTH_JWT_SECRET") or "").strip()
		if not auth_jwt_secret:
			raise ConfigurationError("AUTH_JWT_SECRET environment variable is missing.")

		auth_settings = AuthSettings(
			jwt_secret=auth_jwt_secret,
			jwt_algorithm=(source.get("AUTH_JWT_ALGORITHM") or "HS256").strip(),
			jwt_expiry_hours=_read_int(source, "AUTH_JWT_EXPIRY_HOURS", 24, minimum=1),
			password_min_length=_read_int(
				source,
				"AUTH_PASSWORD_MIN_LENGTH",
				8,
				minimum=8,
			),
			password_max_bytes=_read_int(
				source,
				"AUTH_PASSWORD_MAX_BYTES",
				72,
				minimum=8,
			),
			rate_limit_max_attempts=_read_int(
				source,
				"AUTH_RATE_LIMIT_MAX_ATTEMPTS",
				10,
				minimum=1,
			),
			rate_limit_window_seconds=_read_int(
				source,
				"AUTH_RATE_LIMIT_WINDOW_SECONDS",
				60,
				minimum=1,
			),
			login_max_failures=_read_int(
				source,
				"AUTH_LOGIN_MAX_FAILURES",
				5,
				minimum=1,
			),
			login_lockout_seconds=_read_int(
				source,
				"AUTH_LOGIN_LOCKOUT_SECONDS",
				300,
				minimum=1,
			),
		)

		if auth_settings.password_max_bytes > 72:
			raise ConfigurationError(
				"AUTH_PASSWORD_MAX_BYTES cannot exceed 72 because bcrypt rejects "
				"longer secrets."
			)
		if auth_settings.password_min_length > auth_settings.password_max_bytes:
			raise ConfigurationError(
				"AUTH_PASSWORD_MIN_LENGTH must be less than or equal to "
				"AUTH_PASSWORD_MAX_BYTES."
			)

		quota_settings = QuotaSettings(
			resume_parse_max_per_hour=_read_int(
				source,
				"QUOTA_RESUME_PARSE_PER_HOUR",
				10,
				minimum=1,
			),
			role_match_max_per_hour=_read_int(
				source,
				"QUOTA_ROLE_MATCH_PER_HOUR",
				20,
				minimum=1,
			),
			dsa_execution_max_per_hour=_read_int(
				source,
				"QUOTA_DSA_EXECUTION_PER_HOUR",
				60,
				minimum=1,
			),
			voice_max_per_hour=_read_int(
				source,
				"QUOTA_VOICE_PER_HOUR",
				200,
				minimum=1,
			),
			interview_turn_max_per_hour=_read_int(
				source,
				"QUOTA_INTERVIEW_TURN_PER_HOUR",
				120,
				minimum=1,
			),
			quota_window_seconds=_read_int(
				source,
				"QUOTA_WINDOW_SECONDS",
				3600,
				minimum=1,
			),
		)

		cors_settings = CorsSettings(
			allowed_origins=_read_csv(
				source,
				"CORS_ALLOWED_ORIGINS",
				("http://127.0.0.1:5173", "http://localhost:5173"),
			),
			allow_origin_regex=(
				source.get("CORS_ALLOW_ORIGIN_REGEX")
				or r"http://(127\.0\.0\.1|localhost):(517[0-9]|3000)"
			),
		)

		return cls(
			groq=groq_settings,
			resume_parsing=resume_parsing,
			judge0=judge0_settings,
			mongo=mongo_settings,
			auth=auth_settings,
			quotas=quota_settings,
			cors=cors_settings,
			allow_local_resume_path_api=_read_bool(
				source,
				"ENABLE_LOCAL_RESUME_PATH_API",
				False,
			),
		)


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
	"""Return cached backend settings for the current process."""

	return AppSettings.from_env()


def reset_settings() -> None:
	"""Clear cached backend settings.

	This is mainly useful for tests that need to override environment variables.
	"""

	get_settings.cache_clear()

__all__ = [
	"AppSettings",
	"AuthSettings",
	"ConfigurationError",
	"CorsSettings",
	"DOTENV_OVERRIDE_ENV_VAR",
	"dotenv_override_enabled",
	"load_project_dotenv",
	"GroqSettings",
	"Judge0Settings",
	"MongoSettings",
	"ResumeParsingSettings",
	"get_settings",
	"reset_settings",
]
