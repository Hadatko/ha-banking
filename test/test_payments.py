from datetime import date, datetime, timedelta

from custom_components.ha_bank_watch.mail import MailMessage, snapshots_from_messages
from custom_components.ha_bank_watch.payments import (
    announces_payment,
    low_balance_notification,
    payment_notification,
    seed_low_balance,
    seed_payments,
    take_low_balances,
    take_new_payments,
)
from custom_components.ha_bank_watch.snapshot import PAYMENT_LIST_LIMIT


def _message(amount: int, sent_at: datetime) -> MailMessage:
    return MailMessage(
        sender="Air Bank <info@airbank.cz>",
        subject="Zvýšení zůstatku",
        body=f"cislo 222/3030 se zvysil o castku {amount},00 CZK.",
        sent_at=sent_at,
    )


def test_payment_list_keeps_the_newest_thirty_without_dropping_sums() -> None:
    start = datetime(2026, 1, 2, 8, 0)
    messages = [_message(index + 1, start + timedelta(minutes=index)) for index in range(35)]
    snapshot = snapshots_from_messages(messages, today=date(2026, 11, 21))[0]
    assert len(snapshot.payments) == PAYMENT_LIST_LIMIT
    assert snapshot.payments[0].amount == 35
    assert snapshot.payments[-1].amount == 6
    assert snapshot.year_incoming == 630
    assert snapshot.readings()["recent_payments"].value == PAYMENT_LIST_LIMIT
    assert snapshot.readings()["recent_payments"].attributes["payments"][0]["direction"] == "incoming"


def test_only_payments_after_startup_are_new() -> None:
    early = snapshots_from_messages([_message(5, datetime(2026, 11, 1, 9, 0))], today=date(2026, 11, 21))
    known: set[str] = set()
    seed_payments(known, early)
    assert take_new_payments(known, early) == []
    later = snapshots_from_messages(
        [_message(5, datetime(2026, 11, 1, 9, 0)), _message(8, datetime(2026, 11, 2, 9, 0))],
        today=date(2026, 11, 21),
    )
    fresh = take_new_payments(known, later)
    assert len(fresh) == 1
    assert fresh[0][2].amount == 8
    assert take_new_payments(known, later) == []


def test_payment_notification_follows_the_home_assistant_language() -> None:
    snapshot = snapshots_from_messages([_message(12, datetime(2026, 11, 2, 9, 0))], today=date(2026, 11, 21))
    payment = snapshot[0].payments[0]
    title, message = payment_notification("sk", snapshot[0].name, payment)
    assert title == "Nová platba"
    assert message.startswith(f"{snapshot[0].name}: Príchod 12.00 CZK")
    english_title, _english = payment_notification("en", snapshot[0].name, payment)
    assert english_title == "New bank payment"


def test_a_minimum_amount_hides_smaller_payments() -> None:
    assert announces_payment(10, None) is True
    assert announces_payment(10, 50) is False
    assert announces_payment(50, 50) is True


def test_low_balance_alerts_once_until_the_balance_recovers() -> None:
    low = _balance("40,00")
    high = _balance("150,00")
    alerted: set[str] = set()
    seed_low_balance(alerted, low, 100)
    assert take_low_balances(alerted, low, 100) == []
    fresh = take_low_balances(set(), low, 100)
    assert [item.account_id for item in fresh] == [low[0].account_id]
    assert take_low_balances(alerted, low, 100) == []
    assert take_low_balances(alerted, high, 100) == []
    assert [item.name for item in take_low_balances(alerted, low, 100)] == [low[0].name]
    title, message = low_balance_notification("sk", low[0].name, low[0].available_balance, "CZK")
    assert title == "Nízky dostupný zostatok"
    assert message == f"{low[0].name}: dostupný zostatok je 40.00 CZK"


def _balance(available: str):
    return snapshots_from_messages(
        [
            MailMessage(
                sender="Air Bank <info@airbank.cz>",
                subject="Zníženie zostatku",
                body=(
                    "cislo 222/3030 se snizil o castku 1,00 CZK. "
                    f"Dostupny zustatek k 1.1.2026 v 1:00 je {available} CZK."
                ),
                sent_at=datetime(2026, 11, 21, 12, 0),
            )
        ],
        today=date(2026, 11, 21),
    )
