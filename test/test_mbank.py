"""mBank daily E-mail Push summaries. Row texts come from real summaries (2024-11 to 2026-11)
with account numbers and names replaced."""

from contextlib import contextmanager
from datetime import date, datetime
from email.message import EmailMessage

import pytest

from custom_components.ha_banking import mail
from custom_components.ha_banking.mail import FolderNotFound, fetch_air_bank_messages, snapshots_from_messages, text_from_rfc822
from custom_components.ha_banking.mbank import movements_from_html, parse_row
from custom_components.ha_banking.notices import (
    BANK_PARSERS,
    MBankNoticeParser,
    imap_sender_criteria,
    parse_notice,
    parse_notices,
)

ACCOUNT = "12345678/6210"

ROWS_13 = [
    ("02:33", "mBank: Trvaly prikaz /inkaso pro NAJEMNE 1175 -AVAL, castka 6829,00 CZK nebyl proveden. Pokus o provedeni platby budeme opakovat 3 dny."),
    ("12:53", "mBank: Prichozi platba z uc. ...11111/0100 na uc. 12345678 castka 51980,00 CZK; AV:Novak Jan...; Dostup.zust: 57655,39 CZK; Vlast.prostr: 52655,39 CZK."),
    ("14:36", "mBank: Odch. okamzita platba pro ERA - HYPO, na ...22222/0300 castka 5500,00 CZK; z 12345678 dispo: 52155,39 CZK."),
    ("14:36", "mBank: Z Vaseho uc. 12345678 odeslo: 550,00 CZK AV: MSPOŘENÍ; Dostup.zust:51605,39 CZK; Vlast.prostr: 46605,39 CZK."),
    ("16:36", "mBank: Odch. okamzita platba pro JANA NOVÁKOVÁ, na ...33333/0600 castka 1255,80 CZK; z 12345678 dispo: 34349,59 CZK."),
]
ROWS_15 = [
    ("11:16", "mBank: Prich. okamzita platba od, z ...33333/0600 castka 600,00 CZK; na uc. 12345678 ; dispo: 20728,71 CZK."),
    ("18:28", "mBank: Z Vaseho uc. 12345678 odeslo: 84,00 CZK AV: CYRILOVO PEK...; Dostup.zust:18702,71 CZK; Vlast.prostr: 13702,71 CZK."),
]
ROWS_19 = [
    ("04:22", "mBank: Potvrzeni o uspesnem prihlaseni Internetoveho bankovnictvi. Datum a hodina udalosti: 19-11-2026 04:22."),
    ("09:05", "mBank: Odchozi platba - Provedeno Inkaso/SIPO z uctu 12345678 na ucet ...44444/0300  castka: 337,00 CZK Dostup.zust:15640,88 CZK; Vlast.prostr: 10640,88 CZK."),
]
ROWS_SAME_MINUTE = [
    ("13:00", "mBank: Odchozi platba z uc. 12345678 na uc. ...55555/0600 castka 389,00 CZK; VDF+LAST; Dostup.zust: 27836,87 CZK; Vlast.prostr: 22836,87 CZK."),
    ("13:00", "mBank: Odchozi platba z uc. 12345678 na uc. ...55555/0600 castka 389,00 CZK; VDF+LAST; Dostup.zust: 27836,87 CZK; Vlast.prostr: 22836,87 CZK."),
]
AT = datetime(2026, 11, 13, 12, 0)


def _html(day: str, rows) -> str:
    cells = "".join(
        f'<tr><td class="data" align="center" style="border: 2px solid #dcddde;">\r\n  <nobr>{clock}</nobr>\r\n</td>\r\n'
        f'<td class="data" style="border: 2px solid #dcddde;">{text}</td></tr>\r\n'
        for clock, text in rows
    )
    return (
        '<html><head><META http-equiv="Content-Type" content="text/html;charset=utf-8"><title>E-mail Push</title></head>'
        "<body><table><tr><td>Vážený kliente, posíláme přehled provedených operací na účtu.</td></tr></table>"
        f'<table><tr valign="top" height="60"><td align="left" class="znaki"><strong>E-mailová notifikace ze dne: </strong>{day}</td></tr>'
        '<tr><td><table><tr class="head"><td><center><nobr>Čas operace</nobr><br />(HH:MM)</center></td>'
        f"<td><center>Obsah informace</center></td></tr>\r\n{cells}</table></td></tr></table></body></html>"
    )


def _raw_mail(day: str, rows, sender: str = "kontakt@mbank.cz") -> bytes:
    """Same layout as the real mail: signed, a plain cover note, the table as an HTML attachment."""
    inner = EmailMessage()
    inner.set_content("Dobrý den,\n\nV příloze Vám zasíláme souhrn operací na Vašem účtu za předchozí den.\n")
    inner.add_attachment(
        _html(day, rows).encode("utf-8"), maintype="text", subtype="html", filename=f"E-MAIL PUSH {day}.html"
    )
    outer = EmailMessage()
    outer["From"] = sender
    outer["Subject"] = "mBank – Email Push"
    outer["Date"] = "Sat, 14 Nov 2026 06:32:11 +0100"
    outer.make_mixed()
    outer.attach(inner)
    outer.add_attachment(b"signature", maintype="application", subtype="pkcs7-signature", filename="smime.p7s")
    return outer.as_bytes()


def test_card_payment_row() -> None:
    m = parse_row(ROWS_13[3][1], AT)
    assert (m.account_number, m.direction, m.kind, m.amount, m.currency) == ("12345678", "outgoing", "card", 550.0, "CZK")
    assert (m.counterparty, m.available_balance, m.own_funds) == ("MSPOŘENÍ", 51605.39, 46605.39)


def test_incoming_transfer_row() -> None:
    m = parse_row(ROWS_13[1][1], AT)
    assert (m.direction, m.kind, m.amount, m.available_balance) == ("incoming", "transfer", 51980.0, 57655.39)
    assert (m.counterparty_account, m.note) == ("...11111/0100", "Novak Jan...")


def test_instant_rows() -> None:
    out = parse_row(ROWS_13[2][1], AT)
    assert (out.direction, out.kind, out.amount, out.counterparty) == ("outgoing", "instant", 5500.0, "ERA - HYPO")
    assert (out.account_number, out.available_balance, out.own_funds) == ("12345678", 52155.39, None)
    incoming = parse_row(ROWS_15[0][1], AT)
    assert (incoming.direction, incoming.amount, incoming.counterparty) == ("incoming", 600.0, None)
    assert incoming.counterparty_account == "...33333/0600"


def test_card_refund_row() -> None:
    m = parse_row(
        "mBank: Na Vas ucet 12345678 prislo: 665,00 CZK AV: Alza.cz /Prague; Dostup.zust: 35529,28 CZK; Vlast.prostr: 30529,28 CZK.",
        AT,
    )
    assert (m.account_number, m.direction, m.kind, m.amount) == ("12345678", "incoming", "card", 665.0)
    assert (m.counterparty, m.available_balance, m.own_funds) == ("Alza.cz /Prague", 35529.28, 30529.28)


def test_direct_debit_and_transfer_note() -> None:
    debit = parse_row(ROWS_19[1][1], AT)
    assert (debit.direction, debit.kind, debit.amount, debit.available_balance) == ("outgoing", "direct_debit", 337.0, 15640.88)
    transfer = parse_row(ROWS_SAME_MINUTE[0][1], AT)
    assert (transfer.kind, transfer.note) == ("transfer", "VDF+LAST")


def test_rows_that_do_not_move_money() -> None:
    assert parse_row(ROWS_13[0][1], AT) is None
    assert parse_row(ROWS_19[0][1], AT) is None
    assert parse_row("mBank: Vyber z bankomatu castka 500,00 CZK", AT) is None


def test_thousands_separator_and_negative_balance() -> None:
    m = parse_row("mBank: Z Vaseho uc. 12345678 odeslo: 1 234 567,89 CZK AV: SHOP; Dostup.zust:-120,00 CZK", AT)
    assert (m.amount, m.available_balance, m.account_number) == (1234567.89, -120.0, "12345678")


def test_summary_dates_each_row() -> None:
    movements = movements_from_html(_html("13.11.2026", ROWS_13))
    assert [m.occurred_at for m in movements] == [
        datetime(2026, 11, 13, 12, 53),
        datetime(2026, 11, 13, 14, 36),
        datetime(2026, 11, 13, 14, 36),
        datetime(2026, 11, 13, 16, 36),
    ]
    assert movements_from_html("<html>Novinky, které se vyplatí znát</html>") == []


def test_table_is_read_from_the_html_attachment() -> None:
    message = text_from_rfc822(_raw_mail("13.11.2026", ROWS_13))
    assert "souhrn operací" in message.body
    notices = parse_notices(message)
    assert len(notices) == 4
    assert {n.account_id for n in notices} == {ACCOUNT}
    assert notices[0].institution == "mBank"
    assert parse_notice(message) == notices[0]


def test_other_sender_is_ignored() -> None:
    message = text_from_rfc822(_raw_mail("13.11.2026", ROWS_13, sender="kontakt@mbank.cz.example.com"))
    assert parse_notices(message) == []


def test_same_minute_payments_stay_separate() -> None:
    snapshots = snapshots_from_messages([text_from_rfc822(_raw_mail("15.11.2024", ROWS_SAME_MINUTE))], today=date(2024, 11, 20))
    assert len(snapshots[0].payments) == 2
    assert snapshots[0].month_outgoing == 778.0


def test_snapshot_from_several_summaries() -> None:
    messages = [text_from_rfc822(_raw_mail(day, rows)) for day, rows in (("15.11.2026", ROWS_15), ("13.11.2026", ROWS_13))]
    (snapshot,) = snapshots_from_messages(messages, today=date(2026, 11, 20))
    assert snapshot.account_id == ACCOUNT
    assert snapshot.name == "mBank 12345678"
    assert snapshot.available_balance == 18702.71
    assert snapshot.latest_incoming.amount == 600.0
    assert snapshot.latest_incoming.booking_date == "2026-11-15"
    assert snapshot.latest_outgoing.counterparty == "CYRILOVO PEK..."
    assert snapshot.month_incoming == 52580.0
    assert snapshot.month_outgoing == 5500.0 + 550.0 + 1255.80 + 84.0
    assert len(snapshot.payments) == 6


def test_mbank_is_registered_for_imap_search() -> None:
    assert any(isinstance(parser, MBankNoticeParser) for parser in BANK_PARSERS)
    assert imap_sender_criteria() == ["OR", "FROM", "info@airbank.cz", "FROM", "kontakt@mbank.cz"]


class _Box:
    def __init__(self, raw: dict[bytes, bytes]) -> None:
        self.raw = raw

    def uid(self, command: str, *args):
        if command == "search":
            return "OK", [b" ".join(self.raw)]
        return "OK", [(args[0], self.raw[args[0]])]


def _connect_to(folders: dict[str, dict[bytes, bytes]]):
    @contextmanager
    def connect(host, port, username, password, folder, readonly=True):
        if folder not in folders:
            raise FolderNotFound(folder)
        yield _Box(folders[folder])

    return connect


def test_summaries_are_fetched_and_a_missing_bank_folder_is_skipped() -> None:
    connect = _connect_to({"M-Banka": {b"1": _raw_mail("13.11.2026", ROWS_13)}})
    messages = fetch_air_bank_messages(
        "imap.gmail.com", 993, "u", "p", {"airbank": "airbank", "mbank": "M-Banka"}, today=date(2026, 11, 20), connect=connect
    )
    assert len(parse_notices(messages[0])) == 4


def test_every_folder_missing_is_still_an_error() -> None:
    with pytest.raises(FolderNotFound):
        fetch_air_bank_messages("h", 993, "u", "p", {"airbank": "x", "mbank": "y"}, connect=_connect_to({}))


def test_mailbox_check_accepts_one_existing_folder(monkeypatch) -> None:
    monkeypatch.setattr(mail, "_mailbox", _connect_to({"airbank": {}}))
    mail.open_mailbox("h", 993, "u", "p", ["airbank", "mbank"])
    with pytest.raises(mail.FolderNotFound):
        mail.open_mailbox("h", 993, "u", "p", ["mbank"])


def test_folder_with_spaces_is_quoted(monkeypatch) -> None:
    selected = []

    class Client:
        def __init__(self, host, port):
            pass

        def login(self, username, password):
            return "OK", [b""]

        def select(self, folder, readonly=True):
            selected.append(folder)
            return "OK", [b"1"]

        def logout(self):
            return "BYE", [b""]

    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", Client)
    mail.open_mailbox("h", 993, "u", "p", [".::M-Banka ::.", '"INBOX"', 'a"b'])
    assert selected == ['".::M-Banka ::."', '"INBOX"', '"a\\"b"']
