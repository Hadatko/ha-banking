"""Read-only account view built from AISP responses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import quote

from .const import (
    AVAILABLE_BALANCE_TYPES,
    BOOKED_BALANCE_TYPES,
    BalanceType,
    CreditDebit,
    Direction,
    SensorKey,
    direction_from_indicator,
)


@dataclass(frozen=True)
class Reading:
    """One Home Assistant sensor value."""

    value: float | str | None
    currency: str | None
    attributes: dict


@dataclass(frozen=True)
class PaymentView:
    """Latest incoming or outgoing payment."""

    amount: float
    currency: str
    counterparty: str | None
    status: str | None
    booking_date: str | None
    value_date: str | None
    message: str | None
    reference: str | None
    entry_reference: str | None
    information: str | None

    def attributes(self) -> dict:
        return {
            "counterparty": self.counterparty,
            "status": self.status,
            "booking_date": self.booking_date,
            "value_date": self.value_date,
            "message": self.message,
            "reference": self.reference,
            "entry_reference": self.entry_reference,
            "information": self.information,
        }


@dataclass(frozen=True)
class PaymentRecord:
    """One payment kept for the account list and for a new-payment alert."""

    payment_id: str
    direction: Direction
    amount: float
    currency: str
    booked: str | None
    counterparty: str | None = None
    reference: str | None = None
    message: str | None = None

    def as_dict(self) -> dict:
        data = {
            "id": self.payment_id,
            "direction": self.direction,
            "amount": self.amount,
            "currency": self.currency,
            "booked": self.booked,
        }
        if self.counterparty:
            data["counterparty"] = self.counterparty
        if self.reference:
            data["reference"] = self.reference
        if self.message:
            data["message"] = self.message
        return data


PAYMENT_LIST_LIMIT = 30


def payment_id(account_id: str, direction: str, amount: float, booked: str | None, marker: str) -> str:
    """Stable id so the same payment is not announced again on the next read."""
    return "|".join((account_id, direction, f"{amount:.2f}", booked or "", marker))


def limit_payments(records: list[PaymentRecord]) -> tuple[PaymentRecord, ...]:
    """Keep the newest payments, one row per id."""
    ordered = sorted(records, key=lambda item: (item.booked or "", item.payment_id), reverse=True)
    unique: list[PaymentRecord] = []
    seen: set[str] = set()
    for record in ordered:
        if record.payment_id in seen:
            continue
        seen.add(record.payment_id)
        unique.append(record)
        if len(unique) == PAYMENT_LIST_LIMIT:
            break
    return tuple(unique)


@dataclass(frozen=True)
class AccountSnapshot:
    """Balances and the latest payments for one account."""

    account_id: str
    name: str
    iban: str
    currency: str
    product: str | None
    booked_balance: float | None
    available_balance: float | None
    latest_incoming: PaymentView | None
    latest_outgoing: PaymentView | None
    latest_status: str | None
    institution: str = "Air Bank"
    configuration_url: str | None = "https://ib.airbank.cz/"
    month_incoming: float | None = None
    month_outgoing: float | None = None
    month_net: float | None = None
    year_incoming: float | None = None
    year_outgoing: float | None = None
    year_net: float | None = None
    logo_url: str | None = None
    payments: tuple[PaymentRecord, ...] = ()

    def readings(self) -> dict[str, Reading]:
        readings = {
            SensorKey.BOOKED_BALANCE: Reading(self.booked_balance, self.currency, {}),
            SensorKey.AVAILABLE_BALANCE: Reading(self.available_balance, self.currency, {}),
            SensorKey.INCOMING_PAYMENT: _payment_reading(self.latest_incoming, self.currency),
            SensorKey.OUTGOING_PAYMENT: _payment_reading(self.latest_outgoing, self.currency),
            SensorKey.TRANSACTION_STATUS: Reading(self.latest_status, None, {}),
            SensorKey.RECENT_PAYMENTS: Reading(
                len(self.payments),
                None,
                {"payments": [payment.as_dict() for payment in self.payments]},
            ),
        }
        if self.month_incoming is None:
            return readings
        for key in (
            SensorKey.MONTH_INCOMING,
            SensorKey.MONTH_OUTGOING,
            SensorKey.MONTH_NET,
            SensorKey.YEAR_INCOMING,
            SensorKey.YEAR_OUTGOING,
            SensorKey.YEAR_NET,
        ):
            readings[key] = Reading(getattr(self, key), self.currency, {})
        return readings


def bank_logo_url(name: str, country: str) -> str | None:
    """Public Enable Banking logo for one bank, sized for a sensor icon."""
    bank = name.strip()
    code = country.strip().upper()
    if not bank or len(code) != 2 or not code.isalpha():
        return None
    return f"https://enablebanking.com/brands/{code}/{quote(bank, safe='')}/-/resize/128x/"


def history_start(today: date) -> date:
    """First day to read: the current year, and at least the last 90 days."""
    return min(today - timedelta(days=90), date(today.year, 1, 1))


def period_totals(entries: list[tuple[date | None, Direction, float]], today: date) -> dict[str, float]:
    """Sum incoming and outgoing amounts for the current month and year."""

    def total(direction: Direction, month: int | None) -> float:
        amount = 0.0
        for when, kind, value in entries:
            if kind != direction or when is None or when.year != today.year:
                continue
            if month is not None and when.month != month:
                continue
            amount += value
        return round(amount, 2)

    month_incoming = total(Direction.INCOMING, today.month)
    month_outgoing = total(Direction.OUTGOING, today.month)
    year_incoming = total(Direction.INCOMING, None)
    year_outgoing = total(Direction.OUTGOING, None)
    return {
        SensorKey.MONTH_INCOMING: month_incoming,
        SensorKey.MONTH_OUTGOING: month_outgoing,
        SensorKey.MONTH_NET: round(month_incoming - month_outgoing, 2),
        SensorKey.YEAR_INCOMING: year_incoming,
        SensorKey.YEAR_OUTGOING: year_outgoing,
        SensorKey.YEAR_NET: round(year_incoming - year_outgoing, 2),
    }


def build_account_snapshot(
    account: dict,
    balances: list[dict],
    transactions: list[dict],
    today: date | None = None,
) -> AccountSnapshot:
    """Turn one account payload into balances and the newest payment in each direction."""
    incoming = _first_payment(transactions, CreditDebit.CREDIT)
    outgoing = _first_payment(transactions, CreditDebit.DEBIT)
    latest_status = None
    if transactions and transactions[0].get("status"):
        latest_status = transactions[0]["status"]
    identification = account.get("identification") or {}
    totals = period_totals(_api_entries(transactions), today or date.today())
    return AccountSnapshot(
        account_id=str(account["id"]),
        name=account.get("nameI18N") or identification.get("other") or str(account["id"]),
        iban=identification.get("iban") or "",
        currency=account.get("currency") or "CZK",
        product=account.get("productI18N"),
        booked_balance=_balance(balances, BalanceType.PRCD),
        available_balance=_balance(balances, BalanceType.CLAV),
        latest_incoming=incoming,
        latest_outgoing=outgoing,
        latest_status=latest_status,
        logo_url=bank_logo_url("Air Bank", "CZ"),
        payments=_api_payments(str(account["id"]), transactions),
        **totals,
    )


def collect_account_snapshots(bank, access_token: str) -> list[AccountSnapshot]:
    """Load accounts, balances, and recent transactions. Does not initiate payments."""
    snapshots = []
    for account in _all_accounts(bank, access_token):
        balances = bank.list_balances(access_token, account.id)
        transactions = _account_transactions(bank, access_token, account.id, date.today())
        snapshots.append(
            build_account_snapshot(
                account.model_dump(by_alias=True),
                [item.model_dump(by_alias=True) for item in balances],
                transactions,
            )
        )
    return snapshots


def _account_transactions(bank, access_token: str, account_id: str, today: date) -> list[dict]:
    collected: list[dict] = []
    page_number = 0
    from_date = history_start(today).isoformat()
    for _ in range(20):
        page = bank.list_transactions(
            access_token,
            account_id,
            from_date=from_date,
            page=page_number,
            size=100,
        )
        collected.extend(item.model_dump(by_alias=True) for item in page.transactions)
        if page.next_page is None or page.next_page == page_number:
            break
        page_number = page.next_page
    return collected


def _api_entries(transactions: list[dict]) -> list[tuple[date | None, str, float]]:
    entries: list[tuple[date | None, str, float]] = []
    for transaction in transactions:
        direction = direction_from_indicator(transaction.get("creditDebitIndicator"))
        if direction is None:
            continue
        amount = abs(float((transaction.get("amount") or {}).get("value") or 0))
        when = _calendar_day(transaction.get("bookingDate")) or _calendar_day(transaction.get("valueDate"))
        entries.append((when, direction, amount))
    return entries


def _calendar_day(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, dict):
        value = value.get("date") or value.get("dateTime")
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _all_accounts(bank, access_token: str) -> list:
    accounts = []
    page_number = 0
    for _ in range(20):
        page = bank.list_accounts(access_token, page=page_number, size=50)
        accounts.extend(page.accounts)
        if page.next_page is None or page.next_page == page_number:
            break
        page_number = page.next_page
    return accounts


def _payment_reading(payment: PaymentView | None, currency: str) -> Reading:
    if payment is None:
        return Reading(None, currency, {})
    return Reading(payment.amount, payment.currency or currency, payment.attributes())


def _balance(balances: list[dict], code: str) -> float | None:
    for balance in balances:
        type_code = (
            balance.get("type", {}).get("codeOrProprietary", {}).get("code")
        )
        if type_code != code:
            continue
        value = abs(float(balance["amount"]["value"]))
        if balance.get("creditDebitIndicator") == CreditDebit.DEBIT:
            return -value
        return value
    return None


def _api_payments(account_id: str, transactions: list[dict]) -> tuple[PaymentRecord, ...]:
    records: list[PaymentRecord] = []
    for transaction in transactions:
        direction = direction_from_indicator(transaction.get("creditDebitIndicator"))
        if direction is None:
            continue
        view = _payment(transaction)
        marker = view.entry_reference or view.reference or view.counterparty or view.message or ""
        records.append(
            PaymentRecord(
                payment_id=payment_id(account_id, direction, view.amount, view.booking_date, marker),
                direction=direction,
                amount=view.amount,
                currency=view.currency or "CZK",
                booked=view.booking_date,
                counterparty=view.counterparty,
                reference=view.reference,
                message=view.message,
            )
        )
    return limit_payments(records)


def _first_payment(transactions: list[dict], indicator: str) -> PaymentView | None:
    for transaction in transactions:
        if transaction.get("creditDebitIndicator") == indicator:
            return _payment(transaction)
    return None


def _payment(transaction: dict) -> PaymentView:
    amount = transaction.get("amount") or {}
    details = (transaction.get("entryDetails") or {}).get("transactionDetails") or {}
    parties = details.get("relatedParties") or {}
    indicator = transaction.get("creditDebitIndicator")
    party = parties.get("debtor") if indicator == CreditDebit.CREDIT else parties.get("creditor")
    remittance = details.get("remittanceInformation") or {}
    reference = _reference(remittance)
    return PaymentView(
        amount=abs(float(amount.get("value") or 0)),
        currency=amount.get("currency") or "",
        counterparty=(party or {}).get("name"),
        status=transaction.get("status"),
        booking_date=_date(transaction.get("bookingDate")),
        value_date=_date(transaction.get("valueDate")),
        message=remittance.get("unstructured"),
        reference=reference,
        entry_reference=transaction.get("entryReference"),
        information=details.get("additionalTransactionInformation"),
    )


def _reference(remittance: dict) -> str | None:
    structured = remittance.get("structured") or {}
    reference = (structured.get("creditorReferenceInformation") or {}).get("reference")
    if isinstance(reference, list):
        return ", ".join(str(item) for item in reference) or None
    if isinstance(reference, str) and reference:
        return reference
    return None


def _date(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    return value.get("date") or value.get("dateTime")


def build_enable_banking_snapshot(
    account: dict,
    balances: list[dict],
    transactions: list[dict],
    today: date | None = None,
    institution: str = "Air Bank",
    country: str = "CZ",
) -> AccountSnapshot:
    """Turn one Enable Banking account into the same sensor view as the direct API."""
    ordered = sorted(transactions, key=_enable_banking_sort_key, reverse=True)
    currency = account.get("currency") or "CZK"
    latest_status = ordered[0].get("status") if ordered else None
    totals = period_totals(_enable_banking_entries(transactions), today or date.today())
    return AccountSnapshot(
        account_id=str(account.get("uid") or account.get("iban") or ""),
        name=account.get("name") or account.get("iban") or str(account.get("uid") or ""),
        iban=account.get("iban") or "",
        currency=currency,
        product=account.get("product") or None,
        booked_balance=_enable_banking_balance(balances, BOOKED_BALANCE_TYPES),
        available_balance=_enable_banking_balance(balances, AVAILABLE_BALANCE_TYPES),
        latest_incoming=_first_enable_banking_payment(ordered, CreditDebit.CREDIT),
        latest_outgoing=_first_enable_banking_payment(ordered, CreditDebit.DEBIT),
        latest_status=latest_status,
        institution=institution,
        logo_url=bank_logo_url(institution, country),
        payments=_enable_banking_payments(str(account.get("uid") or account.get("iban") or ""), transactions),
        **totals,
    )


def _enable_banking_entries(transactions: list[dict]) -> list[tuple[date | None, str, float]]:
    entries: list[tuple[date | None, str, float]] = []
    for transaction in transactions:
        direction = direction_from_indicator(transaction.get("credit_debit_indicator"))
        if direction is None:
            continue
        amount = abs(float((transaction.get("transaction_amount") or {}).get("amount") or 0))
        when = _calendar_day(transaction.get("booking_date")) or _calendar_day(transaction.get("value_date"))
        entries.append((when, direction, amount))
    return entries


def _enable_banking_balance(balances: list[dict], types: tuple[str, ...]) -> float | None:
    by_type = {balance.get("balance_type"): balance for balance in balances}
    for balance_type in types:
        balance = by_type.get(balance_type)
        if balance is None:
            continue
        amount = (balance.get("balance_amount") or {}).get("amount")
        if amount is None:
            continue
        return float(amount)
    return None


def _enable_banking_payments(account_id: str, transactions: list[dict]) -> tuple[PaymentRecord, ...]:
    records: list[PaymentRecord] = []
    for transaction in transactions:
        direction = direction_from_indicator(transaction.get("credit_debit_indicator"))
        if direction is None:
            continue
        view = _enable_banking_payment(transaction)
        marker = view.entry_reference or view.reference or view.counterparty or view.message or ""
        records.append(
            PaymentRecord(
                payment_id=payment_id(account_id, direction, view.amount, view.booking_date, marker),
                direction=direction,
                amount=view.amount,
                currency=view.currency or "CZK",
                booked=view.booking_date,
                counterparty=view.counterparty,
                reference=view.reference,
                message=view.message,
            )
        )
    return limit_payments(records)


def _first_enable_banking_payment(transactions: list[dict], indicator: str) -> PaymentView | None:
    for transaction in transactions:
        if transaction.get("credit_debit_indicator") == indicator:
            return _enable_banking_payment(transaction)
    return None


def _enable_banking_payment(transaction: dict) -> PaymentView:
    amount = transaction.get("transaction_amount") or {}
    indicator = transaction.get("credit_debit_indicator")
    party = transaction.get("debtor") if indicator == CreditDebit.CREDIT else transaction.get("creditor")
    remittance = transaction.get("remittance_information") or []
    if isinstance(remittance, str):
        message = remittance
    else:
        message = ", ".join(str(item) for item in remittance if item) or None
    reference = transaction.get("reference_number") or None
    return PaymentView(
        amount=abs(float(amount.get("amount") or 0)),
        currency=amount.get("currency") or "",
        counterparty=(party or {}).get("name") if isinstance(party, dict) else None,
        status=transaction.get("status"),
        booking_date=_plain_date(transaction.get("booking_date")),
        value_date=_plain_date(transaction.get("value_date")),
        message=message,
        reference=reference,
        entry_reference=transaction.get("entry_reference"),
        information=transaction.get("note"),
    )


def _enable_banking_sort_key(transaction: dict) -> str:
    return str(
        transaction.get("booking_date")
        or transaction.get("value_date")
        or transaction.get("transaction_date")
        or ""
    )


def _plain_date(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
