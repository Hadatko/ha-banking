"""mBank CZ daily "E-mail Push" summary. Standard library only.

One mail a day covers the previous day. Each table row is an SMS-style text such as
``mBank: Z Vaseho uc. 12345678 odeslo: 84,00 CZK AV: SHOP; Dostup.zust:100,00 CZK``.
The table is in an HTML attachment; the text/plain part is only a cover note.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from html import unescape
from html.parser import HTMLParser
import re

SENDER = "kontakt@mbank.cz"
BANK_CODE = "6210"

_AMOUNT = r"-?\d[\d  .]*,\d{2}"
_CURRENCY = r"[A-Z]{3}"

_REPORT_DATE = re.compile(r"notifikace\s+ze\s+dne:\s*(\d{1,2})\.(\d{1,2})\.(\d{4})", re.IGNORECASE)
_MOVED = re.compile(rf"(?:castka:?|odeslo:|prislo:)\s*({_AMOUNT})\s*({_CURRENCY})", re.IGNORECASE)
_AVAILABLE = re.compile(rf"(?:Dostup\.zust|dispo):\s*({_AMOUNT})\s*{_CURRENCY}", re.IGNORECASE)
_OWN_FUNDS = re.compile(rf"Vlast\.prostr:\s*({_AMOUNT})\s*{_CURRENCY}", re.IGNORECASE)
_OWN_ACCOUNT = re.compile(r"(?<![.\d/])(\d{6,})(?![\d/,.])")
_PARTY_ACCOUNT = re.compile(r"(\.\.\.\d+/\d{4})")
_CARD_MERCHANT = re.compile(r"AV:\s*(.*?)\s*;\s*(?:Dostup|dispo)", re.IGNORECASE)
_INSTANT_OUT_PARTY = re.compile(r"platba\s+pro\s+(.*?),\s*na\s", re.IGNORECASE)
_INSTANT_IN_PARTY = re.compile(r"platba\s+od\s*(.*?),\s*z\s", re.IGNORECASE)
_TRANSFER_NOTE = re.compile(
    rf"castka:?\s*{_AMOUNT}\s*{_CURRENCY}\s*;\s*(?:AV:\s*)?(.*?)\s*;\s*(?:Dostup|dispo)",
    re.IGNORECASE,
)
_NOT_DONE = re.compile(r"nebyl\s+proveden", re.IGNORECASE)


@dataclass(frozen=True)
class Movement:
    """One movement from a summary row."""

    account_number: str
    occurred_at: datetime
    direction: str
    kind: str
    amount: float
    currency: str
    counterparty: str | None
    counterparty_account: str | None
    note: str | None
    available_balance: float | None
    own_funds: float | None
    row: int
    text: str

    @property
    def movement_id(self) -> str:
        """The same row of the same summary always gets the same id."""
        return f"mbank:{self.account_number}:{self.occurred_at:%Y%m%d%H%M}:{self.row}"


class _DataCells(HTMLParser):
    """Text of every <td class="data"> cell. The summary has two per row: time, text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[str] = []
        self._depth = 0
        self._buffer: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "td":
            return
        if self._depth:
            self._depth += 1
        elif "data" in (dict(attrs).get("class") or "").split():
            self._depth = 1
            self._buffer = []

    def handle_endtag(self, tag):
        if tag != "td" or not self._depth:
            return
        self._depth -= 1
        if self._depth == 0:
            self.cells.append(" ".join("".join(self._buffer).split()))

    def handle_data(self, data):
        if self._depth:
            self._buffer.append(data)


def report_date(html: str) -> date | None:
    match = _REPORT_DATE.search(unescape(re.sub(r"<[^>]+>", " ", html)))
    if match is None:
        return None
    day, month, year = (int(part) for part in match.groups())
    return date(year, month, day)


def movements_from_html(html: str) -> list[Movement]:
    """Every money movement in one summary. Logins and failed orders are skipped."""
    day = report_date(html)
    if day is None:
        return []
    collector = _DataCells()
    collector.feed(html)
    cells = collector.cells
    movements = []
    for index in range(0, len(cells) - 1, 2):
        occurred_at = datetime.combine(day, _clock(cells[index]))
        movement = parse_row(cells[index + 1], occurred_at, index // 2)
        if movement is not None:
            movements.append(movement)
    return movements


def parse_row(text: str, occurred_at: datetime, row: int = 0) -> Movement | None:
    """One summary row, or None when the row does not move money."""
    body = re.sub(r"^\s*mBank:\s*", "", text)
    if _NOT_DONE.search(body):
        return None
    moved = _MOVED.search(body)
    if moved is None:
        return None
    if body.startswith(("Prich", "Na Vas ucet")):
        direction = "incoming"
    elif body.startswith(("Z Vaseho uc", "Odch")):
        direction = "outgoing"
    else:
        return None
    own_account = _OWN_ACCOUNT.search(body)
    if own_account is None:
        return None

    counterparty = None
    note = None
    if body.startswith(("Z Vaseho uc", "Na Vas ucet")):
        kind = "card"
        counterparty = _group(_CARD_MERCHANT.search(body))
    elif "okamzita" in body:
        kind = "instant"
        counterparty = _group((_INSTANT_IN_PARTY if direction == "incoming" else _INSTANT_OUT_PARTY).search(body))
    elif "Inkaso/SIPO" in body:
        kind = "direct_debit"
    else:
        kind = "transfer"
        note = _group(_TRANSFER_NOTE.search(body))

    available = _AVAILABLE.search(body)
    own_funds = _OWN_FUNDS.search(body)
    return Movement(
        account_number=own_account.group(1),
        occurred_at=occurred_at,
        direction=direction,
        kind=kind,
        amount=amount(moved.group(1)),
        currency=moved.group(2).upper(),
        counterparty=counterparty,
        counterparty_account=_group(_PARTY_ACCOUNT.search(body)),
        note=note,
        available_balance=amount(available.group(1)) if available else None,
        own_funds=amount(own_funds.group(1)) if own_funds else None,
        row=row,
        text=body.rstrip(" ."),
    )


def amount(raw: str) -> float:
    return float(raw.replace(" ", "").replace(" ", "").replace(".", "").replace(",", "."))


def _clock(value: str) -> time:
    match = re.search(r"(\d{1,2}):(\d{2})", value)
    return time(int(match.group(1)), int(match.group(2))) if match else time(0, 0)


def _group(match: re.Match | None) -> str | None:
    if match is None:
        return None
    value = match.group(1).strip(" ;,")
    return value or None
