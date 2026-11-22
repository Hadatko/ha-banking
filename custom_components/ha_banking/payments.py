"""New-payment detection and the text of the alert."""

from __future__ import annotations

from hashlib import sha256

from .const import DOMAIN
from .snapshot import AccountSnapshot, PaymentRecord

EVENT_PAYMENT = "ha_banking_payment"

_COPY = {
    "en": {
        "title": "New bank payment",
        "incoming": "Incoming",
        "outgoing": "Outgoing",
        "low_title": "Low available balance",
        "low": "available balance is",
    },
    "sk": {
        "title": "Nová platba",
        "incoming": "Príchod",
        "outgoing": "Odchod",
        "low_title": "Nízky dostupný zostatok",
        "low": "dostupný zostatok je",
    },
    "cs": {
        "title": "Nová platba",
        "incoming": "Příjem",
        "outgoing": "Výdaj",
        "low_title": "Nízký dostupný zůstatek",
        "low": "dostupný zůstatek je",
    },
}


def payment_ids(snapshots: list[AccountSnapshot]) -> set[str]:
    """Ids already present in the current account view."""
    return {payment.payment_id for snapshot in snapshots for payment in snapshot.payments}


def seed_payments(known: set[str], snapshots: list[AccountSnapshot]) -> None:
    """Remember payments that were already there when the integration started."""
    known.update(payment_ids(snapshots))


def take_new_payments(
    known: set[str], snapshots: list[AccountSnapshot]
) -> list[tuple[str, str, PaymentRecord]]:
    """Return payments not seen before and remember them."""
    fresh: list[tuple[str, str, PaymentRecord]] = []
    for snapshot in snapshots:
        for payment in snapshot.payments:
            if payment.payment_id in known:
                continue
            known.add(payment.payment_id)
            fresh.append((snapshot.name, snapshot.account_id, payment))
    return fresh


def optional_amount(value: object) -> float | None:
    """Read a configured limit. An empty value turns the alert off."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def announces_payment(amount: float, minimum: float | None) -> bool:
    """A blank minimum announces every new payment. A number announces that amount or more."""
    return minimum is None or amount >= minimum


def low_balance_alerted(hass) -> set[str]:
    """Accounts whose low-balance alert is already showing during this run."""
    store = hass.data.setdefault(DOMAIN, {})
    alerted = store.get("low_balance_alerted")
    if not isinstance(alerted, set):
        alerted = set()
        store["low_balance_alerted"] = alerted
    return alerted


def seed_low_balance(alerted: set[str], snapshots: list[AccountSnapshot], limit: float | None) -> None:
    """Remember accounts already under the limit so a restart stays quiet."""
    if limit is None:
        return
    for snapshot in snapshots:
        balance = snapshot.available_balance
        if balance is not None and balance < limit:
            alerted.add(snapshot.account_id)


def take_low_balances(
    alerted: set[str], snapshots: list[AccountSnapshot], limit: float | None
) -> list[AccountSnapshot]:
    """Return accounts that just fell below the limit. Rising above it arms the alert again."""
    if limit is None:
        alerted.clear()
        return []
    fresh: list[AccountSnapshot] = []
    for snapshot in snapshots:
        balance = snapshot.available_balance
        if balance is None:
            continue
        if balance < limit:
            if snapshot.account_id not in alerted:
                alerted.add(snapshot.account_id)
                fresh.append(snapshot)
        else:
            alerted.discard(snapshot.account_id)
    return fresh


def known_payments(hass) -> set[str]:
    """Payment ids already announced during this Home Assistant run."""
    store = hass.data.setdefault(DOMAIN, {})
    known = store.get("known_payments")
    if not isinstance(known, set):
        known = set()
        store["known_payments"] = known
    return known


def publish_new_payments(hass, snapshots: list[AccountSnapshot], minimum_amount: float | None = None) -> None:
    """Fire an event for every new payment. Show a notification when it meets the minimum."""
    from homeassistant.components.persistent_notification import async_create

    language = getattr(hass.config, "language", None) or "en"
    for account_name, account_id, payment in take_new_payments(known_payments(hass), snapshots):
        title, message = payment_notification(language, account_name, payment)
        hass.bus.async_fire(
            EVENT_PAYMENT,
            {
                "account_id": account_id,
                "account_name": account_name,
                "direction": payment.direction,
                "amount": payment.amount,
                "currency": payment.currency,
                "booked": payment.booked,
                "counterparty": payment.counterparty,
                "reference": payment.reference,
            },
        )
        if announces_payment(payment.amount, minimum_amount):
            async_create(
                hass,
                message,
                title,
                f"ha_banking_{sha256(payment.payment_id.encode()).hexdigest()[:16]}",
            )


def publish_low_balance(hass, snapshots: list[AccountSnapshot], limit: float | None) -> None:
    """Show one notification when an available balance falls below the limit."""
    from homeassistant.components.persistent_notification import async_create

    language = getattr(hass.config, "language", None) or "en"
    for snapshot in take_low_balances(low_balance_alerted(hass), snapshots, limit):
        title, message = low_balance_notification(language, snapshot.name, snapshot.available_balance, snapshot.currency)
        async_create(hass, message, title, f"ha_banking_low_{sha256(snapshot.account_id.encode()).hexdigest()[:16]}")


def payment_notification(language: str, account_name: str, payment: PaymentRecord) -> tuple[str, str]:
    """Title and message for one new payment, in the Home Assistant language."""
    copy = _COPY.get(language[:2].lower(), _COPY["en"])
    label = copy.get(payment.direction, payment.direction)
    message = f"{account_name}: {label} {payment.amount:.2f} {payment.currency}"
    if payment.counterparty:
        message = f"{message} — {payment.counterparty}"
    return copy["title"], message


def low_balance_notification(language: str, account_name: str, balance: float | None, currency: str) -> tuple[str, str]:
    """Title and message when the available balance is under the chosen limit."""
    copy = _COPY.get(language[:2].lower(), _COPY["en"])
    amount = 0.0 if balance is None else balance
    return copy["low_title"], f"{account_name}: {copy['low']} {amount:.2f} {currency}"
