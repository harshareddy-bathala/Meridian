"""Where a report goes: email, Telegram, or the sink.

The only module in the platform that talks to a mail server or a chat service.
Each channel sends one plain-text message to one recipient, or raises
:class:`DeliveryError` with a short **error class** — ``timeout``,
``smtp_auth``, ``refused`` and the like — and never the server's own words,
which can repeat the address they refused (D-305). Nothing here logs.

* **Email** over SMTP with STARTTLS, as ``text/plain`` UTF-8. Its
  ``Message-ID`` is derived from the report's id, so a report sent twice after a
  crash is recognisably the same message.
* **Telegram** through the Bot API's ``sendMessage``, with no parse mode, so no
  character of a report is read as markup.
* **The sink** writes the report's exact bytes to ``<directory>/<id>.txt`` and
  nowhere else. Every simulated station's report goes here (rule 5), and it is
  what a rehearsal checks against the recorded hash.

Standard library only: no new dependency for a channel (D-098).

Reference: docs/DECISIONS.md D-098, D-300, D-305.
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Protocol

from meridian.notifications.config import EmailSettings, TelegramSettings
from meridian.notifications.templates import Rendered

__all__ = [
    "Channel",
    "DeliveryError",
    "EmailChannel",
    "Outgoing",
    "SinkChannel",
    "TelegramChannel",
]


class DeliveryError(Exception):
    """A report was not delivered, and why, as a class rather than words."""

    def __init__(self, error_class: str, *, permanent: bool = False) -> None:
        """Name the failure.

        Args:
            error_class: A short lower-case code, stored as the attempt's error.
            permanent: Whether trying again cannot help, so the report is
                abandoned at once rather than retried.
        """
        super().__init__(error_class)
        self.error_class = error_class
        self.permanent = permanent


@dataclass(frozen=True, slots=True)
class Outgoing:
    """One report on its way out."""

    delivery_id: int
    rendered: Rendered


class Channel(Protocol):
    """One way a report reaches a person, or the sink."""

    def send(self, outgoing: Outgoing, recipient: str) -> None:
        """Deliver ``outgoing`` to ``recipient``, or raise :class:`DeliveryError`."""
        ...


class EmailChannel:
    """Email over SMTP, authenticated, with STARTTLS."""

    def __init__(self, settings: EmailSettings, password: str, timeout_s: int) -> None:
        """Bind the channel to its server, account and per-attempt timeout."""
        self._settings = settings
        self._password = password
        self._timeout_s = timeout_s

    def send(self, outgoing: Outgoing, recipient: str) -> None:
        """Send one report as ``text/plain`` UTF-8."""
        message = self.message(outgoing, recipient)
        try:
            with smtplib.SMTP(
                self._settings.host, self._settings.port, timeout=self._timeout_s
            ) as smtp:
                if self._settings.starttls:
                    smtp.starttls(context=ssl.create_default_context())
                if self._settings.username:
                    smtp.login(self._settings.username, self._password)
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            # Never chained: a refusal's arguments hold the address it refused.
            raise _smtp_error(exc) from None

    def message(self, outgoing: Outgoing, recipient: str) -> EmailMessage[str, str]:
        """The email for one report, as it would be sent."""
        sender = self._settings.from_address
        message = EmailMessage()
        message["From"] = sender
        message["To"] = recipient
        message["Subject"] = outgoing.rendered.subject
        message["Date"] = formatdate(usegmt=True)
        domain = sender.rpartition("@")[2] or "meridian.invalid"
        # A report's own id, so a copy sent again after a crash is the same
        # message; a test message, id 0, is a new message every time.
        message["Message-ID"] = (
            f"<report-{outgoing.delivery_id}@{domain}>"
            if outgoing.delivery_id
            else make_msgid(domain=domain)
        )
        message.set_content(outgoing.rendered.body, subtype="plain", charset="utf-8")
        return message


_SMTP_ERRORS: tuple[tuple[type[BaseException], str, bool], ...] = (
    (smtplib.SMTPAuthenticationError, "smtp_auth", False),
    (smtplib.SMTPRecipientsRefused, "refused", True),
    (smtplib.SMTPSenderRefused, "sender_refused", False),
    (smtplib.SMTPNotSupportedError, "smtp_unsupported", False),
    (smtplib.SMTPException, "smtp_error", False),
    (ssl.SSLError, "tls", False),
    (TimeoutError, "timeout", False),
    (OSError, "unreachable", False),
)
"""Each failure SMTP can raise, most specific first: its class and whether a
retry can help. A refused recipient cannot be helped by trying again."""


def _smtp_error(exc: BaseException) -> DeliveryError:
    # smtplib reports a server that went quiet as a disconnection, with the
    # timeout as the exception it was handling.
    if isinstance(exc, smtplib.SMTPServerDisconnected) and isinstance(
        exc.__context__, TimeoutError
    ):
        return DeliveryError("timeout")
    for kind, error_class, permanent in _SMTP_ERRORS:
        if isinstance(exc, kind):
            return DeliveryError(error_class, permanent=permanent)
    return DeliveryError("smtp_error")


class TelegramChannel:
    """A Telegram bot's ``sendMessage``, as plain text."""

    def __init__(self, settings: TelegramSettings, token: str, timeout_s: int) -> None:
        """Bind the channel to the Bot API, the bot's token and the timeout."""
        self._url = f"{settings.api_base.rstrip('/')}/bot{token}/sendMessage"
        self._timeout_s = timeout_s

    def send(self, outgoing: Outgoing, recipient: str) -> None:
        """Send one report to the chat ``recipient`` names."""
        body = json.dumps(
            {
                "chat_id": recipient,
                "text": f"{outgoing.rendered.subject}\n\n{outgoing.rendered.body}",
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as answer:
                answer.read()
        except urllib.error.HTTPError as exc:
            raise _http_error(exc.code) from None
        except urllib.error.URLError as exc:
            timed_out = isinstance(exc.reason, TimeoutError)
            raise DeliveryError("timeout" if timed_out else "unreachable") from None
        except TimeoutError:
            raise DeliveryError("timeout") from None


RATE_LIMITED = 429
REFUSED_FOR_GOOD = frozenset({400, 403})
"""A chat that does not exist, or a bot the person blocked: no retry helps."""


def _http_error(status: int) -> DeliveryError:
    """An HTTP answer as an error class.

    The URL holds the bot's token, so the exception is never chained: a
    traceback must not print it.
    """
    if status == RATE_LIMITED:
        return DeliveryError("rate_limited")
    if status in REFUSED_FOR_GOOD:
        return DeliveryError(f"http_{status}", permanent=True)
    return DeliveryError("http_5xx" if status >= 500 else "http_4xx")  # noqa: PLR2004 — the class boundary


class SinkChannel:
    """A directory, one file per report: the test sink, never a person."""

    def __init__(self, directory: Path) -> None:
        """Bind the channel to its directory, made on first use."""
        self._directory = directory

    def path_of(self, delivery_id: int) -> Path:
        """Where report ``delivery_id`` is written."""
        return self._directory / f"{delivery_id}.txt"

    def send(self, outgoing: Outgoing, recipient: str) -> None:  # noqa: ARG002 — no one receives the sink
        """Write the report's exact bytes, atomically, whoever it was for."""
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(dir=self._directory, suffix=".part")
            with os.fdopen(handle, "wb") as written:
                written.write(outgoing.rendered.encoded())
            Path(temporary).replace(self.path_of(outgoing.delivery_id))
        except OSError as exc:
            raise DeliveryError("sink_unwritable") from exc
