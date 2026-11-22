"""Enable Banking account mapping without network access."""

from datetime import datetime, timezone
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import pytest

from custom_components.ha_banking.lib.enable_banking import (
    EnableBanking,
    EnableBankingSessionExpired,
    account_record,
    begin_air_bank_authorization,
    begin_authorization,
    collect_enable_banking_snapshots,
    consent_valid_until,
    find_air_bank,
    finish_authorization,
    private_key_ready,
)
from custom_components.ha_banking.lib.http import HttpResponse
from custom_components.ha_banking.snapshot import build_enable_banking_snapshot


def _private_key(tmp_path: Path) -> Path:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "enablebanking.pem"
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return path


class FakeTransport:
    def __init__(self, responder):
        self.calls = []
        self._responder = responder

    def request(self, method, url, headers=None, body=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        status, payload = self._responder(method, url, body)
        return HttpResponse(status, json.dumps(payload).encode(), {})


def test_private_key_must_be_able_to_sign(tmp_path: Path):
    missing = tmp_path / "missing.pem"
    text = tmp_path / "notes.txt"
    text.write_text("not a key")
    assert private_key_ready(str(missing)) is False
    assert private_key_ready(str(text)) is False
    assert private_key_ready(str(_private_key(tmp_path))) is True


def test_find_air_bank_uses_the_czech_name():
    bank = find_air_bank(
        [
            {"name": "Nordea", "country": "FI"},
            {"name": "Air Bank", "country": "CZ", "maximum_consent_validity": 7776000},
        ]
    )
    assert bank["name"] == "Air Bank"
    with pytest.raises(LookupError):
        find_air_bank([{"name": "Fio banka", "country": "CZ"}])


def test_consent_stays_inside_the_bank_maximum():
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    valid_until = consent_valid_until({"maximum_consent_validity": 3600}, now)
    assert valid_until.startswith("2026-11-21T00:59:00")


def test_account_record_keeps_accounts_that_can_be_polled():
    assert account_record({"name": "hidden"}) is None
    record = account_record(
        {
            "uid": "account-1",
            "details": "Main",
            "account_id": {"iban": "CZ6508000000192000145399"},
            "currency": "CZK",
            "product": "Current account",
        }
    )
    assert record == {
        "uid": "account-1",
        "name": "Main",
        "iban": "CZ6508000000192000145399",
        "currency": "CZK",
        "product": "Current account",
    }


def test_authorization_requests_balances_and_transactions_only(tmp_path: Path):
    def responder(method, url, body):
        if url.startswith("https://api.enablebanking.com/aspsps"):
            return 200, {
                "aspsps": [
                    {"name": "Air Bank", "country": "CZ", "maximum_consent_validity": 86400}
                ]
            }
        payload = json.loads(body)
        assert payload["access"]["balances"] is True
        assert payload["access"]["transactions"] is True
        assert "payments" not in payload
        assert payload["aspsp"] == {"name": "Air Bank", "country": "CZ"}
        assert payload["psu_type"] == "personal"
        assert payload["redirect_url"] == "https://my.home-assistant.io/redirect/oauth"
        return 200, {"url": "https://auth.enablebanking.com/ais/start"}

    bank = EnableBanking("app-1", str(_private_key(tmp_path)), transport=FakeTransport(responder))
    url = begin_air_bank_authorization(
        bank,
        redirect_url="https://my.home-assistant.io/redirect/oauth",
        state="signed-state",
        psu_type="personal",
    )
    assert url == "https://auth.enablebanking.com/ais/start"


def test_authorization_uses_the_selected_bank(tmp_path: Path):
    def responder(method, url, body):
        if url.startswith("https://api.enablebanking.com/aspsps"):
            assert "country=SK" in url
            return 200, {
                "aspsps": [
                    {"name": "Air Bank", "country": "CZ", "maximum_consent_validity": 86400},
                    {"name": "Fio banka", "country": "SK", "maximum_consent_validity": 86400},
                ]
            }
        payload = json.loads(body)
        assert payload["aspsp"] == {"name": "Fio banka", "country": "SK"}
        assert payload["language"] == "sk"
        return 200, {"url": "https://auth.enablebanking.com/ais/fio"}

    bank = EnableBanking("app-1", str(_private_key(tmp_path)), transport=FakeTransport(responder))
    url = begin_authorization(
        bank,
        aspsp_name="Fio banka",
        country="SK",
        redirect_url="https://home.example.com/auth/external/callback",
        state="signed-state",
        psu_type="personal",
    )
    assert url == "https://auth.enablebanking.com/ais/fio"


def test_finish_authorization_stores_readable_accounts(tmp_path: Path):
    def responder(method, url, body):
        assert json.loads(body) == {"code": "auth-code"}
        return 200, {
            "session_id": "session-1",
            "access": {"valid_until": "2026-12-21T00:00:00+00:00"},
            "accounts": [
                {"name": "Blocked"},
                {
                    "uid": "account-1",
                    "details": "Savings",
                    "account_id": {"iban": "CZ00"},
                    "currency": "CZK",
                    "cash_account_type": "SVGS",
                },
            ],
        }

    bank = EnableBanking("app-1", str(_private_key(tmp_path)), transport=FakeTransport(responder))
    finished = finish_authorization(bank, "auth-code")
    assert finished["session_id"] == "session-1"
    assert finished["accounts"] == [
        {
            "uid": "account-1",
            "name": "Savings",
            "iban": "CZ00",
            "currency": "CZK",
            "product": "SVGS",
        }
    ]


def test_collect_uses_session_balances_and_transactions(tmp_path: Path):
    clock = lambda: datetime(2026, 11, 21, tzinfo=timezone.utc)

    def responder(method, url, body):
        if url.endswith("/sessions/session-1"):
            return 200, {"status": "AUTHORIZED"}
        if url.endswith("/balances"):
            return 200, {
                "balances": [
                    {"balance_type": "CLBD", "balance_amount": {"amount": "1500.50", "currency": "CZK"}},
                    {"balance_type": "CLAV", "balance_amount": {"amount": "1400.00", "currency": "CZK"}},
                ]
            }
        assert "date_from=2026-01-01" in url
        return 200, {
            "transactions": [
                {
                    "credit_debit_indicator": "DBIT",
                    "transaction_amount": {"amount": "40.00", "currency": "CZK"},
                    "creditor": {"name": "Shop"},
                    "booking_date": "2026-11-01",
                    "status": "BOOK",
                    "remittance_information": ["rent"],
                    "reference_number": "VS:501",
                    "entry_reference": "out-1",
                },
                {
                    "credit_debit_indicator": "CRDT",
                    "transaction_amount": {"amount": "10.00", "currency": "CZK"},
                    "debtor": {"name": "Old employer"},
                    "booking_date": "2026-10-01",
                    "status": "BOOK",
                },
                {
                    "credit_debit_indicator": "CRDT",
                    "transaction_amount": {"amount": "100.00", "currency": "CZK"},
                    "debtor": {"name": "Employer"},
                    "booking_date": "2026-11-20",
                    "status": "PDNG",
                    "entry_reference": "in-2",
                },
            ]
        }

    transport = FakeTransport(responder)
    bank = EnableBanking(
        "app-1",
        str(_private_key(tmp_path)),
        transport=transport,
        clock=clock,
    )
    snapshots = collect_enable_banking_snapshots(
        bank,
        "session-1",
        [
            {
                "uid": "account-1",
                "name": "Main",
                "iban": "CZ00",
                "currency": "CZK",
                "product": "Current account",
            }
        ],
    )
    snapshot = snapshots[0]
    token = transport.calls[0]["headers"]["Authorization"].removeprefix("Bearer ")
    assert jwt.get_unverified_header(token)["kid"] == "app-1"
    assert {call["url"].split("?")[0] for call in transport.calls} == {
        "https://api.enablebanking.com/sessions/session-1",
        "https://api.enablebanking.com/accounts/account-1/balances",
        "https://api.enablebanking.com/accounts/account-1/transactions",
    }
    assert snapshot.booked_balance == 1500.50
    assert snapshot.available_balance == 1400.00
    assert snapshot.latest_incoming.amount == 100
    assert snapshot.latest_incoming.counterparty == "Employer"
    assert snapshot.latest_outgoing.counterparty == "Shop"
    assert snapshot.latest_outgoing.reference == "VS:501"
    assert snapshot.latest_status == "PDNG"
    assert snapshot.month_incoming == 100
    assert snapshot.month_outgoing == 40
    assert snapshot.month_net == 60
    assert snapshot.year_incoming == 110
    assert snapshot.year_outgoing == 40
    assert snapshot.year_net == 70


def test_expired_session_asks_for_a_new_login(tmp_path: Path):
    def responder(method, url, body):
        return 200, {"status": "EXPIRED"}

    bank = EnableBanking("app-1", str(_private_key(tmp_path)), transport=FakeTransport(responder))
    with pytest.raises(EnableBankingSessionExpired):
        collect_enable_banking_snapshots(bank, "session-1", [])


def test_snapshot_prefers_booked_and_available_balance_types():
    snapshot = build_enable_banking_snapshot(
        {"uid": "1", "name": "Main", "iban": "CZ00", "currency": "CZK", "product": "Current"},
        [
            {"balance_type": "PRCD", "balance_amount": {"amount": "1"}},
            {"balance_type": "CLBD", "balance_amount": {"amount": "9"}},
            {"balance_type": "ITAV", "balance_amount": {"amount": "8"}},
            {"balance_type": "CLAV", "balance_amount": {"amount": "7"}},
        ],
        [],
    )
    assert snapshot.booked_balance == 9
    assert snapshot.available_balance == 7
    assert snapshot.logo_url == "https://enablebanking.com/brands/CZ/Air%20Bank/-/resize/128x/"
    fio = build_enable_banking_snapshot(
        {"uid": "1", "name": "Main", "iban": "SK00", "currency": "EUR", "product": "Current"},
        [],
        [],
        institution="Fio banka",
        country="SK",
    )
    assert fio.institution == "Fio banka"
    assert fio.logo_url == "https://enablebanking.com/brands/SK/Fio%20banka/-/resize/128x/"
