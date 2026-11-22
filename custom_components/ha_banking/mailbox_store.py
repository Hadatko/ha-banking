"""Keep the parsed mailbox on disk so a restart needs no full IMAP read.

The file is only a cache. The mailbox folder stays the source of truth: when the file is
missing, unreadable, made for another mailbox, or written by other parser code, it is
ignored and the whole history window is read from the mailbox again. Raw mail is never
stored, only the parsed notices and the IMAP position of each folder.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, Direction
from .mail import FolderState, MailboxCache
from .notices import ParsedNotice

LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
SAVE_DELAY = 30
# Code that decides what a notice contains. A change in any of them makes the cache stale.
PARSER_FILES = ("mail.py", "mbank.py", "notices.py")


def parser_fingerprint() -> str:
    """Hash of the parser source files. Reads files, so run it outside the event loop."""
    digest = hashlib.sha256()
    folder = Path(__file__).parent
    for name in PARSER_FILES:
        digest.update((folder / name).read_bytes())
    return digest.hexdigest()


def mailbox_key(host: str, username: str) -> str:
    return f"{host.strip().lower()}|{username.strip().lower()}"


def cache_to_dict(cache: MailboxCache, key: str, fingerprint: str) -> dict[str, Any]:
    return {
        "mailbox": key,
        "parser": fingerprint,
        "folders": {
            folder: {
                "uidvalidity": state.uidvalidity,
                "last_uid": state.last_uid,
                "notices": {str(uid): [_notice_to_dict(n) for n in notices] for uid, notices in state.notices.items()},
            }
            for folder, state in cache.folders.items()
        },
    }


def cache_from_dict(data: Any, key: str, fingerprint: str) -> MailboxCache | None:
    """The cache in the file, or None when it does not fit this mailbox and code."""
    if not isinstance(data, dict) or data.get("mailbox") != key or data.get("parser") != fingerprint:
        return None
    cache = MailboxCache()
    try:
        for folder, raw in (data.get("folders") or {}).items():
            cache.folders[folder] = FolderState(
                uidvalidity=raw.get("uidvalidity"),
                last_uid=int(raw.get("last_uid") or 0),
                notices={
                    int(uid): [_notice_from_dict(n) for n in notices]
                    for uid, notices in (raw.get("notices") or {}).items()
                },
            )
    except (AttributeError, KeyError, TypeError, ValueError) as err:
        LOGGER.warning("Ignoring the stored mailbox cache: %s", err)
        return None
    return cache


def _notice_to_dict(notice: ParsedNotice) -> dict[str, Any]:
    return {
        "account_number": notice.account_number,
        "account_id": notice.account_id,
        "name": notice.name,
        "institution": notice.institution,
        "amount": notice.amount,
        "currency": notice.currency,
        "direction": str(notice.direction),
        "variable_symbol": notice.variable_symbol,
        "sent_at": notice.sent_at.isoformat() if notice.sent_at else None,
        "subject": notice.subject,
        "configuration_url": notice.configuration_url,
        "available_balance": notice.available_balance,
        "country": notice.country,
        "counterparty": notice.counterparty,
        "reference_id": notice.reference_id,
    }


def _notice_from_dict(raw: dict[str, Any]) -> ParsedNotice:
    return ParsedNotice(
        account_number=raw["account_number"],
        account_id=raw["account_id"],
        name=raw["name"],
        institution=raw["institution"],
        amount=float(raw["amount"]),
        currency=raw["currency"],
        direction=Direction(raw["direction"]),
        variable_symbol=raw.get("variable_symbol"),
        sent_at=datetime.fromisoformat(raw["sent_at"]) if raw.get("sent_at") else None,
        subject=raw.get("subject") or "",
        configuration_url=raw.get("configuration_url"),
        available_balance=raw.get("available_balance"),
        country=raw.get("country") or "CZ",
        counterparty=raw.get("counterparty"),
        reference_id=raw.get("reference_id"),
    )


class MailboxStore:
    """The cache file of one config entry, readable only by Home Assistant."""

    def __init__(self, hass: HomeAssistant, entry_id: str, key: str) -> None:
        self._hass = hass
        self._key = key
        self._fingerprint: str | None = None
        self._store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, f"{DOMAIN}.{entry_id}.mailbox", private=True
        )

    async def async_load(self) -> MailboxCache | None:
        self._fingerprint = await self._hass.async_add_executor_job(parser_fingerprint)
        try:
            data = await self._store.async_load()
        except Exception as err:  # corrupt file: read the mailbox again
            LOGGER.warning("Could not read the stored mailbox cache: %s", err)
            return None
        return cache_from_dict(data, self._key, self._fingerprint)

    def schedule_save(self, cache: MailboxCache) -> None:
        if self._fingerprint is None:
            return
        data = cache_to_dict(cache, self._key, self._fingerprint)
        self._store.async_delay_save(lambda: data, SAVE_DELAY)

    async def async_remove(self) -> None:
        await self._store.async_remove()
