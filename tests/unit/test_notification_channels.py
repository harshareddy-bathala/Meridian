"""Owner-report channels: email, Telegram and the sink, against stub servers.

Marked as a unit test by living in ``tests/unit``. The servers are the
standard library's, in this process, on the loopback interface:
- an email is plain UTF-8 text, from the configured address, with a
  ``Message-ID`` that is the report's own;
- a refused recipient is abandoned at once, a refused login is retried, a
  silent or absent server is a timeout or unreachable;
- a Telegram message is plain text with no parse mode, to the chat named;
- a chat that refuses the bot is abandoned at once, a rate limit retried, and
  no error carries the bot's token;
- the sink holds a report's exact bytes, which hash to its content hash;
- no error, from any channel, carries the recipient.

Reference: docs/DECISIONS.md D-098, D-300, D-305.
"""

from __future__ import annotations

import email
import json
import socket
import socketserver
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from email.message import Message
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from meridian.notifications.channels import (
    DeliveryError,
    EmailChannel,
    Outgoing,
    SinkChannel,
    TelegramChannel,
)
from meridian.notifications.config import EmailSettings, TelegramSettings
from meridian.notifications.templates import Rendered, content_sha256

OWNER = "owner-private@example.org"
TOKEN = "123456:bot-token-that-must-never-be-printed"
REPORT = Outgoing(
    delivery_id=42,
    rendered=Rendered(
        subject="Meridian: METEOR-M2 3 over Station 001 — decoded",
        body=(
            "What was received: The pass was received and decoded.\nNo ‘markup’ *here*."
        ),
    ),
)


@dataclass
class _Mailbox:
    mode: str = "accept"
    received: list[bytes] = field(default_factory=list)
    envelope: list[str] = field(default_factory=list)


class _Smtp(socketserver.StreamRequestHandler):
    """Just enough SMTP for :mod:`smtplib`: EHLO, AUTH, MAIL, RCPT, DATA, QUIT."""

    def handle(self) -> None:
        box: _Mailbox = self.server.box  # type: ignore[attr-defined]
        if box.mode == "silent":
            self.rfile.readline()
            return
        self._say("220 stub ESMTP")
        while line := self.rfile.readline():
            command = line.decode().strip()
            verb = command.split(" ", 1)[0].upper()
            if verb == "EHLO":
                self._say("250-stub", "250 AUTH PLAIN LOGIN")
            elif verb == "AUTH":
                self._say("535 5.7.8 rejected" if box.mode == "bad-login" else "235 ok")
            elif verb == "RCPT" and box.mode == "refuse":
                self._say(f"550 5.1.1 <{OWNER}> no such user")
            elif verb == "DATA":
                self._say("354 go ahead")
                self._data(box)
            elif verb == "QUIT":
                self._say("221 bye")
                return
            else:
                box.envelope.append(command)
                self._say("250 ok")

    def _data(self, box: _Mailbox) -> None:
        lines = []
        while (line := self.rfile.readline()) not in (b".\r\n", b""):
            lines.append(line)
        box.received.append(b"".join(lines))
        self._say("250 queued")

    def _say(self, *lines: str) -> None:
        self.wfile.write("".join(f"{one}\r\n" for one in lines).encode())


@pytest.fixture
def smtp() -> Iterator[tuple[_Mailbox, int]]:
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Smtp)
    server.box = _Mailbox()  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.box, server.server_address[1]  # type: ignore[attr-defined]
    server.shutdown()
    server.server_close()


def channel_at(port: int, *, timeout_s: int = 5) -> EmailChannel:
    settings = EmailSettings(
        host="127.0.0.1",
        port=port,
        username="login@example.org",
        from_address="hello@meridian.example",
        starttls=False,
    )
    return EmailChannel(settings, "smtp-password", timeout_s)


def test_an_email_is_plain_utf8_text_with_the_reports_own_id(
    smtp: tuple[_Mailbox, int],
) -> None:
    box, port = smtp

    channel_at(port).send(REPORT, OWNER)

    [raw] = box.received
    message: Message = email.message_from_bytes(raw)
    assert message.get_content_type() == "text/plain"
    assert message.get_content_charset() == "utf-8"
    assert message["Message-ID"] == "<report-42@meridian.example>"
    assert message["From"] == "hello@meridian.example"
    assert message["To"] == OWNER
    payload = message.get_payload(decode=True)
    assert isinstance(payload, bytes)
    assert "No ‘markup’ *here*." in payload.decode("utf-8")
    assert any(
        OWNER in line for line in box.envelope if line.upper().startswith("RCPT")
    )


def test_a_test_message_gets_a_fresh_message_id(smtp: tuple[_Mailbox, int]) -> None:
    box, port = smtp
    test = Outgoing(0, REPORT.rendered)

    channel_at(port).send(test, OWNER)
    channel_at(port).send(test, OWNER)

    ids = {email.message_from_bytes(raw)["Message-ID"] for raw in box.received}
    assert len(ids) == 2


@pytest.mark.parametrize(
    ("mode", "error_class", "permanent"),
    [("refuse", "refused", True), ("bad-login", "smtp_auth", False)],
)
def test_a_refusal_is_a_class_and_never_the_servers_words(
    smtp: tuple[_Mailbox, int], mode: str, error_class: str, permanent: bool
) -> None:
    box, port = smtp
    box.mode = mode

    with pytest.raises(DeliveryError) as raised:
        channel_at(port).send(REPORT, OWNER)

    assert (raised.value.error_class, raised.value.permanent) == (
        error_class,
        permanent,
    )
    assert OWNER not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


def test_a_server_that_never_answers_is_a_timeout(smtp: tuple[_Mailbox, int]) -> None:
    box, port = smtp
    box.mode = "silent"

    with pytest.raises(DeliveryError, match="timeout"):
        channel_at(port, timeout_s=1).send(REPORT, OWNER)


def test_no_server_at_all_is_unreachable() -> None:
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        port = held.getsockname()[1]

    with pytest.raises(DeliveryError, match="unreachable"):
        channel_at(port).send(REPORT, OWNER)


@dataclass
class _Bot:
    status: int = 200
    requests: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


@pytest.fixture
def telegram() -> Iterator[tuple[_Bot, str]]:
    bot = _Bot()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            bot.requests.append((self.path, json.loads(self.rfile.read(length))))
            self.send_response(bot.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *_: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield bot, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_a_telegram_message_is_plain_text_to_the_chat_named(
    telegram: tuple[_Bot, str],
) -> None:
    bot, base = telegram

    TelegramChannel(TelegramSettings(api_base=base), TOKEN, 5).send(REPORT, "987654")

    [(path, body)] = bot.requests
    assert path == f"/bot{TOKEN}/sendMessage"
    assert body["chat_id"] == "987654"
    assert body["text"] == f"{REPORT.rendered.subject}\n\n{REPORT.rendered.body}"
    assert "parse_mode" not in body


@pytest.mark.parametrize(
    ("status", "error_class", "permanent"),
    [
        (403, "http_403", True),
        (400, "http_400", True),
        (429, "rate_limited", False),
        (502, "http_5xx", False),
        (404, "http_4xx", False),
    ],
)
def test_a_telegram_refusal_is_a_class_without_the_token(
    telegram: tuple[_Bot, str], status: int, error_class: str, permanent: bool
) -> None:
    bot, base = telegram
    bot.status = status

    with pytest.raises(DeliveryError) as raised:
        TelegramChannel(TelegramSettings(api_base=base), TOKEN, 5).send(
            REPORT, "987654"
        )

    assert (raised.value.error_class, raised.value.permanent) == (
        error_class,
        permanent,
    )
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    assert TOKEN not in repr(raised.value)


def test_the_sink_holds_a_reports_exact_bytes(tmp_path: Path) -> None:
    sink = SinkChannel(tmp_path / "sink")

    sink.send(REPORT, "")

    written = sink.path_of(42).read_bytes()
    assert written == REPORT.rendered.encoded()
    import hashlib

    assert hashlib.sha256(written).digest() == content_sha256(REPORT.rendered)
    assert [one.name for one in (tmp_path / "sink").iterdir()] == ["42.txt"]


def test_a_sink_that_cannot_be_written_is_a_class(tmp_path: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("not a directory", encoding="utf-8")

    with pytest.raises(DeliveryError, match="sink_unwritable"):
        SinkChannel(blocked).send(REPORT, "")
