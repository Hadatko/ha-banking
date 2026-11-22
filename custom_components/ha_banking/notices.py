"""Bank email parsers. A new bank is another parser in BANK_PARSERS."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
import re
import unicodedata

from . import mbank
from .const import BankId, Direction
from .snapshot import (
    AccountSnapshot,
    PaymentRecord,
    PaymentView,
    bank_logo_url,
    limit_payments,
    payment_id,
    period_totals,
)


@dataclass(frozen=True)
class MailMessage:
    """One mailbox message reduced to the fields a bank parser needs."""

    sender: str
    subject: str
    body: str
    sent_at: datetime | None
    html: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedNotice:
    """One balance-change notice from a registered bank."""

    account_number: str
    account_id: str
    name: str
    institution: str
    amount: float
    currency: str
    direction: Direction
    variable_symbol: str | None
    sent_at: datetime | None
    subject: str
    configuration_url: str | None
    available_balance: float | None = None
    country: str = "CZ"
    counterparty: str | None = None
    reference_id: str | None = None


class AirBankNoticeParser:
    """Air Bank balance mail: the account number, bank code 3030, and the amount."""

    bank_id = BankId.AIRBANK
    default_folder = "airbank"
    sender = "info@airbank.cz"
    _sender = re.compile(r"(?:^|[\s<\"])info@airbank\.cz(?=$|[\s>\"])", re.IGNORECASE)
    _movement = re.compile(
        r"cislo\s+([0-9]+)/3030\s+se\s+(zvysil|snizil)\s+o\s+castku\s+"
        r"([0-9][0-9 .,\u00a0]*)\s*(?:czk|kc)",
        re.IGNORECASE,
    )
    _symbol = re.compile(r"variabilni\s+symbol:\s*([0-9]+)", re.IGNORECASE)
    _label = re.compile(
        r"(?:zůstatek|zustatek)\s+na\s+(?:účtu|uctu)\s+(.+?)\s+(?:číslo|cislo)\s+[0-9]+/3030",
        re.IGNORECASE,
    )
    _available = re.compile(
        r"(?:dostupný|dostupny)\s+(?:zůstatek|zustatek)\s+k\s+.+?\s+je\s+"
        r"([0-9][0-9 .,\u00a0]*)\s*(?:czk|kč|kc)",
        re.IGNORECASE,
    )
    _counterparty = re.compile(
        r"(?:příchozí|prichozi|odchozí|odchozi)\s+(?:úhrada|uhrada)\s+(?:z|na)\s+"
        r"(?:účtu|uctu)\s+(.+?)\s+(?:číslo|cislo)\s+[0-9]+/3030",
        re.IGNORECASE,
    )

    def parse(self, message: MailMessage) -> ParsedNotice | None:
        if self._sender.search(message.sender) is None:
            return None
        folded = _fold(message.body)
        match = self._movement.search(folded)
        if match is None:
            return None
        symbol = self._symbol.search(folded)
        account_number = match.group(1)
        label = self._label.search(message.body)
        available = self._available.search(message.body) or self._available.search(folded)
        party = self._counterparty.search(message.body) or self._counterparty.search(folded)
        account_name = label.group(1).strip() if label and label.group(1).strip() else f"Air Bank {account_number}"
        counterparty = party.group(1).strip() if party and party.group(1).strip() else None
        return ParsedNotice(
            account_number=account_number,
            account_id=f"{account_number}/3030",
            name=account_name,
            institution="Air Bank",
            amount=_amount(match.group(3)),
            currency="CZK",
            direction=Direction.INCOMING if match.group(2).lower() == "zvysil" else Direction.OUTGOING,
            variable_symbol=symbol.group(1) if symbol else None,
            sent_at=message.sent_at,
            subject=message.subject,
            configuration_url="https://ib.airbank.cz/",
            available_balance=_amount(available.group(1)) if available else None,
            counterparty=counterparty,
        )


class MBankNoticeParser:
    """mBank daily E-mail Push: one mail lists every movement of the previous day."""

    bank_id = BankId.MBANK
    default_folder = "mbank"
    sender = mbank.SENDER
    _sender = re.compile(r"(?:^|[\s<\"])kontakt@mbank\.cz(?=$|[\s>\"])", re.IGNORECASE)

    def parse(self, message: MailMessage) -> ParsedNotice | None:
        notices = self.parse_all(message)
        return notices[0] if notices else None

    def parse_all(self, message: MailMessage) -> list[ParsedNotice]:
        if self._sender.search(message.sender) is None:
            return []
        for html in message.html:
            movements = mbank.movements_from_html(html)
            if movements:
                return [self._notice(movement) for movement in movements]
        return []

    @staticmethod
    def _notice(movement: mbank.Movement) -> ParsedNotice:
        return ParsedNotice(
            account_number=movement.account_number,
            account_id=f"{movement.account_number}/{mbank.BANK_CODE}",
            name=f"mBank {movement.account_number}",
            institution="mBank",
            amount=movement.amount,
            currency=movement.currency,
            direction=Direction(movement.direction),
            variable_symbol=None,
            sent_at=movement.occurred_at,
            subject=movement.text,
            configuration_url="https://online.mbank.cz/",
            available_balance=movement.available_balance,
            counterparty=movement.counterparty or movement.counterparty_account,
            reference_id=movement.movement_id,
        )


class CeskaSporitelnaNoticeParser:
    """Česká spořitelna notice for a received or sent payment."""

    bank_id = BankId.CSAS
    default_folder = "sporitelna"
    sender = "ceskasporitelna@csas.cz"
    _sender = re.compile(
        r"(?:^|[\s<\"])ceskasporitelna@csas\.cz(?=$|[\s>\"])",
        re.IGNORECASE,
    )
    _amount = r"([0-9][0-9 .,\u00a0\u202f]*)\s*(?:kc|czk)"
    _incoming_movement = re.compile(
        r"na\s+ucet\s+([0-9][0-9 ]*)/([0-9]{4})\s+prave\s+dorazila\s+platba\s+ve\s+vysi\s+" + _amount,
        re.IGNORECASE,
    )
    _outgoing_movement = re.compile(
        r"z\s+uctu\s+([0-9][0-9 ]*)/([0-9]{4})\s+odesla\s+platba\s+ve\s+vysi\s+" + _amount,
        re.IGNORECASE,
    )
    _available = re.compile(
        r"na\s+uctu\s+(?:je\s+nove|zustava)\s+k\s+dispozici\s+"
        r"([0-9][0-9 .,\u00a0\u202f]*)\s*(?:kc|czk)",
        re.IGNORECASE,
    )
    _counterparty = re.compile(
        r"cislo\s+uctu\s+protistrany:\s*([0-9][0-9 ]*)/([0-9]{4})",
        re.IGNORECASE,
    )
    _recipient = re.compile(
        r"na\s+ucet:\s*([0-9][0-9 ]*)/([0-9]{4})",
        re.IGNORECASE,
    )

    def parse(self, message: MailMessage) -> ParsedNotice | None:
        if self._sender.search(message.sender) is None:
            return None
        folded = _fold(message.body)
        match = self._incoming_movement.search(folded)
        direction = Direction.INCOMING
        if match is None:
            match = self._outgoing_movement.search(folded)
            direction = Direction.OUTGOING
        if match is None:
            return None
        account_number = "".join(match.group(1).split())
        available = self._available.search(folded)
        party = self._counterparty.search(folded) or self._recipient.search(folded)
        counterparty = f"{''.join(party.group(1).split())}/{party.group(2)}" if party else None
        return ParsedNotice(
            account_number=account_number,
            account_id=f"{account_number}/{match.group(2)}",
            name=f"Česká spořitelna {account_number}",
            institution="Česká spořitelna",
            amount=_amount(match.group(3)),
            currency="CZK",
            direction=direction,
            variable_symbol=None,
            sent_at=message.sent_at,
            subject=message.subject,
            configuration_url="https://george.csas.cz/",
            available_balance=_amount(available.group(1)) if available else None,
            counterparty=counterparty,
        )


BANK_PARSERS = (AirBankNoticeParser(), CeskaSporitelnaNoticeParser(), MBankNoticeParser())


def parse_air_bank_notice(message: MailMessage) -> ParsedNotice | None:
    """Return a payment from an Air Bank notice, or None when the mail is not one."""
    return AirBankNoticeParser().parse(message)


def parse_notice(message: MailMessage, parsers=BANK_PARSERS) -> ParsedNotice | None:
    """Return the first registered bank that recognizes this message."""
    for parser in parsers:
        notice = parser.parse(message)
        if notice is not None:
            return notice
    return None


def parse_notices(message: MailMessage, parsers=BANK_PARSERS) -> list[ParsedNotice]:
    """Every payment in the message. A summary mail can hold several."""
    for parser in parsers:
        parse_all = getattr(parser, "parse_all", None)
        notices = parse_all(message) if parse_all else [n for n in (parser.parse(message),) if n]
        if notices:
            return notices
    return []


def parse_account_names(raw: str | dict[str, str]) -> dict[str, str]:
    """Read lines of ``account number=name``. Blank lines and lines without ``=`` are ignored."""
    if isinstance(raw, dict):
        return {
            "".join(str(number).split()): str(name).strip()
            for number, name in raw.items()
            if "".join(str(number).split()) and str(name).strip()
        }
    names: dict[str, str] = {}
    for line in str(raw or "").splitlines():
        if "=" not in line:
            continue
        number, name = line.split("=", 1)
        number = "".join(number.split())
        name = name.strip()
        if number and name:
            names[number] = name
    return names


def format_account_names(mapping: dict[str, str]) -> str:
    """Show a stored account-name mapping as one ``number=name`` line each."""
    return "\n".join(f"{number}={name}" for number, name in mapping.items())


def apply_account_names(snapshots: list[AccountSnapshot], mapping: dict[str, str]) -> list[AccountSnapshot]:
    """Replace a snapshot name when its account number is in the mapping."""
    if not mapping:
        return list(snapshots)
    return [_with_account_name(snapshot, mapping) for snapshot in snapshots]


def _with_account_name(snapshot: AccountSnapshot, mapping: dict[str, str]) -> AccountSnapshot:
    number = snapshot.account_id.split("/", 1)[0]
    name = mapping.get(snapshot.account_id) or mapping.get(number)
    if not name:
        return snapshot
    return replace(snapshot, name=name)


def folder_field(bank_id: str) -> str:
    """Config-flow field that stores one bank's mailbox folder."""
    return f"folder_{bank_id}"


def configured_bank_folders(data: dict, parsers=BANK_PARSERS) -> dict[str, str]:
    """Return a folder for every bank. An older single folder applies to all of them."""
    stored = data.get("bank_folders") or {}
    legacy = data.get("imap_folder")
    folders: dict[str, str] = {}
    for parser in parsers:
        raw = data.get(folder_field(parser.bank_id))
        if raw is None:
            raw = stored.get(parser.bank_id, legacy)
        if raw is None:
            raw = parser.default_folder
        folders[parser.bank_id] = str(raw).strip() or parser.default_folder
    return folders


def imap_sender_criteria(parsers=BANK_PARSERS) -> list[str]:
    """IMAP search keys for every registered bank sender."""
    senders = [parser.sender for parser in parsers if parser.sender]
    if not senders:
        return []
    criteria = ["FROM", senders[-1]]
    for sender in reversed(senders[:-1]):
        criteria = ["OR", "FROM", sender, *criteria]
    return criteria


def snapshots_from_messages(
    messages: list[MailMessage],
    parsers=BANK_PARSERS,
    today: date | None = None,
) -> list[AccountSnapshot]:
    """Keep the newest payment in each direction and sum notices from the current month."""
    notices = [notice for message in messages for notice in parse_notices(message, parsers)]
    return snapshots_from_notices(notices, today)


def snapshots_from_notices(notices: list[ParsedNotice], today: date | None = None) -> list[AccountSnapshot]:
    """One snapshot per account from notices already parsed, in mailbox order."""
    if today is None:
        today = date.today()
    grouped: dict[str, list[tuple[int, ParsedNotice]]] = {}
    for order, notice in enumerate(notices):
        grouped.setdefault(notice.account_id, []).append((order, notice))
    return [_snapshot(items, today) for items in grouped.values()]


def _snapshot(notices: list[tuple[int, ParsedNotice]], today: date) -> AccountSnapshot:
    incoming = _newest(notices, Direction.INCOMING)
    outgoing = _newest(notices, Direction.OUTGOING)
    sample = _latest(notices)
    totals = period_totals(
        [(notice.sent_at.date() if notice.sent_at else None, notice.direction, notice.amount) for _, notice in notices],
        today,
        [
            (notice.sent_at, notice.available_balance)
            for _, notice in notices
            if notice.sent_at is not None and notice.available_balance is not None
        ],
    )
    return AccountSnapshot(
        account_id=sample.account_id,
        name=sample.name,
        iban="",
        currency=sample.currency,
        product="Email",
        booked_balance=None,
        available_balance=_latest_available_balance(notices),
        latest_incoming=_payment(incoming) if incoming else None,
        latest_outgoing=_payment(outgoing) if outgoing else None,
        latest_status=None,
        institution=sample.institution,
        configuration_url=sample.configuration_url,
        logo_url=bank_logo_url(sample.institution, sample.country),
        payments=_notice_payments(notices),
        **totals,
    )


def _latest(notices: list[tuple[int, ParsedNotice]]) -> ParsedNotice:
    return max(notices, key=lambda item: (item[1].sent_at or datetime.min, item[0]))[1]


def _latest_available_balance(notices: list[tuple[int, ParsedNotice]]) -> float | None:
    reported = [item for item in notices if item[1].available_balance is not None]
    if not reported:
        return None
    return _latest(reported).available_balance


def _newest(notices: list[tuple[int, ParsedNotice]], direction: Direction) -> ParsedNotice | None:
    chosen = [item for item in notices if item[1].direction == direction]
    if not chosen:
        return None
    return max(chosen, key=lambda item: (item[1].sent_at or datetime.min, item[0]))[1]


def _notice_payments(notices: list[tuple[int, ParsedNotice]]) -> tuple[PaymentRecord, ...]:
    records: list[PaymentRecord] = []
    for _, notice in notices:
        booked = notice.sent_at.isoformat() if notice.sent_at else None
        records.append(
            PaymentRecord(
                payment_id=payment_id(
                    notice.account_id,
                    notice.direction,
                    notice.amount,
                    booked,
                    notice.reference_id or notice.variable_symbol or notice.subject or "",
                ),
                direction=notice.direction,
                amount=notice.amount,
                currency=notice.currency,
                booked=booked,
                reference=notice.variable_symbol,
                message=notice.subject or None,
                counterparty=notice.counterparty,
            )
        )
    return limit_payments(records)


def _payment(notice: ParsedNotice) -> PaymentView:
    booked = notice.sent_at.date().isoformat() if notice.sent_at else None
    return PaymentView(
        amount=notice.amount,
        currency=notice.currency,
        counterparty=notice.counterparty,
        status=None,
        booking_date=booked,
        value_date=None,
        message=None,
        reference=notice.variable_symbol,
        entry_reference=None,
        information=notice.subject or None,
    )


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(character for character in normalized if not unicodedata.combining(character))


def _amount(raw: str) -> float:
    cleaned = raw.replace("\u00a0", "").replace(" ", "")
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    return float(cleaned)
