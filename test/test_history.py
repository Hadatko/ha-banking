"""Monthly history and the in-memory mailbox sync."""

from contextlib import contextmanager
from datetime import date, datetime

from custom_components.ha_banking.const import Direction
from custom_components.ha_banking.mail import MailboxCache, MailboxFolderNotFound, fetch_notices
from custom_components.ha_banking.notices import MBankNoticeParser, snapshots_from_notices
from custom_components.ha_banking.snapshot import mail_history_start, month_keys, monthly_totals

from test_mbank import _raw_mail

PARSERS = (MBankNoticeParser(),)
TODAY = date(2026, 11, 22)


def _rows(clock: str, amount: str, balance: str, account: str = "12345678") -> list[tuple[str, str]]:
    return [
        (
            clock,
            f"mBank: Z Vaseho uc. {account} odeslo: {amount} CZK AV: SHOP; Dostup.zust:{balance} CZK; "
            "Vlast.prostr: 1,00 CZK.",
        )
    ]


def test_month_keys_end_with_the_current_month() -> None:
    keys = month_keys(TODAY)
    assert len(keys) == 13
    assert keys[0] == "2025-11"
    assert keys[-1] == "2026-11"
    assert month_keys(date(2026, 1, 5), 2) == ["2025-12", "2026-01"]


def test_mail_window_covers_the_history_and_the_year() -> None:
    assert mail_history_start(TODAY) == date(2025, 11, 1)
    assert mail_history_start(date(2026, 1, 5)) == date(2025, 1, 1)


def test_monthly_totals_fill_empty_months_and_keep_the_last_balance() -> None:
    months = monthly_totals(
        [
            (date(2026, 10, 3), Direction.INCOMING, 100.0),
            (date(2026, 10, 9), Direction.OUTGOING, 30.5),
            (date(2026, 11, 1), Direction.OUTGOING, 10.0),
            (date(2024, 1, 1), Direction.INCOMING, 999.0),
            (None, Direction.INCOMING, 5.0),
        ],
        TODAY,
        [(datetime(2026, 10, 9, 12, 0), 70.0), (datetime(2026, 10, 3, 8, 0), 100.0)],
    )
    by_month = {month["month"]: month for month in months}
    assert len(months) == 13
    assert by_month["2026-10"] == {
        "month": "2026-10",
        "incoming": 100.0,
        "outgoing": 30.5,
        "count": 2,
        "net": 69.5,
        "balance": 70.0,
    }
    assert by_month["2026-11"]["outgoing"] == 10.0
    assert by_month["2026-11"]["balance"] is None
    assert by_month["2026-01"]["count"] == 0


class _Folder:
    """IMAP double: UID SEARCH with SINCE or a UID range, FETCH, UIDVALIDITY."""

    def __init__(self, mails: dict[int, bytes], uidvalidity: str = "51") -> None:
        self.mails = mails
        self.uidvalidity = uidvalidity
        self.searches: list[tuple] = []
        self.fetched: list[int] = []

    def response(self, code: str):
        return code, [self.uidvalidity.encode()]

    def uid(self, command: str, *args):
        if command == "search":
            self.searches.append(args)
            if args[1] == "UID":
                first = int(args[2].split(":")[0])
                found = [u for u in self.mails if u >= first] or [max(self.mails)]
            else:
                found = list(self.mails)
            return "OK", [b" ".join(str(u).encode() for u in sorted(found))]
        uid = int(args[0])
        self.fetched.append(uid)
        return "OK", [(b"1 (BODY[] {1}", self.mails[uid]), b")"]


def _connect(folders: dict[str, _Folder]):
    @contextmanager
    def connect(host, port, username, password, folder, readonly=True):
        if folder not in folders:
            raise MailboxFolderNotFound(folder)
        yield folders[folder]

    return connect


def _fetch(folder: _Folder, cache: MailboxCache, today: date = TODAY):
    return fetch_notices(
        "h", 993, "u", "p", {"mbank": "mbank"}, cache, today=today, connect=_connect({"mbank": folder}), parsers=PARSERS
    )


def test_first_read_is_full_and_later_reads_fetch_only_new_mail() -> None:
    folder = _Folder({10: _raw_mail("13.11.2026", _rows("10:00", "100,00", "900,00"))})
    cache = MailboxCache()
    assert [n.amount for n in _fetch(folder, cache)] == [100.0]
    assert folder.searches[0][1] == "SINCE"
    assert folder.searches[0][2] == "01-Nov-2025"

    folder.fetched.clear()
    assert [n.amount for n in _fetch(folder, cache)] == [100.0]
    assert folder.searches[-1][1:3] == ("UID", "11:*")
    assert folder.fetched == []

    folder.mails[12] = _raw_mail("14.11.2026", _rows("09:00", "50,00", "850,00"))
    notices = _fetch(folder, cache)
    assert folder.fetched == [12]
    assert [n.amount for n in notices] == [100.0, 50.0]
    (snapshot,) = snapshots_from_notices(notices, TODAY)
    assert snapshot.available_balance == 850.0
    assert snapshot.months[-1]["outgoing"] == 150.0
    assert snapshot.months[-1]["balance"] == 850.0


def test_changed_uidvalidity_reads_the_folder_again() -> None:
    folder = _Folder({10: _raw_mail("13.11.2026", _rows("10:00", "100,00", "900,00"))})
    cache = MailboxCache()
    _fetch(folder, cache)
    folder.uidvalidity = "52"
    folder.fetched.clear()
    assert [n.amount for n in _fetch(folder, cache)] == [100.0]
    assert folder.searches[-1][1] == "SINCE"
    assert folder.fetched == [10]


def test_notices_older_than_the_window_are_forgotten() -> None:
    folder = _Folder({10: _raw_mail("13.11.2026", _rows("10:00", "100,00", "900,00"))})
    cache = MailboxCache()
    _fetch(folder, cache)
    later = date(2027, 12, 2)
    assert _fetch(folder, cache, later) == []
    assert cache.folders["mbank"].notices == {}
    assert cache.folders["mbank"].last_uid == 10


def test_a_missing_folder_is_skipped_and_forgotten() -> None:
    cache = MailboxCache()
    _fetch(_Folder({10: _raw_mail("13.11.2026", _rows("10:00", "100,00", "900,00"))}), cache)
    notices = fetch_notices(
        "h", 993, "u", "p", {"mbank": "mbank"}, cache, today=TODAY, connect=_connect({}), parsers=PARSERS
    )
    assert notices == []
    assert "mbank" not in cache.folders


def test_months_are_a_sensor_attribute() -> None:
    folder = _Folder({10: _raw_mail("13.11.2026", _rows("10:00", "100,00", "900,00"))})
    (snapshot,) = snapshots_from_notices(_fetch(folder, MailboxCache()), TODAY)
    months = snapshot.readings()["recent_payments"].attributes["months"]
    assert len(months) == 13
    assert months[-1]["month"] == "2026-11"


def test_a_missing_folder_is_reported_once(caplog) -> None:
    cache = MailboxCache()
    for _ in range(3):
        fetch_notices("h", 993, "u", "p", {"mbank": "mbank"}, cache, today=TODAY, connect=_connect({}), parsers=PARSERS)
    assert caplog.text.count("Mailbox folder mbank was not found") == 1
    _fetch(_Folder({10: _raw_mail("13.11.2026", _rows("10:00", "100,00", "900,00"))}), cache)
    assert "mbank" not in cache.missing


def test_changed_flag_marks_only_reads_that_alter_the_cache() -> None:
    folder = _Folder({10: _raw_mail("13.11.2026", _rows("10:00", "100,00", "900,00"))})
    cache = MailboxCache()
    _fetch(folder, cache)
    assert cache.changed
    cache.changed = False
    _fetch(folder, cache)
    assert not cache.changed
    folder.mails[11] = _raw_mail("14.11.2026", _rows("09:00", "50,00", "850,00"))
    _fetch(folder, cache)
    assert cache.changed
