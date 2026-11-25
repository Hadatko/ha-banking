import json
from urllib.parse import parse_qs, urlparse

import pytest

from custom_components.ha_bank_watch.lib import (
    AirBank,
    AirBankApiError,
    ClientConfig,
    authorization_code_from_redirect,
    domestic_payment,
    sepa_payment,
)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers=None, body=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "body": body,
            }
        )
        if not self.responses:
            raise AssertionError(f"unexpected request {method} {url}")
        return self.responses.pop(0)


def response(status, payload=None, raw=b""):
    body = raw if payload is None else json.dumps(payload).encode()
    return type("HttpResponse", (), {"status": status, "body": body, "headers": {}})()


def client(transport, sandbox=False):
    return AirBank(ClientConfig(sandbox=sandbox), transport=transport)


def json_body(call):
    return json.loads(call["body"].decode())


def test_register_posts_application_to_production_oauth():
    transport = FakeTransport(
        [
            response(
                200,
                {
                    "client_id": "cid",
                    "client_secret": "sec",
                    "api_key": "key",
                },
            )
        ]
    )
    registered = client(transport).register(
        "HadiScript",
        ["https://ib.airbank.cz/paired"],
        scopes=["AISP"],
    )

    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.airbank.cz/oauth2/register"
    assert call["headers"]["Content-Type"] == "application/json"
    assert json_body(call) == {
        "client_name": "HadiScript",
        "redirect_uris": ["https://ib.airbank.cz/paired"],
        "scopes": ["AISP"],
    }
    assert registered.client_id == "cid"
    assert registered.client_secret == "sec"
    assert registered.api_key == "key"


def test_sandbox_register_uses_sandbox_prefix():
    transport = FakeTransport(
        [response(200, {"client_id": "a", "client_secret": "b", "api_key": "c"})]
    )
    client(transport, sandbox=True).register("App", ["https://example.com/back"])
    assert transport.calls[0]["url"] == "https://api.airbank.cz/sandbox/oauth2/register"


def test_register_rejects_unknown_scope():
    with pytest.raises(ValueError, match="AISP"):
        client(FakeTransport([])).register("App", ["https://example.com/back"], scopes=["KYC"])


def test_production_authorization_url_uses_internet_banking():
    url = client(FakeTransport([])).authorization_url(
        "MYID",
        "state-1",
        redirect_uri="https://myapp.example.com/callback",
    )
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://ib.airbank.cz/"
    assert parse_qs(parsed.query) == {
        "client_id": ["MYID"],
        "response_type": ["code"],
        "state": ["state-1"],
        "redirect_uri": ["https://myapp.example.com/callback"],
    }


def test_sandbox_authorization_url_uses_documented_parameter_name():
    url = client(FakeTransport([]), sandbox=True).authorization_url(
        "sandbox_client_id",
        "state-1",
        redirect_uri="https://www.airbank.cz",
    )
    parsed = urlparse(url)
    assert parsed.scheme == "http"
    assert parsed.netloc == "developers.airbank.cz"
    assert parsed.path == "/sandbox/login"
    assert parse_qs(parsed.query)["redirectUri"] == ["https://www.airbank.cz"]


def test_exchange_code_and_refresh_use_token_endpoint():
    transport = FakeTransport(
        [
            response(
                200,
                {
                    "access_token": "access-1",
                    "expires_in": 7200,
                    "refresh_token": "refresh-1",
                    "token_type": "bearer",
                },
            ),
            response(
                200,
                {
                    "access_token": "access-2",
                    "expires_in": 7200,
                    "token_type": "bearer",
                },
            ),
        ]
    )
    bank = client(transport)
    token = bank.exchange_code("cid", "sec", "auth-code", "https://myapp.example.com/callback")
    refreshed = bank.refresh("cid", "sec", token.refresh_token)

    assert json_body(transport.calls[0]) == {
        "grant_type": "authorization_code",
        "code": "auth-code",
        "redirect_uri": "https://myapp.example.com/callback",
        "client_id": "cid",
        "client_secret": "sec",
    }
    assert transport.calls[0]["url"] == "https://api.airbank.cz/oauth2/token"
    assert json_body(transport.calls[1])["grant_type"] == "refresh_token"
    assert json_body(transport.calls[1])["refresh_token"] == "refresh-1"
    assert refreshed.access_token == "access-2"
    assert refreshed.refresh_token is None


def test_redirect_rejects_mismatched_state():
    with pytest.raises(ValueError, match="state"):
        authorization_code_from_redirect(
            "https://myapp.example.com/callback?code=abc&state=other",
            "expected",
        )


def test_redirect_returns_authorization_code():
    code = authorization_code_from_redirect(
        "https://myapp.example.com/callback?code=abc&state=expected",
        "expected",
    )
    assert code == "abc"


def test_list_accounts_uses_aisp_v7_and_bearer_token():
    transport = FakeTransport(
        [
            response(
                200,
                {
                    "pageNumber": "0",
                    "pageCount": "1",
                    "pageSize": "3",
                    "accounts": [
                        {
                            "id": "10037188",
                            "identification": {
                                "iban": "CZ4130300000001018074010",
                                "other": "1018074010/3030",
                            },
                            "currency": "CZK",
                            "servicer": {
                                "bankCode": "3030",
                                "countryCode": "CZ",
                                "bic": "AIRACZPP",
                            },
                            "nameI18N": "Spořící účet v CZK",
                            "productI18N": "Spořící účet",
                            "ownersNames": ["Karel Spořivka"],
                        }
                    ],
                },
            )
        ]
    )
    page = client(transport).list_accounts("token-1", page=0, size=3)
    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://api.airbank.cz/openapi/accountInfo/v7/my/accounts?size=3&page=0"
    assert call["headers"]["Authorization"] == "Bearer token-1"
    assert page.accounts[0].id == "10037188"
    assert page.accounts[0].identification.iban == "CZ4130300000001018074010"
    assert page.page_number == 0


def test_sandbox_accounts_omit_openapi_prefix():
    transport = FakeTransport([response(200, {"accounts": []})])
    client(transport, sandbox=True).list_accounts("token-1")
    assert (
        transport.calls[0]["url"]
        == "https://api.airbank.cz/sandbox/accountInfo/v7/my/accounts"
    )


def test_balances_and_transactions_use_account_paths():
    transport = FakeTransport(
        [
            response(
                200,
                {
                    "balances": [
                        {
                            "type": {"codeOrProprietary": {"code": "CLAV"}},
                            "creditLine": {"included": False},
                            "amount": {"value": 10.5, "currency": "CZK"},
                            "creditDebitIndicator": "CRDT",
                            "date": {"dateTime": "2018-03-20T15:34:46Z"},
                        }
                    ]
                },
            ),
            response(
                200,
                {
                    "transactions": [
                        {
                            "entryReference": "RB-1",
                            "amount": {"value": 20, "currency": "CZK"},
                            "creditDebitIndicator": "DBIT",
                            "status": "BOOK",
                        }
                    ]
                },
            ),
        ]
    )
    bank = client(transport)
    balances = bank.list_balances("token-1", "10000411")
    transactions = bank.list_transactions(
        "token-1",
        "10000411",
        from_date="2026-01-01",
        to_date="2026-02-01",
        sort="valueDate",
        order="DESC",
        page=1,
        size=50,
    )

    assert balances[0].amount.value == 10.5
    assert balances[0].type_code == "CLAV"
    assert transactions.transactions[0].entry_reference == "RB-1"
    assert (
        transport.calls[0]["url"]
        == "https://api.airbank.cz/openapi/accountInfo/v7/my/accounts/10000411/balance"
    )
    parsed = urlparse(transport.calls[1]["url"])
    assert parsed.path == "/openapi/accountInfo/v7/my/accounts/10000411/transactions"
    assert parse_qs(parsed.query) == {
        "fromDate": ["2026-01-01"],
        "toDate": ["2026-02-01"],
        "sort": ["valueDate"],
        "order": ["DESC"],
        "page": ["1"],
        "size": ["50"],
    }


def test_api_error_exposes_bank_error_code():
    transport = FakeTransport(
        [
            response(
                404,
                {"errors": [{"error": "ID_NOT_FOUND", "message": "missing account"}]},
            )
        ]
    )
    with pytest.raises(AirBankApiError) as caught:
        client(transport).list_balances("token-1", "missing")
    assert caught.value.status == 404
    assert caught.value.codes == ["ID_NOT_FOUND"]


def test_balance_check_uses_cisp_v3_and_api_key_header():
    transport = FakeTransport(
        [
            response(
                200,
                {
                    "authorization": {
                        "responseIdentification": "987654",
                        "exchangeIdentification": "123456",
                        "response": "APPR",
                    }
                },
            )
        ]
    )
    result = client(transport).check_funds(
        "api-key-1",
        exchange_identification="123456",
        iban="CZ0708000000001019382023",
        currency="CZK",
        amount=20,
    )
    call = transport.calls[0]
    assert call["url"] == "https://api.airbank.cz/openapi/balanceCheck/v3/accounts/balanceCheck"
    assert call["headers"]["Api-key"] == "api-key-1"
    assert json_body(call)["transactionDetails"] == {"currency": "CZK", "totalAmount": 20}
    assert result == "APPR"


def test_domestic_payment_is_posted_to_pisp_v7():
    transport = FakeTransport(
        [
            response(
                200,
                {
                    "transactionIdentification": "100010771",
                    "serviceLevel": {"code": "DOMESTIC"},
                    "signInfo": {"state": "IN_PROGRESS", "signId": "sign-1", "signInfo": "ACTC"},
                },
            )
        ]
    )
    payment = domestic_payment(
        instruction_id="pay-1",
        amount=1048.64,
        currency="CZK",
        debtor_iban="CZ7508000000002108589434",
        creditor_iban="CZ6330300000000000000123",
        variable_symbol="5010000000",
        instant=True,
    )
    created = client(transport).create_payment("token-1", payment)
    body = json_body(transport.calls[0])
    assert transport.calls[0]["url"] == "https://api.airbank.cz/openapi/paymentInit/v7/my/payments"
    assert body["paymentTypeInformation"]["instructionPriority"] == "INST"
    assert body["remittanceInformation"]["structured"]["creditorReferenceInformation"]["reference"] == [
        "VS:5010000000"
    ]
    assert created.transaction_identification == "100010771"
    assert created.sign_id == "sign-1"


def test_sepa_payment_includes_structured_postal_address():
    payload = sepa_payment(
        instruction_id="SEPAFUL123",
        amount=10,
        currency="EUR",
        debtor_iban="CZ7830300000001000000013",
        creditor_iban="AT033363343184694213",
        creditor_name="1. Wien investment",
        town_name="Vienna",
        country="AT",
        street_name="Reisnerstrasse",
        creditor_bic="RZBAATWWXXX",
    )
    address = payload["creditor"]["postalAddress"]
    assert address["townName"] == "Vienna"
    assert address["country"] == "AT"
    assert address["streetName"] == "Reisnerstrasse"
    assert payload["creditorAgent"]["financialInstitutionIdentification"]["bic"] == "RZBAATWWXXX"


def test_payment_status_and_cancel():
    transport = FakeTransport(
        [
            response(200, {"instructionStatus": "ACTC"}),
            response(204, raw=b""),
        ]
    )
    bank = client(transport)
    assert bank.payment_status("token-1", "pay-9").instruction_status == "ACTC"
    bank.cancel_payment("token-1", "pay-9")
    assert transport.calls[0]["url"].endswith("/openapi/paymentInit/v7/my/payments/pay-9/status")
    assert transport.calls[1]["method"] == "DELETE"
    assert transport.calls[1]["url"].endswith("/openapi/paymentInit/v7/my/payments/pay-9")


def test_client_construction_does_not_contact_the_bank():
    bank = AirBank(ClientConfig())
    assert bank.config.base_url == "https://api.airbank.cz"


def test_revoke_sends_form_encoded_token():
    transport = FakeTransport([response(200, raw=b"")])
    client(transport).revoke("refresh-1")
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.airbank.cz/oauth2/token/revoke"
    assert call["headers"]["Content-Type"] == "application/x-www-form-urlencoded;charset=UTF-8"
    assert call["body"] == b"token=refresh-1"


def test_sandbox_token_exchange_uses_sandbox_oauth():
    transport = FakeTransport(
        [response(200, {"access_token": "a", "expires_in": 60, "token_type": "bearer"})]
    )
    client(transport, sandbox=True).exchange_code("sandbox_client_id", "sandbox_client_secret", "code", "https://www.airbank.cz")
    assert transport.calls[0]["url"] == "https://api.airbank.cz/sandbox/oauth2/token"


def test_remaining_resources_use_current_versions():
    transport = FakeTransport(
        [
            response(200, {"client_id": "cid", "client_secret": "new", "api_key": "key"}),
            response(200, {"transactionIdentification": "order-1"}),
            response(200, {"scenarios": [], "signInfo": {"state": "IN_PROGRESS"}}),
            response(200, {"response": "APPR"}),
        ]
    )
    bank = client(transport)
    renewed = bank.renew_secret("cid")
    bank.create_standing_order("token-1", {"instructionIdentification": "order-1"})
    bank.payment_authorization("token-1", "pay-1", "sign-1")
    bank.check_payment_funds(
        "token-1",
        {
            "exchangeIdentification": "1",
            "debtorAccount": {"identification": {"iban": "CZ0708000000001019382023"}},
        },
    )

    assert renewed.client_secret == "new"
    assert transport.calls[0]["url"] == "https://api.airbank.cz/oauth2/register/cid/renewSecret"
    assert transport.calls[1]["url"] == "https://api.airbank.cz/openapi/paymentInit/v7/my/standingorders"
    assert transport.calls[2]["url"] == "https://api.airbank.cz/openapi/paymentInit/v7/my/payments/pay-1/sign/sign-1"
    assert transport.calls[3]["url"] == "https://api.airbank.cz/openapi/paymentInit/v7/my/payments/balanceCheck"


def test_standing_orders_are_listed_from_account_info():
    transport = FakeTransport([response(200, {"standingOrders": []})])
    orders = client(transport).list_standing_orders("token-1")
    assert orders == []
    assert (
        transport.calls[0]["url"]
        == "https://api.airbank.cz/openapi/accountInfo/v7/my/standingorders"
    )
