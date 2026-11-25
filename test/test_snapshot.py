from datetime import date

from custom_components.ha_bank_watch.snapshot import (
    bank_logo_url,
    build_account_snapshot,
    collect_account_snapshots,
)
from custom_components.ha_bank_watch.lib import AirBank, ClientConfig


def _account():
    return {
        "id": "10037188",
        "identification": {"iban": "CZ4130300000001018074010", "other": "1018074010/3030"},
        "currency": "CZK",
        "nameI18N": "Main account",
        "productI18N": "Current account",
    }


def _balance(code, value, indicator):
    return {
        "type": {"codeOrProprietary": {"code": code}},
        "creditLine": {"included": False},
        "amount": {"value": value, "currency": "CZK"},
        "creditDebitIndicator": indicator,
        "date": {"dateTime": "2026-11-21T10:00:00Z"},
    }


def _transaction(indicator, value, name_party, status="BOOK"):
    party_key = "debtor" if indicator == "CRDT" else "creditor"
    return {
        "entryReference": f"ref-{indicator}",
        "amount": {"value": value, "currency": "CZK"},
        "creditDebitIndicator": indicator,
        "status": status,
        "bookingDate": {"date": "2026-11-20"},
        "valueDate": {"date": "2026-11-20"},
        "entryDetails": {
            "transactionDetails": {
                "relatedParties": {party_key: {"name": name_party}},
                "remittanceInformation": {
                    "unstructured": "rent",
                    "structured": {
                        "creditorReferenceInformation": {"reference": ["VS:501", "KS:9"]}
                    },
                },
                "additionalTransactionInformation": "note",
            }
        },
    }


def test_snapshot_splits_balances_and_payment_directions():
    snapshot = build_account_snapshot(
        _account(),
        [_balance("PRCD", 1500, "CRDT"), _balance("CLAV", 20, "DBIT")],
        [
            _transaction("DBIT", 40, "Shop"),
            _transaction("CRDT", 100, "Employer"),
        ],
    )

    assert snapshot.account_id == "10037188"
    assert snapshot.name == "Main account"
    assert snapshot.booked_balance == 1500
    assert snapshot.logo_url == "https://enablebanking.com/brands/CZ/Air%20Bank/-/resize/128x/"
    assert snapshot.available_balance == -20
    assert snapshot.latest_incoming.amount == 100
    assert snapshot.latest_incoming.counterparty == "Employer"
    assert snapshot.latest_incoming.reference == "VS:501, KS:9"
    assert snapshot.latest_outgoing.amount == 40
    assert snapshot.latest_outgoing.counterparty == "Shop"
    assert snapshot.latest_status == "BOOK"
    readings = snapshot.readings()
    assert readings["booked_balance"].value == 1500
    assert readings["incoming_payment"].attributes["counterparty"] == "Employer"
    assert readings["outgoing_payment"].currency == "CZK"
    assert readings["transaction_status"].value == "BOOK"
    assert readings["transaction_status"].currency is None


def test_period_sums_follow_the_calendar_month_and_year():
    last_year = _transaction("DBIT", 4, "Last year")
    last_year["bookingDate"] = {"date": "2025-11-01"}
    january = _transaction("CRDT", 10, "January")
    january["bookingDate"] = {"date": "2026-01-15"}
    snapshot = build_account_snapshot(
        _account(),
        [_balance("PRCD", 1500, "CRDT")],
        [
            last_year,
            january,
            _transaction("CRDT", 100, "Employer"),
            _transaction("DBIT", 40, "Shop"),
        ],
        today=date(2026, 11, 21),
    )
    assert snapshot.booked_balance == 1500
    assert snapshot.month_incoming == 100
    assert snapshot.month_outgoing == 40
    assert snapshot.month_net == 60
    assert snapshot.year_incoming == 110
    assert snapshot.year_outgoing == 40
    assert snapshot.year_net == 70


def test_snapshot_ignores_missing_optional_payment_details():
    snapshot = build_account_snapshot(
        _account(),
        [],
        [{"amount": {"value": 5, "currency": "CZK"}, "creditDebitIndicator": "CRDT"}],
    )
    assert snapshot.booked_balance is None
    assert snapshot.latest_incoming.counterparty is None
    assert snapshot.latest_outgoing is None
    assert snapshot.latest_status is None


class _Response:
    def __init__(self, payload):
        import json

        self.status = 200
        self.body = json.dumps(payload).encode()
        self.headers = {}


class _Transport:
    def __init__(self):
        self.urls = []

    def request(self, method, url, headers=None, body=None):
        self.urls.append(url)
        path = url.split("?", 1)[0]
        if path.endswith("/accounts"):
            return _Response({"accounts": [_account()]})
        if path.endswith("/balance"):
            return _Response({"balances": [_balance("PRCD", 10, "CRDT")]})
        if path.endswith("/transactions"):
            return _Response({"transactions": [_transaction("CRDT", 10, "Employer")]})
        raise AssertionError(url)


def test_bank_logo_uses_the_enable_banking_brand_image():
    assert bank_logo_url("Air Bank", "cz") == "https://enablebanking.com/brands/CZ/Air%20Bank/-/resize/128x/"
    assert bank_logo_url("Fio banka", "SK") == "https://enablebanking.com/brands/SK/Fio%20banka/-/resize/128x/"
    assert bank_logo_url("  ", "CZ") is None


def test_collection_reads_accounts_only():
    transport = _Transport()
    bank = AirBank(ClientConfig(), transport=transport)
    snapshots = collect_account_snapshots(bank, "token")

    assert snapshots[0].booked_balance == 10
    assert snapshots[0].latest_incoming.counterparty == "Employer"
    assert all("/paymentInit/" not in url for url in transport.urls)
    assert all("/balanceCheck/" not in url for url in transport.urls)
