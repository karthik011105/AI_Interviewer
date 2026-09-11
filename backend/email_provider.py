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
	use_tls = (source.get("SMTP_USE_TLS") or "true").strip().casefold() not in {"0", "false", "no"}

	message = EmailMessage()
	message["Subject"] = subject
	message["From"] = from_address
	message["To"] = to
	message.set_content(body)

	try:
		with smtplib.SMTP(host, port, timeout=10) as smtp_connection:
			if use_tls:
				smtp_connection.starttls()
			if username:
				smtp_connection.login(username, password)
			smtp_connection.send_message(message)
	except (OSError, smtplib.SMTPException) as exc:
		raise EmailDeliveryError(f"Could not send email via SMTP: {exc}") from exc


__all__ = ["EmailDeliveryError", "send_email"]
