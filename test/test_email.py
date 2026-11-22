"""Air Bank balance-change emails become the latest payment only."""

from contextlib import contextmanager
from datetime import date, datetime
from email.message import EmailMessage

from custom_components.ha_banking.mail import (
    MailboxFolderNotFound,
    MailMessage,
    fetch_air_bank_messages,
    imap_error_text,
    imap_sender_criteria,
    normalize_mailbox_password,
    parse_air_bank_notice,
    snapshots_from_messages,
    text_from_rfc822,
)
from custom_components.ha_banking.const import Direction
from custom_components.ha_banking.notices import (
    CeskaSporitelnaNoticeParser,
    ParsedNotice,
    apply_account_names,
    configured_bank_folders,
    parse_account_names,
)


def _message(
    body: str,
    *,
    sender: str = "Air Bank <info@airbank.cz>",
    subject: str = "Zvýšení zůstatku",
    sent_at: datetime | None = None,
) -> MailMessage:
    return MailMessage(sender=sender, subject=subject, body=body, sent_at=sent_at)


def test_incoming_notice_reads_account_amount_and_symbol() -> None:
    notice = parse_air_bank_notice(
        _message(
            "Dobrý den, zůstatek na účtu číslo 1234567890/3030 se zvýšil o částku 1 234,56 CZK.\n"
            "Variabilní symbol: 2026001\n",
            sent_at=datetime(2026, 11, 21, 8, 0),
        )
    )
    assert notice is not None
    assert notice.account_number == "1234567890"
    assert notice.amount == 1234.56
    assert notice.currency == "CZK"
    assert notice.direction == "incoming"
    assert notice.variable_symbol == "2026001"


def test_notice_reads_the_account_name_and_available_balance() -> None:
    notice = parse_air_bank_notice(
        _message(
            "Dobrý den,\n\n"
            "zůstatek na účtu Spořicí účet číslo 222/3030 se zvýšil o částku 10,00 CZK. "
            "Dostupný zůstatek k 21.11.2026 v 23:47 je 1 250,50 CZK.\n\n"
            "Příchozí úhrada z účtu Hlavní účet číslo 111/3030\n",
            subject="Zvýšení zůstatku na účtu Spořicí účet",
            sent_at=datetime(2026, 11, 21, 23, 47),
        )
    )
    assert notice is not None
    assert notice.account_number == "222"
    assert notice.name == "Spořicí účet"
    assert notice.available_balance == 1250.50
    assert notice.direction == "incoming"
    assert notice.amount == 10.0
    assert notice.counterparty == "Hlavní účet"
    snapshots = snapshots_from_messages(
        [
            _message(
                "zůstatek na účtu Spořicí účet číslo 222/3030 se snížil o částku 5,00 CZK. "
                "Dostupný zůstatek k 21.11.2026 v 10:00 je 9,00 CZK.",
                subject="Snížení zůstatku na účtu Spořicí účet",
                sent_at=datetime(2026, 11, 21, 10, 0),
            ),
            _message(
                "zůstatek na účtu Spořicí účet číslo 222/3030 se zvýšil o částku 10,00 CZK. "
                "Dostupný zůstatek k 21.11.2026 v 23:47 je 1 250,50 CZK. "
                "Příchozí úhrada z účtu Hlavní účet číslo 111/3030",
                sent_at=datetime(2026, 11, 21, 23, 47),
            ),
        ],
        today=date(2026, 11, 21),
    )
    assert snapshots[0].name == "Spořicí účet"
    assert snapshots[0].available_balance == 1250.50
    assert snapshots[0].booked_balance is None
    assert snapshots[0].logo_url == "https://enablebanking.com/brands/CZ/Air%20Bank/-/resize/128x/"
    assert snapshots[0].payments[0].counterparty == "Hlavní účet"
    assert snapshots[0].latest_incoming is not None
    assert snapshots[0].latest_incoming.counterparty == "Hlavní účet"


def test_outgoing_notice_without_symbol_is_still_a_payment() -> None:
    notice = parse_air_bank_notice(
        _message(
            "zůstatek na účtu číslo 1234567890/3030 se snížil o částku 50,00 Kč.",
            subject="Snížení zůstatku",
        )
    )
    assert notice is not None
    assert notice.direction == "outgoing"
    assert notice.amount == 50.0
    assert notice.variable_symbol is None


def test_notice_without_diacritics_still_parses() -> None:
    notice = parse_air_bank_notice(
        _message(
            "zustatek na uctu cislo 1234567890/3030 se zvysil o castku 10,00 CZK.",
            subject="Zvyseni zustatku",
        )
    )
    assert notice is not None
    assert notice.direction == "incoming"
    assert notice.amount == 10.0


def test_html_notice_is_read_and_other_air_bank_addresses_are_not() -> None:
    html = EmailMessage()
    html["From"] = "info@airbank.cz"
    html["Subject"] = "Zvýšení zůstatku"
    html.set_content(
        "<p>číslo 1234567890/3030 se zvýšil o částku 8,00 CZK.</p>",
        subtype="html",
    )
    notice = parse_air_bank_notice(text_from_rfc822(html.as_bytes()))
    assert notice is not None
    assert notice.amount == 8.0
    marketing = _message(
        "číslo 1234567890/3030 se zvýšil o částku 1,00 CZK.",
        sender="info@novinky.airbank.cz",
    )
    assert parse_air_bank_notice(marketing) is None


def test_other_sender_and_marketing_mail_are_ignored() -> None:
    movement = "číslo 1234567890/3030 se zvýšil o částku 10,00 CZK."
    assert parse_air_bank_notice(_message(movement, sender="someone@example.com")) is None
    assert parse_air_bank_notice(_message("Prosíme vás o kontrolu údajů.")) is None


def test_snapshots_keep_the_newest_payment_in_each_direction() -> None:
    account = "1234567890"
    body = "číslo {account}/3030 se {verb} o částku {amount} CZK."
    snapshots = snapshots_from_messages(
        [
            _message(
                body.format(account=account, verb="zvýšil", amount="10,00"),
                sent_at=datetime(2026, 11, 1, 8, 0),
            ),
            _message(
                body.format(account=account, verb="zvýšil", amount="20,00"),
                sent_at=datetime(2026, 11, 20, 8, 0),
            ),
            _message(
                body.format(account=account, verb="snížil", amount="5,00"),
                subject="Snížení zůstatku",
                sent_at=datetime(2026, 11, 21, 8, 0),
            ),
            _message(
                "číslo 999/3030 se zvýšil o částku 3,00 CZK.",
                sent_at=datetime(2026, 11, 21, 9, 0),
            ),
        ]
    )
    by_account = {item.account_id: item for item in snapshots}
    main = by_account["1234567890/3030"]
    assert main.booked_balance is None
    assert main.available_balance is None
    assert main.latest_incoming is not None
    assert main.latest_incoming.amount == 20.0
    assert main.latest_outgoing is not None
    assert main.latest_outgoing.amount == 5.0
    assert by_account["999/3030"].latest_incoming is not None
    assert by_account["999/3030"].latest_incoming.amount == 3.0


def test_month_sums_count_every_notice_in_the_current_month() -> None:
    account = "1234567890"
    body = "číslo {account}/3030 se {verb} o částku {amount} CZK."
    snapshots = snapshots_from_messages(
        [
            _message(
                body.format(account=account, verb="zvýšil", amount="10,00"),
                sent_at=datetime(2026, 10, 31, 23, 0),
            ),
            _message(
                body.format(account=account, verb="zvýšil", amount="20,00"),
                sent_at=datetime(2026, 11, 2, 8, 0),
            ),
            _message(
                body.format(account=account, verb="zvýšil", amount="3,50"),
                sent_at=datetime(2026, 11, 20, 8, 0),
            ),
            _message(
                body.format(account=account, verb="snížil", amount="5,00"),
                subject="Snížení zůstatku",
                sent_at=datetime(2026, 11, 21, 8, 0),
            ),
            _message(
                body.format(account=account, verb="zvýšil", amount="100,00"),
                sent_at=None,
            ),
        ],
        today=date(2026, 11, 21),
    )
    main = {item.account_id: item for item in snapshots}["1234567890/3030"]
    assert main.booked_balance is None
    assert main.available_balance is None
    assert main.month_incoming == 23.5
    assert main.month_outgoing == 5.0
    assert main.month_net == 18.5
    assert main.year_incoming == 33.5
    assert main.year_outgoing == 5.0
    assert main.year_net == 28.5
    assert main.latest_incoming is not None
    assert main.latest_incoming.amount == 3.5


def test_gmail_app_password_keeps_its_letters_and_drops_the_spaces() -> None:
    assert normalize_mailbox_password("abcd efgh ijkl mnop") == "abcdefghijklmnop"
    assert normalize_mailbox_password("real password") == "real password"


def test_imap_rejection_keeps_the_server_reply() -> None:
    import imaplib

    text = imap_error_text(imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)"))
    assert "Invalid credentials" in text


def test_account_name_mapping_renames_each_account_number() -> None:
    mapping = parse_account_names("1234567890 = Hlavný účet\n\n999/3030=Sporiaci\nbez znamienka\n")
    assert mapping == {"1234567890": "Hlavný účet", "999/3030": "Sporiaci"}
    snapshots = snapshots_from_messages(
        [
            _message(
                "číslo 1234567890/3030 se zvýšil o částku 20,00 CZK.",
                sent_at=datetime(2026, 11, 2, 8, 0),
            ),
            _message(
                "číslo 999/3030 se zvýšil o částku 3,00 CZK.",
                sent_at=datetime(2026, 11, 3, 8, 0),
            ),
        ],
        today=date(2026, 11, 21),
    )
    renamed = {item.account_id: item for item in apply_account_names(snapshots, mapping)}
    assert renamed["1234567890/3030"].name == "Hlavný účet"
    assert renamed["1234567890/3030"].month_incoming == 20.0
    assert renamed["999/3030"].name == "Sporiaci"


def test_rfc822_plain_text_is_the_message_body() -> None:
    mail = EmailMessage()
    mail["From"] = "Air Bank <info@airbank.cz>"
    mail["Subject"] = "Zvýšení zůstatku"
    mail["Date"] = "Sat, 21 Nov 2026 08:00:00 +0100"
    mail.set_content("číslo 1234567890/3030 se zvýšil o částku 15,00 CZK.")
    parsed = text_from_rfc822(mail.as_bytes())
    notice = parse_air_bank_notice(parsed)
    assert notice is not None
    assert notice.amount == 15.0
    assert notice.sent_at == datetime(2026, 11, 21, 8, 0)


class _Mailbox:
    def __init__(self, raw: dict[bytes, bytes]) -> None:
        self.raw = raw
        self.stored: list[tuple] = []

    def uid(self, command: str, *args):
        if command == "search":
            return "OK", [b" ".join(self.raw)]
        if command == "fetch":
            uid = args[0]
            return "OK", [(uid, self.raw[uid])]
        if command == "store":
            self.stored.append(args)
            return "OK", [None]
        raise AssertionError(command)


def _raw_pair() -> dict[bytes, bytes]:
    payment = EmailMessage()
    payment["From"] = "info@airbank.cz"
    payment["Subject"] = "Zvýšení zůstatku"
    payment.set_content("číslo 1234567890/3030 se zvýšil o částku 15,00 CZK.")
    other = EmailMessage()
    other["From"] = "info@airbank.cz"
    other["Subject"] = "Prosíme vás o kontrolu údajů"
    other.set_content("Prosíme vás o kontrolu údajů.")
    return {b"1": payment.as_bytes(), b"2": other.as_bytes()}


def test_marks_only_the_parsed_notice_as_read() -> None:
    mailbox = _Mailbox(_raw_pair())

    @contextmanager
    def connect(host, port, username, password, folder, readonly=True):
        assert folder in {"airbank", "sporitelna", "mbank"}
        assert readonly is False
        yield mailbox

    fetch_air_bank_messages(
        "imap.gmail.com",
        993,
        "user@example.com",
        "secret",
        {"airbank": "airbank"},
        mark_seen=True,
        connect=connect,
    )
    assert mailbox.stored == [(b"1", "+FLAGS", "(\\Seen)")]


class _ExampleBank:
    sender = "info@example-bank.test"

    def parse(self, message: MailMessage) -> ParsedNotice | None:
        if self.sender not in message.sender.lower():
            return None
        return ParsedNotice(
            account_number="42",
            account_id="example:42",
            name="Example Bank 42",
            institution="Example Bank",
            amount=7.0,
            currency="EUR",
            direction="incoming",
            variable_symbol=None,
            sent_at=message.sent_at,
            subject=message.subject,
            configuration_url="https://example-bank.test/",
        )


def test_a_registered_parser_names_its_own_bank() -> None:
    message = _message("payment", sender="Bank <info@example-bank.test>")
    snapshots = snapshots_from_messages([message], parsers=(_ExampleBank(),))
    assert len(snapshots) == 1
    assert snapshots[0].account_id == "example:42"
    assert snapshots[0].institution == "Example Bank"
    assert snapshots[0].name == "Example Bank 42"
    assert snapshots[0].currency == "EUR"
    assert snapshots[0].configuration_url == "https://example-bank.test/"


def test_sender_search_covers_every_registered_bank() -> None:
    class AirBank:
        sender = "info@airbank.cz"

    assert imap_sender_criteria((AirBank(), _ExampleBank())) == [
        "OR",
        "FROM",
        "info@airbank.cz",
        "FROM",
        "info@example-bank.test",
    ]


def test_csas_incoming_notice_reads_amount_balance_and_counterparty() -> None:
    parser = CeskaSporitelnaNoticeParser()
    notice = parser.parse(
        _message(
            "Dobrý den,\n"
            "na účet 1234567890/0800 právě dorazila platba ve výši 20,00 Kč.\n"
            "Na účtu je nově k dispozici 1 500,00 Kč.\n"
            "Směr platby: příchozí\n"
            "Číslo účtu protistrany: 9999999999/3030\n",
            sender="Česká spořitelna <ceskasporitelna@csas.cz>",
            subject="Přišla platba",
            sent_at=datetime(2026, 11, 22, 9, 2),
        )
    )
    assert notice is not None
    assert notice.account_id == "1234567890/0800"
    assert notice.name == "Česká spořitelna 1234567890"
    assert notice.institution == "Česká spořitelna"
    assert notice.amount == 20.0
    assert notice.currency == "CZK"
    assert notice.direction == "incoming"
    assert notice.available_balance == 1500.0
    assert notice.counterparty == "9999999999/3030"
    assert notice.configuration_url == "https://george.csas.cz/"
    assert parser.parse(
        _message("Půjčka s úrokem", sender="Česká spořitelna <ceskasporitelna@csas.cz>", subject="Novinky")
    ) is None
    assert parser.parse(
        _message(
            "na účet 1234567890/0800 právě odešla platba ve výši 20,00 Kč.",
            sender="Česká spořitelna <ceskasporitelna@csas.cz>",
            subject="Odešla platba",
        )
    ) is None


def test_csas_outgoing_notice_reads_the_sent_payment() -> None:
    notice = CeskaSporitelnaNoticeParser().parse(
        _message(
            "z účtu 1234567890/0800 odešla platba ve výši 15,00 Kč.\n"
            "Na účtu zůstává k dispozici 1 485,00 Kč.\n"
            "Směr platby: odchozí\n"
            "Z účtu: 1234567890/0800\n"
            "Na účet: 9999999999/3030\n",
            sender="Česká spořitelna <ceskasporitelna@csas.cz>",
            subject="Odešla platba",
            sent_at=datetime(2026, 11, 22, 9, 18),
        )
    )
    assert notice is not None
    assert notice.direction == Direction.OUTGOING
    assert notice.account_id == "1234567890/0800"
    assert notice.amount == 15.0
    assert notice.available_balance == 1485.0
    assert notice.counterparty == "9999999999/3030"
    assert configured_bank_folders({})["csas"] == "sporitelna"
    assert imap_sender_criteria() == [
        "OR",
        "FROM",
        "info@airbank.cz",
        "OR",
        "FROM",
        "ceskasporitelna@csas.cz",
        "FROM",
        "kontakt@mbank.cz",
    ]


def test_csas_html_notice_uses_the_visible_text() -> None:
    mail = EmailMessage()
    mail["From"] = "Česká spořitelna <ceskasporitelna@csas.cz>"
    mail["Subject"] = "Přišla platba"
    mail.set_content(
        "<p>na účet 1234567890/0800 právě dorazila platba ve výši 20,00 Kč.</p>"
        "<p>Na účtu je nově k dispozici 1&nbsp;500,00 Kč.</p>",
        subtype="html",
    )
    notice = CeskaSporitelnaNoticeParser().parse(text_from_rfc822(mail.as_bytes()))
    assert notice is not None
    assert notice.amount == 20.0
    assert notice.available_balance == 1500.0


def test_csas_notice_ignores_an_inline_image() -> None:
    mail = EmailMessage()
    mail["From"] = "Česká spořitelna <ceskasporitelna@csas.cz>"
    mail["Subject"] = "Přišla platba"
    mail.set_content(
        "<p>na účet 1234567890/0800 právě dorazila platba ve výši 20,00 Kč.</p>",
        subtype="html",
    )
    mail.add_related(b"\x89PNG\r\nnot a payment", maintype="image", subtype="png")
    notice = CeskaSporitelnaNoticeParser().parse(text_from_rfc822(mail.as_bytes()))
    assert notice is not None
    assert notice.amount == 20.0


def test_a_missing_bank_folder_does_not_stop_the_other_bank() -> None:
    opened: list[str] = []

    @contextmanager
    def connect(host, port, username, password, folder, readonly=True):
        opened.append(folder)
        if folder == "sporitelna":
            raise MailboxFolderNotFound("Mailbox folder was not found")
        yield _Mailbox({})

    fetch_air_bank_messages(
        "imap.gmail.com",
        993,
        "user@example.com",
        "secret",
        {"airbank": "airbank", "csas": "sporitelna"},
        connect=connect,
    )
    assert opened == ["airbank", "sporitelna", "mbank"]


def test_leaves_mail_unread_when_marking_is_off() -> None:
    mailbox = _Mailbox(_raw_pair())

    @contextmanager
    def connect(host, port, username, password, folder, readonly=True):
        assert readonly is True
        yield mailbox

    fetch_air_bank_messages(
        "imap.gmail.com",
        993,
        "user@example.com",
        "secret",
        {"airbank": "INBOX"},
        connect=connect,
    )
    assert mailbox.stored == []


class _FolderBank:
    def __init__(self, bank_id: str, sender: str) -> None:
        self.bank_id = bank_id
        self.sender = sender
        self.default_folder = bank_id

    def parse(self, message: MailMessage) -> ParsedNotice | None:
        return None


def test_each_bank_is_read_from_its_own_folder() -> None:
    opened: list[str] = []
    searches: list[tuple[str, tuple]] = []

    class Mailbox:
        def __init__(self, folder: str) -> None:
            self.folder = folder

        def uid(self, command: str, *args):
            if command == "search":
                searches.append((self.folder, args))
                return "OK", [b""]
            raise AssertionError(command)

    @contextmanager
    def connect(host, port, username, password, folder, readonly=True):
        opened.append(folder)
        yield Mailbox(folder)

    banks = (
        _FolderBank("airbank", "info@airbank.cz"),
        _FolderBank("example", "info@example-bank.test"),
    )
    fetch_air_bank_messages(
        "imap.gmail.com",
        993,
        "user@example.com",
        "secret",
        {"airbank": "airbank", "example": "fio"},
        parsers=banks,
        connect=connect,
        today=date(2026, 11, 21),
    )
    assert opened == ["airbank", "fio"]
    air_search = searches[0][1]
    other_search = searches[1][1]
    assert "info@airbank.cz" in air_search
    assert "info@example-bank.test" not in air_search
    assert "info@example-bank.test" in other_search
    assert "info@airbank.cz" not in other_search
    assert "01-Jan-2026" in air_search
