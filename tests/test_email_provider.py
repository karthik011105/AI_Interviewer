"""SMTP delivery for password-reset mail.

Three defects drove these tests.

**TLS was decorative.** ``starttls()`` and ``SMTP_SSL`` called with no context
use ``ssl._create_stdlib_context()``, which verifies neither the certificate nor
the hostname. Demonstrated against a local SMTP server presenting a self-signed
certificate: the old code delivered the reset token to it. The message is a
password-reset link, so that is a credential handed to anyone on-path.

**Port 465 did not work.** Implicit TLS needs ``SMTP_SSL``; the code always
opened a plaintext ``SMTP`` connection and issued STARTTLS, which on a port-465
server hangs until the timeout.

**Misconfiguration was silent.** The forgot-password route returns the same
response whether or not delivery worked — anything else reveals which addresses
have accounts — so a wrong or missing provider produced no error anywhere.
"""

from __future__ import annotations

import ssl
from unittest import TestCase
from unittest.mock import MagicMock, patch

from backend import email_provider
from backend.email_provider import (
    EmailDeliveryError,
    resolve_smtp_security,
    send_email,
    validate_email_configuration,
)

_BASE = {
    "EMAIL_PROVIDER": "smtp",
    "SMTP_HOST": "smtp.example.com",
    "SMTP_FROM_ADDRESS": "noreply@example.com",
}


def _send(**overrides: str) -> None:
    send_email(to="user@example.com", subject="Reset", body="link", env={**_BASE, **overrides})


class TlsVerificationTests(TestCase):
    def test_starttls_is_given_a_verifying_context(self) -> None:
        connection = MagicMock()
        with patch.object(email_provider.smtplib, "SMTP") as smtp:
            smtp.return_value.__enter__.return_value = connection
            _send(SMTP_PORT="587")

        context = connection.starttls.call_args.kwargs.get("context")
        self.assertIsNotNone(context, "starttls() must not fall back to smtplib's unverified default")
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_implicit_tls_is_given_a_verifying_context(self) -> None:
        with patch.object(email_provider.smtplib, "SMTP_SSL") as smtp_ssl:
            smtp_ssl.return_value.__enter__.return_value = MagicMock()
            _send(SMTP_PORT="465")

        context = smtp_ssl.call_args.kwargs.get("context")
        self.assertIsNotNone(context)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_a_certificate_failure_surfaces_as_a_delivery_error(self) -> None:
        """The route catches EmailDeliveryError; anything else would be a 500."""

        with patch.object(
            email_provider.smtplib,
            "SMTP_SSL",
            side_effect=ssl.SSLCertVerificationError("certificate verify failed"),
        ):
            with self.assertRaises(EmailDeliveryError):
                _send(SMTP_PORT="465")


class ConnectionModeTests(TestCase):
    def test_port_465_uses_implicit_tls_and_no_starttls(self) -> None:
        connection = MagicMock()
        with patch.object(email_provider.smtplib, "SMTP_SSL") as smtp_ssl, patch.object(
            email_provider.smtplib, "SMTP"
        ) as smtp:
            smtp_ssl.return_value.__enter__.return_value = connection
            _send(SMTP_PORT="465")

        smtp_ssl.assert_called_once()
        smtp.assert_not_called()
        connection.starttls.assert_not_called()

    def test_port_587_uses_starttls(self) -> None:
        connection = MagicMock()
        with patch.object(email_provider.smtplib, "SMTP") as smtp:
            smtp.return_value.__enter__.return_value = connection
            _send(SMTP_PORT="587")

        connection.starttls.assert_called_once()

    def test_credentials_are_used_when_configured(self) -> None:
        connection = MagicMock()
        with patch.object(email_provider.smtplib, "SMTP") as smtp:
            smtp.return_value.__enter__.return_value = connection
            _send(SMTP_PORT="587", SMTP_USERNAME="apikey", SMTP_PASSWORD="secret")

        connection.login.assert_called_once_with("apikey", "secret")
        connection.send_message.assert_called_once()

    def test_security_mode_resolution(self) -> None:
        cases = [
            ({}, 587, "starttls"),
            ({}, 465, "ssl"),
            ({}, 25, "starttls"),
            ({"SMTP_SECURITY": "ssl"}, 2525, "ssl"),
            ({"SMTP_SECURITY": "NONE"}, 587, "none"),
            # Legacy flag keeps its meaning for existing configurations.
            ({"SMTP_USE_TLS": "false"}, 587, "none"),
            # An explicit mode wins over the legacy flag.
            ({"SMTP_USE_TLS": "false", "SMTP_SECURITY": "starttls"}, 587, "starttls"),
        ]
        for env, port, expected in cases:
            with self.subTest(env=env, port=port):
                self.assertEqual(resolve_smtp_security(env, port), expected)

    def test_an_unknown_security_mode_is_refused(self) -> None:
        with self.assertRaises(EmailDeliveryError):
            resolve_smtp_security({"SMTP_SECURITY": "tls13-please"}, 587)


class ConfigurationValidationTests(TestCase):
    """Startup is the only place a broken provider can be made visible."""

    def test_console_in_production_is_flagged(self) -> None:
        problems = validate_email_configuration(
            {"EMAIL_PROVIDER": "console", "APP_ENV": "production"}
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("console", problems[0])

    def test_console_in_development_is_fine(self) -> None:
        """Console is real, intended behaviour locally and in CI."""

        self.assertEqual(validate_email_configuration({"EMAIL_PROVIDER": "console"}), [])

    def test_an_unknown_provider_is_flagged(self) -> None:
        problems = validate_email_configuration({"EMAIL_PROVIDER": "sendgird"})
        self.assertTrue(any("sendgird" in p for p in problems))

    def test_smtp_without_a_host_is_flagged(self) -> None:
        problems = validate_email_configuration({"EMAIL_PROVIDER": "smtp"})
        self.assertTrue(any("SMTP_HOST" in p for p in problems))

    def test_plaintext_smtp_in_production_is_flagged(self) -> None:
        problems = validate_email_configuration(
            {**_BASE, "APP_ENV": "production", "SMTP_SECURITY": "none"}
        )
        self.assertTrue(any("plain text" in p for p in problems))

    def test_a_username_without_a_password_is_flagged(self) -> None:
        problems = validate_email_configuration({**_BASE, "SMTP_USERNAME": "apikey"})
        self.assertTrue(any("SMTP_PASSWORD" in p for p in problems))

    def test_a_bad_port_is_flagged_rather_than_raising(self) -> None:
        problems = validate_email_configuration({**_BASE, "SMTP_PORT": "abc"})
        self.assertTrue(any("SMTP_PORT" in p for p in problems))

    def test_a_complete_production_configuration_is_clean(self) -> None:
        problems = validate_email_configuration(
            {
                **_BASE,
                "APP_ENV": "production",
                "SMTP_PORT": "587",
                "SMTP_USERNAME": "apikey",
                "SMTP_PASSWORD": "secret",
            }
        )
        self.assertEqual(problems, [])

    def test_problems_are_logged_as_warnings(self) -> None:
        with self.assertLogs(email_provider._LOGGER, level="WARNING") as captured:
            validate_email_configuration({"EMAIL_PROVIDER": "smtp"})
        self.assertIn("SMTP_HOST", "\n".join(captured.output))
