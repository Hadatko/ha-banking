"""Read bank balance emails from the user's own mailbox."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from email import message_from_bytes, policy
from email.message import Message
from email.utils import parsedate_to_datetime
import imaplib
import logging
import re
from html import unescape

from .notices import (
    BANK_PARSERS,
    MailMessage,
    imap_sender_criteria,
    parse_air_bank_notice,
    parse_notice,
    snapshots_from_messages,
)
from .snapshot import history_start

LOGGER = logging.getLogger(__name__)


class MailboxFolderNotFound(OSError):
    """The mailbox has no folder with this name."""

_IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

__all__ = [
    "MailMessage",
    "fetch_air_bank_messages",
    "imap_sender_criteria",
    "parse_air_bank_notice",
    "snapshots_from_messages",
    "text_from_rfc822",
]


def _imap_date(value: date) -> str:
    """IMAP dates use an English month abbreviation."""
    return f"{value.day:02d}-{_IMAP_MONTHS[value.month - 1]}-{value.year}"


class EmailLoginError(Exception):
    """The mailbox rejected the username or password."""


def normalize_mailbox_password(password: str) -> str:
    """Drop the spaces Google inserts into a 16-letter app password."""
    text = str(password).strip()
    compact = text.replace(" ", "")
    if len(compact) == 16 and compact.isalpha():
        return compact
    return text


def imap_error_text(err: BaseException) -> str:
    """Return the mailbox server reply without any stored secret."""
    parts: list[str] = []
    for arg in err.args or (str(err),):
        if isinstance(arg, bytes):
            parts.append(arg.decode("utf-8", "replace"))
        else:
            parts.append(str(arg))
    return " ".join(part.strip() for part in parts if part.strip())[:300]


def text_from_rfc822(raw: bytes) -> MailMessage:
    """Read the plain text of one RFC 822 message."""
    message = message_from_bytes(raw, policy=policy.default)
    sent_at = None
    if message.get("Date"):
        sent_at = parsedate_to_datetime(str(message["Date"])).replace(tzinfo=None)
    return MailMessage(
        sender=str(message.get("From") or ""),
        subject=str(message.get("Subject") or ""),
        body=_body_text(message),
        sent_at=sent_at,
    )


def open_mailbox(host: str, port: int, username: str, password: str, folders: list[str]) -> None:
    """Log in and open each folder. Raises EmailLoginError when the password is rejected."""
    for folder in dict.fromkeys(folders):
        with _mailbox(host, port, username, password, folder):
            pass


def fetch_air_bank_messages(
    host: str,
    port: int,
    username: str,
    password: str,
    bank_folders: dict[str, str],
    mark_seen: bool = False,
    *,
    today: date | None = None,
    connect=None,
    parsers=BANK_PARSERS,
) -> list[MailMessage]:
    """Download recent notices. Each bank is read from its own folder."""
    if connect is None:
        connect = _mailbox
    if today is None:
        today = date.today()
    since = _imap_date(history_start(today))
    grouped: dict[str, list] = {}
    for parser in parsers:
        if not parser.sender:
            continue
        folder = bank_folders.get(parser.bank_id) or parser.default_folder
        grouped.setdefault(folder, []).append(parser)
    messages: list[MailMessage] = []
    for folder, folder_parsers in grouped.items():
        try:
            messages.extend(
                _fetch_folder(
                    host,
                    port,
                    username,
                    password,
                    folder,
                    folder_parsers,
                    mark_seen,
                    connect,
                    since,
                )
            )
        except MailboxFolderNotFound:
            LOGGER.warning("Mailbox folder %s was not found, so that bank was skipped", folder)
    return messages


def _fetch_folder(host, port, username, password, folder, parsers, mark_seen: bool, connect, since: str) -> list[MailMessage]:
    with connect(host, port, username, password, folder, readonly=not mark_seen) as client:
        status, data = client.uid("search", None, "SINCE", since, *imap_sender_criteria(parsers))
        if status != "OK" or not data or not data[0]:
            return []
        downloaded: list[tuple[bytes, MailMessage]] = []
        for uid in data[0].split():
            fetched_status, fetched = client.uid("fetch", uid, "(BODY.PEEK[])")
            raw = _raw_message(fetched_status, fetched)
            if raw is not None:
                downloaded.append((uid, text_from_rfc822(raw)))
        if mark_seen:
            _mark_parsed_seen(client, downloaded, parsers)
        return [message for _, message in downloaded]


def _mark_parsed_seen(client, downloaded: list[tuple[bytes, MailMessage]], parsers) -> None:
    for uid, message in downloaded:
        if parse_notice(message, parsers) is None:
            continue
        status, _ = client.uid("store", uid, "+FLAGS", "(\\Seen)")
        if status != "OK":
            raise OSError("Could not mark the bank notice as read")


def _body_text(message: Message) -> str:
    plain: list[str] = []
    html_parts: list[str] = []
    parts: Iterator[Message] = message.walk() if message.is_multipart() else iter((message,))
    for part in parts:
        if part.get_content_disposition() == "attachment" or part.is_multipart():
            continue
        if part.get_content_type() == "text/html":
            html_parts.append(_part_text(part))
        elif part.get_content_type() == "text/plain":
            plain.append(_part_text(part))
    if any(part.strip() for part in plain):
        return "\n".join(plain)
    return "\n".join(_html_to_text(part) for part in html_parts)


def _part_text(part: Message) -> str:
    try:
        content = part.get_content()
    except (LookupError, KeyError, UnicodeError):
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", errors="replace")
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return str(content)


def _html_to_text(html: str) -> str:
    without_hidden = re.sub(r"(?is)<(script|style)\b.*?>.*?</\1>", " ", html)
    with_breaks = re.sub(r"(?i)<br\s*/?>|</p>", "\n", without_hidden)
    text = re.sub(r"<[^>]+>", " ", with_breaks)
    return unescape(text)


def _raw_message(status: str, fetched: list | None) -> bytes | None:
    if status != "OK" or not fetched:
        return None
    for item in fetched:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


@contextmanager
def _mailbox(
    host: str,
    port: int,
    username: str,
    password: str,
    folder: str,
    readonly: bool = True,
) -> Iterator[imaplib.IMAP4_SSL]:
    client = imaplib.IMAP4_SSL(host, int(port))
    try:
        try:
            client.login(username, normalize_mailbox_password(password))
        except imaplib.IMAP4.error as err:
            reason = imap_error_text(err)
            LOGGER.warning("Mailbox %s rejected the login: %s", host, reason)
            raise EmailLoginError(reason or "Mailbox rejected the username or password") from err
        status, _ = client.select(folder, readonly=readonly)
        if status != "OK":
            raise MailboxFolderNotFound("Mailbox folder was not found")
        yield client
    finally:
        try:
            client.logout()
        except imaplib.IMAP4.error:
            pass
