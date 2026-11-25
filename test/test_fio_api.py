"""Fio API Bankovnictví. Read-only, with a stand-in transport."""

from datetime import date
import json

from custom_components.ha_bank_watch.const import AuthMethod
from custom_components.ha_bank_watch.csas import bank_api_menu
from custom_components.ha_bank_watch.fio import Fio, FioError, collect_fio_snapshots, fio_unique_id, read_start
from custom_components.ha_bank_watch.lib.http import HttpResponse


class FakeTransport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, headers: dict | None = None, body: bytes | None = None) -> HttpResponse:
        self.calls.append((method, url))
        return self.response


def _statement() -> dict:
    return {
        "accountStatement": {
            "info": {
                "accountId": "2400222222",
                "bankId": "2010",
                "currency": "CZK",
                "iban": "CZ7920100000002400222222",
                "closingBalance": 195.01,
            },
            "transactionList": {
                "transaction": [
                    {
                        "column22": {"value": 10, "name": "ID pohybu", "id": 22},
                        "column0": {"value": 1340661600000, "name": "Datum", "id": 0},
                        "column1": {"value": 1.0, "name": "Objem", "id": 1},
                        "column14": {"value": "CZK", "name": "Měna", "id": 14},
                        "column10": {"value": "Counterparty", "name": "Název protiúčtu", "id": 10},
                        "column5": {"value": "0001", "name": "VS", "id": 5},
                    },
                    {
                        "column22": {"value": 11, "name": "ID pohybu", "id": 11},
                        "column0": {"value": "2026-11-20+0100", "name": "Datum", "id": 0},
                        "column1": {"value": -15.0, "name": "Objem", "id": 1},
                        "column14": {"value": "CZK", "name": "Měna", "id": 14},
                        "column2": {"value": "2600044444", "name": "Protiúčet", "id": 2},
                        "column3": {"value": "2010", "name": "Kód banky", "id": 3},
                        "column16": {"value": "Rent", "name": "Zpráva pro příjemce", "id": 16},
                    },
                ]
            },
        }
    }


def test_read_window_stays_inside_the_published_limit() -> None:
    assert read_start(date(2026, 1, 15)) == date(2026, 1, 1)
    assert read_start(date(2026, 11, 22)) == date(2026, 8, 25)


def test_periods_request_reads_transactions_and_skips_payment_import() -> None:
    transport = FakeTransport(HttpResponse(200, json.dumps(_statement()).encode(), {}))
    bank = Fio(transport)
    snapshots = collect_fio_snapshots(bank, "read-token", date(2026, 11, 22))
    method, url = transport.calls[0]
    assert method == "GET"
    assert url == "https://fioapi.fio.cz/v1/rest/periods/read-token/2026-08-25/2026-11-22/transactions.json"
    assert "/import/" not in url
    assert len(transport.calls) == 1
    account = snapshots[0]
    assert fio_unique_id(account.account_id) == "fio:2400222222/2010"
    assert account.institution == "Fio banka"
    assert account.configuration_url == "https://ib.fio.cz/"
    assert account.booked_balance == 195.01
    assert account.available_balance is None
    assert account.latest_outgoing is not None
    assert account.latest_outgoing.amount == 15.0
    assert account.latest_outgoing.counterparty == "2600044444/2010"
    assert account.latest_outgoing.message == "Rent"
    assert account.latest_outgoing.booking_date == "2026-11-20"
    assert account.latest_incoming is not None
    assert account.latest_incoming.booking_date == "2012-06-26"
    assert account.latest_incoming.counterparty == "Counterparty"
    assert account.latest_incoming.reference == "0001"


def test_inactive_token_is_reported_by_status_only() -> None:
    transport = FakeTransport(HttpResponse(500, b"", {}))
    try:
        Fio(transport).statement("secret-token", date(2026, 11, 22))
    except FioError as err:
        assert err.status == 500
        assert "secret-token" not in str(err)
    else:
        raise AssertionError("expected FioError")


def test_bank_api_menu_includes_fio() -> None:
    assert bank_api_menu() == (AuthMethod.DIRECT, AuthMethod.CSAS, AuthMethod.FIO)
