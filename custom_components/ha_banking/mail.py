"""Read bank balance emails from the user's own mailbox."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
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
    ParsedNotice,
    imap_sender_criteria,
    parse_air_bank_notice,
    parse_notice,
    parse_notices,
    snapshots_from_messages,
)
from .snapshot import history_start, mail_history_start

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
        html=_html_parts(message),
    )


def open_mailbox(host: str, port: int, username: str, password: str, folders: list[str]) -> None:
    """Log in and open each folder. Raises EmailLoginError when the password is rejected.

    A missing folder is allowed while at least one other folder exists, so a user of
    one bank does not need a folder for every registered bank.
    """
    missing: MailboxFolderNotFound | None = None
    opened = False
    for folder in dict.fromkeys(folders):
        try:
            with _mailbox(host, port, username, password, folder):
                opened = True
        except MailboxFolderNotFound as err:
            missing = err
    if not opened and missing is not None:
        raise missing


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


@dataclass
class FolderState:
    """What one folder held at the last read: parsed notices by IMAP UID."""

    uidvalidity: str | None = None
    last_uid: int = 0
    notices: dict[int, list[ParsedNotice]] = field(default_factory=dict)


@dataclass
class MailboxCache:
    """Parsed notices kept in memory between reads. Nothing is written to disk.

    The folder stays the source of truth: after a restart, or when the folder's
    UIDVALIDITY changes, the whole history window is read again.
    """

    folders: dict[str, FolderState] = field(default_factory=dict)
    missing: set[str] = field(default_factory=set)
    # Set when the folders' content changed since the last save of the cache file.
    changed: bool = False

    def notices(self) -> list[ParsedNotice]:
        """Every cached notice, folder by folder in UID order."""
        found: list[ParsedNotice] = []
        for state in self.folders.values():
            for uid in sorted(state.notices):
                found.extend(state.notices[uid])
        return found


def fetch_notices(
    host: str,
    port: int,
    username: str,
    password: str,
    bank_folders: dict[str, str],
    cache: MailboxCache,
    mark_seen: bool = False,
    *,
    today: date | None = None,
    connect=None,
    parsers=BANK_PARSERS,
) -> list[ParsedNotice]:
    """Notices of the monthly history window. Only mail newer than the last read is downloaded."""
    if connect is None:
        connect = _mailbox
    if today is None:
        today = date.today()
    start = mail_history_start(today)
    grouped: dict[str, list] = {}
    for parser in parsers:
        if not parser.sender:
            continue
        folder = bank_folders.get(parser.bank_id) or parser.default_folder
        grouped.setdefault(folder, []).append(parser)
    for folder in list(cache.folders):
        if folder not in grouped:
            del cache.folders[folder]
            cache.changed = True
    for folder, folder_parsers in grouped.items():
        try:
            state = _sync_folder(
                host, port, username, password, folder, folder_parsers, mark_seen, connect, start, cache
            )
        except MailboxFolderNotFound:
            if cache.folders.pop(folder, None) is not None:
                cache.changed = True
            if folder not in cache.missing:
                cache.missing.add(folder)
                LOGGER.warning("Mailbox folder %s was not found, so that bank was skipped", folder)
            continue
        cache.missing.discard(folder)
    return cache.notices()


def _sync_folder(
    host, port, username, password, folder, parsers, mark_seen: bool, connect, start: date, cache: MailboxCache
) -> FolderState:
    with connect(host, port, username, password, folder, readonly=not mark_seen) as client:
        uidvalidity = _uidvalidity(client)
        state = cache.folders.get(folder)
        if state is None or state.uidvalidity != uidvalidity:
            state = FolderState(uidvalidity=uidvalidity)
            cache.changed = True
        senders = imap_sender_criteria(parsers)
        if state.last_uid:
            status, data = client.uid("search", None, "UID", f"{state.last_uid + 1}:*", *senders)
        else:
            status, data = client.uid("search", None, "SINCE", _imap_date(start), *senders)
        if status != "OK":
            raise OSError(f"Mailbox search failed in {folder}")
        uids = [int(uid) for uid in (data[0].split() if data and data[0] else [])]
        # "n:*" still returns the newest message when nothing is newer than n.
        downloaded: list[tuple[bytes, MailMessage]] = []
        for uid in sorted(u for u in uids if u > state.last_uid):
            fetched_status, fetched = client.uid("fetch", str(uid).encode(), "(BODY.PEEK[])")
            raw = _raw_message(fetched_status, fetched)
            if raw is None:
                continue
            message = text_from_rfc822(raw)
            downloaded.append((str(uid).encode(), message))
            state.notices[uid] = parse_notices(message, parsers)
            state.last_uid = max(state.last_uid, uid)
            cache.changed = True
        if mark_seen:
            _mark_parsed_seen(client, downloaded, parsers)
    if _drop_before(state, datetime.combine(start, datetime.min.time())):
        cache.changed = True
    cache.folders[folder] = state
    return state


def _uidvalidity(client) -> str | None:
    """UIDVALIDITY that SELECT reported. A test double without it counts as unchanged."""
    response = getattr(client, "response", None)
    if response is None:
        return None
    try:
        _, data = response("UIDVALIDITY")
    except (imaplib.IMAP4.error, TypeError, ValueError):
        return None
    value = data[0] if data else None
    if isinstance(value, bytes):
        value = value.decode("ascii", "replace")
    return str(value) if value else None


def _drop_before(state: FolderState, start: datetime) -> bool:
    """Forget notices older than the history window and mail without any notice."""
    dropped = False
    for uid in list(state.notices):
        kept = [n for n in state.notices[uid] if n.sent_at is None or n.sent_at >= start]
        if len(kept) != len(state.notices[uid]):
            dropped = True
        if kept:
            state.notices[uid] = kept
        else:
            del state.notices[uid]
    return dropped


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


def _html_parts(message: Message) -> tuple[str, ...]:
    """Raw HTML of every text/html part, attachments included. mBank puts its table in one."""
    parts: Iterator[Message] = message.walk() if message.is_multipart() else iter((message,))
    return tuple(_part_text(part) for part in parts if part.get_content_type() == "text/html")


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


def _quoted(folder: str) -> str:
    """imaplib sends arguments as they are. A Gmail label with a space must be quoted."""
    if len(folder) >= 2 and folder.startswith('"') and folder.endswith('"'):
        return folder
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


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
        status, _ = client.select(_quoted(folder), readonly=readonly)
        if status != "OK":
            raise MailboxFolderNotFound("Mailbox folder was not found")
        yield client
    finally:
        try:
            client.logout()
        except imaplib.IMAP4.error:
            pass
