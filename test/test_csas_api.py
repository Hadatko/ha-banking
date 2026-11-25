"""Česká spořitelna Premium Accounts API. Read-only, with a stand-in transport."""

from datetime import date
from urllib.parse import parse_qs, urlparse

from custom_components.ha_bank_watch.const import AuthMethod
from custom_components.ha_bank_watch.csas import (
    CeskaSporitelna,
    CeskaSporitelnaError,
    authorization_url,
    bank_api_menu,
    collect_csas_snapshots,
    sign_in_menu,
)
from custom_components.ha_bank_watch.lib.http import HttpResponse


class FakeTransport:
    def __init__(self, routes: dict[tuple[str, str], HttpResponse]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, dict, bytes | None]] = []

    def request(self, method: str, url: str, headers: dict | None = None, body: bytes | None = None) -> HttpResponse:
        self.calls.append((method, url, headers or {}, body))
        key = (method, url.split("?", 1)[0])
        if key not in self.routes:
            raise AssertionError(f"unexpected {method} {url}")
        return self.routes[key]


def test_sign_in_menu_keeps_three_methods_and_bank_api_offers_both_banks() -> None:
    assert sign_in_menu() == (AuthMethod.EMAIL, AuthMethod.ENABLE_BANKING, AuthMethod.BANK_API)
    assert bank_api_menu() == (AuthMethod.DIRECT, AuthMethod.CSAS, AuthMethod.FIO)


def test_authorization_url_asks_only_for_account_access() -> None:
    sandbox = authorization_url(
        sandbox=True,
        client_id="app",
        redirect_uri="https://ha.example/auth/external/callback",
        state="signed-state",
    )
    production = authorization_url(
        sandbox=False,
        client_id="app",
        redirect_uri="https://ha.example/auth/external/callback",
        state="signed-state",
    )
    sandbox_query = parse_qs(urlparse(sandbox).query)
    assert sandbox.startswith("https://webapi.developers.erstegroup.com/api/csas/sandbox/v1/sandbox-idp/auth?")
    assert production.startswith("https://bezpecnost.csas.cz/api/psd2/fl/oidc/v1/auth?")
    assert sandbox_query["scope"] == ["siblings.accounts"]
    assert sandbox_query["response_type"] == ["code"]
    assert sandbox_query["access_type"] == ["offline"]
    assert "siblings.payments" not in sandbox


def test_code_exchange_and_refresh_use_the_documented_token_endpoint() -> None:
    transport = FakeTransport(
        {
            ("POST", "https://webapi.developers.erstegroup.com/api/csas/sandbox/v1/sandbox-idp/token"): HttpResponse(
                200,
                b'{"access_token":"access","refresh_token":"refresh","expires_in":300}',
                {},
            )
        }
    )
    bank = CeskaSporitelna(
        sandbox=True,
        client_id="app",
        client_secret="secret",
        web_api_key="key",
        transport=transport,
    )
    tokens = bank.exchange_code("the-code", "https://ha.example/auth/external/callback")
    assert tokens["refresh_token"] == "refresh"
    body = parse_qs(transport.calls[0][3].decode())
    assert body["grant_type"] == ["authorization_code"]
    assert body["code"] == ["the-code"]
    assert body["client_id"] == ["app"]
    refreshed = bank.refresh("refresh")
    refresh_body = parse_qs(transport.calls[1][3].decode())
    assert refresh_body["grant_type"] == ["refresh_token"]
    assert refresh_body["refresh_token"] == ["refresh"]
    assert refreshed["access_token"] == "access"


def test_snapshots_read_balances_and_payments_without_starting_a_payment() -> None:
    account_id = "AA195E7DB499B4D9F48D46C208625FF53F2245F7"
    accounts = (
        "https://webapi.developers.erstegroup.com/api/csas/public/sandbox/v3/accounts/my/accounts"
    )
    balance = f"{accounts}/{account_id}/balance"
    transactions = f"{accounts}/{account_id}/transactions"
    transport = FakeTransport(
        {
            ("GET", accounts): HttpResponse(
                200,
                (
                    '{"pageNumber":0,"pageCount":1,"nextPage":null,"accounts":[{'
                    f'"id":"{account_id}",'
                    '"identification":{"iban":"CZ1208000000000259459101","other":"259459101"},'
                    '"currency":"CZK","nameI18N":"Main account","productI18N":"Personal account"}]}'
                ).encode(),
                {},
            ),
            ("GET", balance): HttpResponse(
                200,
                (
                    '{"balances":['
                    '{"type":{"codeOrProprietary":{"code":"PRCD"}},"amount":{"value":1000.50,"currency":"CZK"},'
                    '"creditDebitIndicator":"CRDT"},'
                    '{"type":{"codeOrProprietary":{"code":"CLAV"}},"amount":{"value":900.25,"currency":"CZK"},'
                    '"creditDebitIndicator":"CRDT"}]}'
                ).encode(),
                {},
            ),
            ("GET", transactions): HttpResponse(
                200,
                (
                    '{"nextPage":null,"transactions":[{'
                    '"entryReference":"in-1","amount":{"value":20.00,"currency":"CZK"},'
                    '"creditDebitIndicator":"CRDT","status":"BOOKED",'
                    '"bookingDate":{"date":"2026-11-20"},'
                    '"entryDetails":{"transactionDetails":{"relatedParties":{"debtor":{"name":"Sender"}},'
                    '"remittanceInformation":{"unstructured":"Invoice"}}}},'
                    '{"entryReference":"out-1","amount":{"value":15.00,"currency":"CZK"},'
                    '"creditDebitIndicator":"DBIT","status":"BOOKED",'
                    '"bookingDate":{"date":"2026-11-21"},'
                    '"entryDetails":{"transactionDetails":{"relatedParties":{"creditor":{"name":"Shop"}}}}}]}'
                ).encode(),
                {},
            ),
        }
    )
    bank = CeskaSporitelna(
        sandbox=True,
        client_id="app",
        client_secret="secret",
        web_api_key="web-key",
        transport=transport,
    )
    (snapshot,) = collect_csas_snapshots(bank, "access", today=date(2026, 11, 22))
    assert snapshot.institution == "Česká spořitelna"
    assert snapshot.configuration_url == "https://george.csas.cz/"
    assert snapshot.name == "Main account"
    assert snapshot.booked_balance == 1000.50
    assert snapshot.available_balance == 900.25
    assert snapshot.latest_incoming is not None
    assert snapshot.latest_incoming.counterparty == "Sender"
    assert snapshot.latest_outgoing is not None
    assert snapshot.latest_outgoing.amount == 15.0
    assert snapshot.month_incoming == 20.0
    assert snapshot.month_outgoing == 15.0
    assert all(call[0] != "POST" or "payment" not in call[1] for call in transport.calls)
    assert transport.calls[0][2]["web-api-key"] == "web-key"
    assert transport.calls[0][2]["Authorization"] == "Bearer access"


def test_rejected_token_keeps_the_http_status() -> None:
    transport = FakeTransport(
        {
            ("POST", "https://www.csas.cz/webapi/api/v3/accounts"): HttpResponse(401, b"{}", {}),
            ("POST", "https://bezpecnost.csas.cz/api/psd2/fl/oidc/v1/token"): HttpResponse(401, b"{}", {}),
        }
    )
    bank = CeskaSporitelna(
        sandbox=False,
        client_id="app",
        client_secret="secret",
        web_api_key="key",
        transport=transport,
    )
    try:
        bank.refresh("expired")
    except CeskaSporitelnaError as err:
        assert err.status == 401
    else:
        raise AssertionError("refresh should fail")
