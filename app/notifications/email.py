"""Pluggable email backend abstraction.

Zero-budget local dev: uses ConsoleBackend by default.
Production: set NOTIFICATION_BACKEND=smtp and provide SMTP env vars.
"""

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Protocol

logger = logging.getLogger(__name__)


class EmailBackend(Protocol):
    def send(self, to: str, subject: str, body: str) -> None:
        """Send an email. Raises an exception on failure."""
        ...


class ConsoleBackend:
    """Prints emails to the console. Default for local dev."""

    def send(self, to: str, subject: str, body: str) -> None:
        print("\n=== MOCK EMAIL ===", flush=True)
        print(f"To: {to}", flush=True)
        print(f"Subject: {subject}", flush=True)
        print(f"Body:\n{body}", flush=True)
        print("==================\n", flush=True)


class SMTPBackend:
    """Sends emails via SMTP using stdlib smtplib."""

    def __init__(self):
        self.host = os.environ.get("SMTP_HOST", "localhost")
        self.port = int(os.environ.get("SMTP_PORT", "25"))
        self.user = os.environ.get("SMTP_USER")
        self.password = os.environ.get("SMTP_PASSWORD")
        self.from_addr = os.environ.get("SMTP_FROM", "alerts@agency.local")

    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg.set_content(body)
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = to

        with smtplib.SMTP(self.host, self.port, timeout=10) as server:
            if self.user and self.password:
                server.starttls()
                server.login(self.user, self.password)
            server.send_message(msg)


def get_backend() -> EmailBackend:
    backend_type = os.environ.get("NOTIFICATION_BACKEND", "console").lower()
    if backend_type == "smtp":
        return SMTPBackend()
    return ConsoleBackend()


def send_email(to: str, subject: str, body: str) -> None:
    """Wrapper to send an email using the configured backend."""
    backend = get_backend()
    backend.send(to, subject, body)
