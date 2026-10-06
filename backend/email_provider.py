"""Pluggable email delivery for account-recovery mail (password reset).

``EMAIL_PROVIDER`` selects the backend:

- ``console`` (the default): logs the email instead of sending it. This is
  real, working behaviour for local development and CI, not a stub to swap
  out later — no account or credentials needed, and the reset link is right
  there in the log for manual testing.
- ``smtp``: sends via any SMTP relay (Gmail, SendGrid, Mailgun, AWS SES,
  Postmark, and effectively every transactional-email provider all speak
  SMTP) using the standard library's ``smtplib`` — deliberately not a
  provider-specific SDK, so switching providers later is a config change,
  not a code change or a new dependency.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Mapping

_LOGGER = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
	"""Raised when an email could not be sent via the configured provider."""


def send_email(*, to: str, subject: str, body: str, env: Mapping[str, str] | None = None) -> None:
	source = env if env is not None else os.environ
	provider = (source.get("EMAIL_PROVIDER") or "console").strip().casefold()

	if provider == "console":
		_LOGGER.info("Email (console provider): to=%s subject=%r\n%s", to, subject, body)
		return

	if provider == "smtp":
		_send_via_smtp(to=to, subject=subject, body=body, source=source)
		return

	raise EmailDeliveryError(
		f"Unknown EMAIL_PROVIDER={provider!r}. Expected 'console' or 'smtp'."
	)


def _send_via_smtp(*, to: str, subject: str, body: str, source: Mapping[str, str]) -> None:
	host = (source.get("SMTP_HOST") or "").strip()
	if not host:
		raise EmailDeliveryError("EMAIL_PROVIDER=smtp requires SMTP_HOST to be set.")

	port_raw = (source.get("SMTP_PORT") or "587").strip()
	try:
		port = int(port_raw)
	except ValueError as exc:
		raise EmailDeliveryError(f"SMTP_PORT={port_raw!r} is not a valid port number.") from exc

	username = (source.get("SMTP_USERNAME") or "").strip()
	password = (source.get("SMTP_PASSWORD") or "").strip()
	from_address = (source.get("SMTP_FROM_ADDRESS") or username or "no-reply@localhost").strip()
	security = resolve_smtp_security(source, port)

	message = EmailMessage()
	message["Subject"] = subject
	message["From"] = from_address
	message["To"] = to
	message.set_content(body)

	# A VERIFYING context, passed explicitly. smtplib's own default — what
	# starttls() and SMTP_SSL use when given no context — is
	# ssl._create_stdlib_context(), which checks neither the certificate nor
	# the hostname. Measured, not assumed. That made the encryption decorative:
	# anyone on-path could present a self-signed certificate and read the
	# message, and the message is a password-reset link, i.e. a credential.
	tls_context = ssl.create_default_context()

	try:
		if security == "ssl":
			# Implicit TLS (port 465). The connection is encrypted from the first
			# byte, so it must be opened with SMTP_SSL; issuing STARTTLS on it
			# instead hangs until the timeout, which is what used to happen.
			connection = smtplib.SMTP_SSL(host, port, timeout=10, context=tls_context)
		else:
			connection = smtplib.SMTP(host, port, timeout=10)
		with connection as smtp_connection:
			if security == "starttls":
				smtp_connection.starttls(context=tls_context)
			if username:
				smtp_connection.login(username, password)
			smtp_connection.send_message(message)
	except (OSError, smtplib.SMTPException) as exc:
		raise EmailDeliveryError(f"Could not send email via SMTP: {exc}") from exc


_SMTP_SECURITY_MODES = ("starttls", "ssl", "none")


def resolve_smtp_security(source: Mapping[str, str], port: int) -> str:
	"""Pick how the SMTP connection is encrypted.

	``SMTP_SECURITY`` wins when set. Otherwise port 465 means implicit TLS and
	anything else means STARTTLS, which matches how every major provider
	documents its ports.

	The legacy ``SMTP_USE_TLS=false`` is still honoured as "none", so an
	existing configuration keeps behaving the same.
	"""

	explicit = (source.get("SMTP_SECURITY") or "").strip().casefold()
	if explicit:
		if explicit not in _SMTP_SECURITY_MODES:
			raise EmailDeliveryError(
				f"SMTP_SECURITY={explicit!r} is not one of {', '.join(_SMTP_SECURITY_MODES)}."
			)
		return explicit
	if (source.get("SMTP_USE_TLS") or "").strip().casefold() in {"0", "false", "no"}:
		return "none"
	return "ssl" if port == 465 else "starttls"


def validate_email_configuration(env: Mapping[str, str] | None = None) -> list[str]:
	"""Return the configuration problems, logging each as a warning.

	This exists because every one of these fails **silently** at request time.
	The forgot-password route deliberately returns the same response whether or
	not delivery worked — anything else would reveal which addresses have
	accounts — so a misconfigured provider produces no error anywhere: users
	request a reset, are told to check their inbox, and nothing arrives.
	Startup is the only point at which the problem can be made visible.
	"""

	source = env if env is not None else os.environ
	provider = (source.get("EMAIL_PROVIDER") or "console").strip().casefold()
	app_env = (source.get("APP_ENV") or "development").strip().casefold()
	problems: list[str] = []

	if provider not in {"console", "smtp"}:
		problems.append(
			f"EMAIL_PROVIDER={provider!r} is not recognised; password-reset mail "
			"will fail to send. Expected 'console' or 'smtp'."
		)
	elif provider == "console" and app_env == "production":
		problems.append(
			"EMAIL_PROVIDER is 'console' with APP_ENV=production: password-reset "
			"mail is written to the log instead of being sent, so no user can "
			"actually reset a password. Set EMAIL_PROVIDER=smtp and SMTP_*."
		)
	elif provider == "smtp":
		if not (source.get("SMTP_HOST") or "").strip():
			problems.append("EMAIL_PROVIDER=smtp but SMTP_HOST is not set.")
		port_raw = (source.get("SMTP_PORT") or "587").strip()
		try:
			port = int(port_raw)
			security = resolve_smtp_security(source, port)
			if security == "none" and app_env == "production":
				problems.append(
					"SMTP is configured without encryption in production; reset "
					"links would cross the network in plain text."
				)
		except ValueError:
			problems.append(f"SMTP_PORT={port_raw!r} is not a valid port number.")
		except EmailDeliveryError as exc:
			problems.append(str(exc))
		if (source.get("SMTP_USERNAME") or "").strip() and not (
			source.get("SMTP_PASSWORD") or ""
		).strip():
			problems.append("SMTP_USERNAME is set but SMTP_PASSWORD is empty.")

	for problem in problems:
		_LOGGER.warning("Email configuration: %s", problem)
	return problems


__all__ = [
	"EmailDeliveryError",
	"resolve_smtp_security",
	"send_email",
	"validate_email_configuration",
]
