"""Read-only Fio API Bankovnictví.

The account holder creates a watch-only token in Fio internet banking. One token
belongs to one account. This module downloads movements and the closing balance.
It does not submit payment orders.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo
import json

import urllib3

from .const import BalanceType, CreditDebit
from .lib.http import HttpResponse
from .snapshot import build_account_snapshot

FIO_API = "https://fioapi.fio.cz/v1/rest"
INSTITUTION = "Fio banka"
CONFIGURATION_URL = "https://ib.fio.cz/"
PRAGUE = ZoneInfo("Europe/Prague")
USER_AGENT = "HomeAssistant ha_banking"


class FioError(Exception):
    """Fio rejected a movements request. The message never includes the token."""

    def __init__(self, status: int | None = None) -> None:
        super().__init__("Fio rejected the request")
        self.status = status


class Transport:
    """HTTPS call used by tests and by the live client."""

    def request(
        self, method: str, url: str, headers: dict | None = None, body: bytes | None = None
    ) -> HttpResponse: ...


class Fio:
    """One Fio token. The token is sent only as the path segment Fio requires."""

    def __init__(self, transport: Transport | None = None) -> None:
        self._transport = transport or Urllib3Transport()

    def statement(self, token: str, today: date) -> dict:
        """Movements from the start of the readable window through today."""
        start = read_start(today)
        url = (
            f"{FIO_API}/periods/{quote(token.strip(), safe='')}"
            f"/{start.isoformat()}/{today.isoformat()}/transactions.json"
        )
        response = self._transport.request(
            "GET",
            url,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        return _json(response)


class Urllib3Transport:
    """Plain HTTPS. Fio does not use a client certificate for this read."""

    def __init__(self) -> None:
        self._http = urllib3.PoolManager(timeout=urllib3.Timeout(connect=10, read=30))

    def request(
        self, method: str, url: str, headers: dict | None = None, body: bytes | None = None
    ) -> HttpResponse:
        response = self._http.request(method, url, body=body, headers=headers or {})
        return HttpResponse(response.status, response.data, dict(response.headers))


def read_start(today: date) -> date:
    """First day Fio returns without the extra history unlock in internet banking."""
    return max(date(today.year, 1, 1), today - timedelta(days=89))


def fio_unique_id(account_id: str) -> str:
    """Stable id for one Fio account. The token is not part of it."""
    return f"fio:{account_id}"


def collect_fio_snapshots(bank: Fio, token: str, today: date | None = None) -> list:
    """Load the one account behind this token into the shared sensor view."""
    today = today or date.today()
    payload = bank.statement(token, today)
    info = (payload.get("accountStatement") or {}).get("info") or {}
    account_number = str(info.get("accountId") or "").strip()
    bank_code = str(info.get("bankId") or "").strip()
    if not account_number or not bank_code:
        raise FioError()
    account_id = f"{account_number}/{bank_code}"
    currency = str(info.get("currency") or "CZK")
    transactions = _transactions(payload, currency)
    return [
        build_account_snapshot(
            {
                "id": account_id,
                "currency": currency,
                "nameI18N": f"Fio banka {account_number}",
                "identification": {"iban": info.get("iban") or "", "other": account_id},
            },
            [_balance(info.get("closingBalance"), currency)],
            transactions,
            today,
            institution=INSTITUTION,
            country="CZ",
            configuration_url=CONFIGURATION_URL,
        )
    ]


def _transactions(payload: dict, currency: str) -> list[dict]:
    raw = ((payload.get("accountStatement") or {}).get("transactionList") or {}).get("transaction")
    if raw is None:
        rows: list = []
    elif isinstance(raw, dict):
        rows = [raw]
    elif isinstance(raw, list):
        rows = [row for row in raw if isinstance(row, dict)]
    else:
        rows = []
    converted = []
    for row in rows:
        transaction = _transaction(row, currency)
        if transaction is not None:
            converted.append(transaction)
    converted.sort(key=lambda item: (item["bookingDate"]["date"], item["entryReference"]), reverse=True)
    return converted


def _transaction(row: dict, currency: str) -> dict | None:
    amount = _amount(_column(row, 1))
    if amount is None or amount == 0:
        return None
    when = _fio_day(_column(row, 0))
    if when is None:
        return None
    movement = _column(row, 22)
    counterparty = _text(_column(row, 10))
    account = _text(_column(row, 2))
    bank = _text(_column(row, 3))
    if counterparty is None and account and bank:
        counterparty = f"{account}/{bank}"
    message = _text(_column(row, 16)) or _text(_column(row, 25)) or _text(_column(row, 7))
    variable = _text(_column(row, 5))
    party_key = "debtor" if amount > 0 else "creditor"
    remittance: dict = {}
    if message:
        remittance["unstructured"] = message
    if variable:
        remittance["structured"] = {"creditorReferenceInformation": {"reference": variable}}
    return {
        "creditDebitIndicator": CreditDebit.CREDIT if amount > 0 else CreditDebit.DEBIT,
        "amount": {"value": abs(amount), "currency": _text(_column(row, 14)) or currency},
        "status": "BOOK",
        "bookingDate": {"date": when.isoformat()},
        "entryReference": "" if movement is None else str(movement),
        "entryDetails": {
            "transactionDetails": {
                "relatedParties": {party_key: {"name": counterparty}} if counterparty else {},
                "remittanceInformation": remittance,
            }
        },
    }


def _balance(closing: object, currency: str) -> dict:
    amount = _amount(closing)
    if amount is None:
        amount = 0.0
    balance = {
        "type": {"codeOrProprietary": {"code": BalanceType.PRCD}},
        "amount": {"value": abs(amount), "currency": currency},
    }
    if amount < 0:
        balance["creditDebitIndicator"] = CreditDebit.DEBIT
    return balance


def _column(row: dict, number: int) -> object:
    cell = row.get(f"column{number}")
    if not isinstance(cell, dict):
        return None
    return cell.get("value")


def _amount(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(" ", "").replace(",", ".")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _fio_day(value: object) -> date | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000 if abs(value) > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, PRAGUE).date()
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _json(response: HttpResponse) -> dict:
    if response.status < 200 or response.status >= 300:
        raise FioError(response.status)
    try:
        payload = json.loads(response.body.decode() or "{}")
    except json.JSONDecodeError as err:
        raise FioError(response.status) from err
    if not isinstance(payload, dict):
        raise FioError(response.status)
    return payload
