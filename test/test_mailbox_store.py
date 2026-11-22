"""The mailbox cache file: a restart starts from it, the mailbox stays the source of truth."""

from datetime import datetime

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_banking.const import (
    CONF_AUTH_METHOD,
    CONF_IMAP_HOST,
    CONF_IMAP_PASSWORD,
    CONF_IMAP_USERNAME,
    DOMAIN,
    AuthMethod,
    Direction,
)
from custom_components.ha_banking.coordinator import AirBankCoordinator
from custom_components.ha_banking.mail import FolderState, MailboxCache
from custom_components.ha_banking.mailbox_store import (
    cache_from_dict,
    cache_to_dict,
    mailbox_key,
    parser_fingerprint,
)
from custom_components.ha_banking.notices import ParsedNotice

KEY = mailbox_key("imap.gmail.com", "User@Example.com")


def _notice(amount: float, when: datetime) -> ParsedNotice:
    return ParsedNotice(
        account_number="12345678",
        account_id="12345678/6210",
        name="mBank 12345678",
        institution="mBank",
        amount=amount,
        currency="CZK",
        direction=Direction.OUTGOING,
        variable_symbol=None,
        sent_at=when,
        subject="Z Vaseho uc. 12345678 odeslo",
        configuration_url="https://online.mbank.cz/",
        available_balance=900.0,
        counterparty="SHOP",
        reference_id=f"mbank:12345678:{when:%Y%m%d%H%M}:0",
    )


def _cache() -> MailboxCache:
    cache = MailboxCache()
    cache.folders["mbank"] = FolderState(
        uidvalidity="51",
        last_uid=12,
        notices={10: [_notice(100.0, datetime(2026, 11, 13, 10, 0))], 12: [_notice(50.0, datetime(2026, 11, 14, 9, 0))]},
    )
    return cache


def test_round_trip_keeps_every_field() -> None:
    restored = cache_from_dict(cache_to_dict(_cache(), KEY, "abc"), KEY, "abc")
    assert restored is not None
    state = restored.folders["mbank"]
    assert (state.uidvalidity, state.last_uid) == ("51", 12)
    assert state.notices == _cache().folders["mbank"].notices
    assert restored.notices()[0].direction is Direction.OUTGOING
    assert restored.changed is False


def test_cache_of_another_mailbox_or_parser_is_ignored() -> None:
    data = cache_to_dict(_cache(), KEY, "abc")
    assert cache_from_dict(data, mailbox_key("imap.gmail.com", "other@example.com"), "abc") is None
    assert cache_from_dict(data, KEY, "changed-parser") is None
    assert cache_from_dict(None, KEY, "abc") is None


def test_broken_cache_is_ignored() -> None:
    data = cache_to_dict(_cache(), KEY, "abc")
    data["folders"]["mbank"]["notices"]["10"][0].pop("amount")
    assert cache_from_dict(data, KEY, "abc") is None


def test_the_fingerprint_follows_the_parser_code() -> None:
    assert parser_fingerprint() == parser_fingerprint()
    assert len(parser_fingerprint()) == 64


async def test_coordinator_starts_from_the_stored_cache(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_AUTH_METHOD: AuthMethod.EMAIL,
            CONF_IMAP_HOST: "imap.gmail.com",
            CONF_IMAP_USERNAME: "user@example.com",
            CONF_IMAP_PASSWORD: "secret",
        },
    )
    entry.add_to_hass(hass)
    coordinator = AirBankCoordinator(hass, entry, None)
    assert await coordinator.async_load_mailbox_cache() is None

    store = coordinator.mailbox_store
    await store._store.async_save(cache_to_dict(_cache(), KEY, store._fingerprint))
    fresh = AirBankCoordinator(hass, entry, None)
    (snapshot,) = await fresh.async_load_mailbox_cache()
    assert snapshot.account_id == "12345678/6210"
    assert snapshot.available_balance == 900.0
    assert fresh.mailbox_cache.folders["mbank"].last_uid == 12
