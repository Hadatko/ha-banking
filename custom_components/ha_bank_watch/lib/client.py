import json
from urllib.parse import parse_qs, quote, urlencode, urlparse

from .errors import AirBankApiError
from .http import Urllib3Transport
from .models import (
    AccountPage,
    Balance,
    ClientConfig,
    PaymentInitiation,
    PaymentStatus,
    RegisteredApplication,
    Token,
    TransactionPage,
)

SCOPES = ("AISP", "PISP", "CISP")
AISP_VERSION = "v7"
PISP_VERSION = "v7"
CISP_VERSION = "v3"


class AirBank:
    """Client for the current Air Bank Open API.

    Account information and payment initiation use Czech Open Banking Standard 7.0.
    The balance check still uses version 3, which is the current CISP contract.
    Calls other than the public directory require a PSD2 client certificate.
    """

    def __init__(self, config: ClientConfig | None = None, transport=None):
        self.config = config or ClientConfig()
        self.transport = transport or Urllib3Transport(self.config)

    def register(
        self,
        client_name: str,
        redirect_uris: list[str],
        scopes: list[str] | None = None,
    ) -> RegisteredApplication:
        selected = list(scopes or SCOPES)
        unknown = [scope for scope in selected if scope not in SCOPES]
        if unknown:
            raise ValueError(f"Scopes must be a subset of {', '.join(SCOPES)}")
        if not redirect_uris:
            raise ValueError("At least one redirect URI is required")
        payload = {
            "client_name": client_name,
            "redirect_uris": redirect_uris,
            "scopes": selected,
        }
        data = self._request("POST", f"{self._oauth_base()}/oauth2/register", json_body=payload)
        return RegisteredApplication.model_validate(data)

    def application_info(self, client_id: str) -> dict:
        data = self._request("GET", f"{self._oauth_base()}/oauth2/register/{_segment(client_id)}")
        return data or {}

    def renew_secret(self, client_id: str) -> RegisteredApplication:
        data = self._request(
            "POST",
            f"{self._oauth_base()}/oauth2/register/{_segment(client_id)}/renewSecret",
        )
        return RegisteredApplication.model_validate(data)

    def delete_application(self, client_id: str) -> None:
        self._request("DELETE", f"{self._oauth_base()}/oauth2/register/{_segment(client_id)}")

    def authorization_url(self, client_id: str, state: str, redirect_uri: str | None = None) -> str:
        if self.config.sandbox:
            query = {"state": state, "redirectUri": redirect_uri}
            return _with_query("http://developers.airbank.cz/sandbox/login", query)
        query = {
            "client_id": client_id,
            "response_type": "code",
            "state": state,
            "redirect_uri": redirect_uri,
        }
        return _with_query("https://ib.airbank.cz/", query)

    def exchange_code(self, client_id: str, client_secret: str, code: str, redirect_uri: str) -> Token:
        return self._token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            }
        )

    def refresh(self, client_id: str, client_secret: str, refresh_token: str) -> Token:
        return self._token(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            }
        )

    def revoke(self, token: str) -> None:
        self._request(
            "POST",
            f"{self._oauth_base()}/oauth2/token/revoke",
            form_body={"token": token},
        )

    def list_accounts(self, access_token: str, *, page: int | None = None, size: int | None = None) -> AccountPage:
        data = self._request(
            "GET",
            self._api(f"/accountInfo/{AISP_VERSION}/my/accounts", {"size": size, "page": page}),
            access_token=access_token,
        )
        return AccountPage.model_validate(data or {"accounts": []})

    def list_balances(self, access_token: str, account_id: str) -> list[Balance]:
        data = self._request(
            "GET",
            self._api(f"/accountInfo/{AISP_VERSION}/my/accounts/{_segment(account_id)}/balance"),
            access_token=access_token,
        )
        return [Balance.model_validate(item) for item in (data or {}).get("balances", [])]

    def list_transactions(
        self,
        access_token: str,
        account_id: str,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        page: int | None = None,
        size: int | None = None,
        sort: str | None = None,
        order: str | None = None,
    ) -> TransactionPage:
        data = self._request(
            "GET",
            self._api(
                f"/accountInfo/{AISP_VERSION}/my/accounts/{_segment(account_id)}/transactions",
                {
                    "fromDate": from_date,
                    "toDate": to_date,
                    "sort": sort,
                    "order": order,
                    "page": page,
                    "size": size,
                },
            ),
            access_token=access_token,
        )
        return TransactionPage.model_validate(data or {"transactions": []})

    def check_funds(
        self,
        api_key: str,
        *,
        exchange_identification: str,
        iban: str,
        currency: str,
        amount: float,
        transaction_currency: str | None = None,
    ) -> str:
        data = self._request(
            "POST",
            self._api(f"/balanceCheck/{CISP_VERSION}/accounts/balanceCheck"),
            api_key=api_key,
            json_body={
                "exchangeIdentification": exchange_identification,
                "debtorAccount": {
                    "identification": {"iban": iban},
                    "currency": currency,
                },
                "transactionDetails": {
                    "currency": transaction_currency or currency,
                    "totalAmount": amount,
                },
            },
        )
        return data["authorization"]["response"]

    def create_payment(self, access_token: str, payment: dict) -> PaymentInitiation:
        data = self._request(
            "POST",
            self._api(f"/paymentInit/{PISP_VERSION}/my/payments"),
            access_token=access_token,
            json_body=payment,
        )
        return PaymentInitiation.model_validate(data)

    def payment_detail(self, access_token: str, payment_id: str) -> dict:
        data = self._request(
            "GET",
            self._api(f"/paymentInit/{PISP_VERSION}/my/payments/{_segment(payment_id)}"),
            access_token=access_token,
        )
        return data or {}

    def payment_status(self, access_token: str, payment_id: str) -> PaymentStatus:
        data = self._request(
            "GET",
            self._api(f"/paymentInit/{PISP_VERSION}/my/payments/{_segment(payment_id)}/status"),
            access_token=access_token,
        )
        return PaymentStatus.model_validate(data)

    def payment_authorization(self, access_token: str, payment_id: str, sign_id: str) -> dict:
        data = self._request(
            "GET",
            self._api(
                f"/paymentInit/{PISP_VERSION}/my/payments/{_segment(payment_id)}/sign/{_segment(sign_id)}"
            ),
            access_token=access_token,
        )
        return data or {}

    def cancel_payment(self, access_token: str, payment_id: str) -> None:
        self._request(
            "DELETE",
            self._api(f"/paymentInit/{PISP_VERSION}/my/payments/{_segment(payment_id)}"),
            access_token=access_token,
        )

    def check_payment_funds(self, access_token: str, payload: dict) -> dict:
        data = self._request(
            "POST",
            self._api(f"/paymentInit/{PISP_VERSION}/my/payments/balanceCheck"),
            access_token=access_token,
            json_body=payload,
        )
        return data or {}

    def create_standing_order(self, access_token: str, order: dict) -> dict:
        data = self._request(
            "POST",
            self._api(f"/paymentInit/{PISP_VERSION}/my/standingorders"),
            access_token=access_token,
            json_body=order,
        )
        return data or {}

    def update_standing_order(self, access_token: str, order_id: str, order: dict) -> dict:
        data = self._request(
            "PUT",
            self._api(f"/paymentInit/{PISP_VERSION}/my/standingorders/{_segment(order_id)}"),
            access_token=access_token,
            json_body=order,
        )
        return data or {}

    def standing_order_status(self, access_token: str, order_id: str) -> dict:
        data = self._request(
            "GET",
            self._api(f"/paymentInit/{PISP_VERSION}/my/standingorders/{_segment(order_id)}/status"),
            access_token=access_token,
        )
        return data or {}

    def cancel_standing_order(self, access_token: str, order_id: str) -> None:
        self._request(
            "DELETE",
            self._api(f"/paymentInit/{PISP_VERSION}/my/standingorders/{_segment(order_id)}"),
            access_token=access_token,
        )

    def list_standing_orders(self, access_token: str) -> list:
        data = self._request(
            "GET",
            self._api(f"/accountInfo/{AISP_VERSION}/my/standingorders"),
            access_token=access_token,
        )
        return (data or {}).get("standingOrders", [])

    def standing_order_authorization(self, access_token: str, order_id: str, sign_id: str) -> dict:
        data = self._request(
            "GET",
            self._api(
                f"/paymentInit/{PISP_VERSION}/my/standingorders/{_segment(order_id)}/sign/{_segment(sign_id)}"
            ),
            access_token=access_token,
        )
        return data or {}

    def _token(self, payload: dict) -> Token:
        data = self._request("POST", f"{self._oauth_base()}/oauth2/token", json_body=payload)
        return Token.model_validate(data)

    def _oauth_base(self) -> str:
        if self.config.sandbox:
            return f"{self.config.base_url}/sandbox"
        return self.config.base_url

    def _api(self, path: str, query: dict | None = None) -> str:
        prefix = "/sandbox" if self.config.sandbox else "/openapi"
        return _with_query(f"{self.config.base_url}{prefix}{path}", query)

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict | None = None,
        form_body: dict | None = None,
        access_token: str | None = None,
        api_key: str | None = None,
    ):
        headers: dict[str, str] = {}
        body = None
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        if api_key:
            headers["Api-key"] = api_key
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(json_body).encode()
        elif form_body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
            body = urlencode(form_body).encode()
        response = self.transport.request(method, url, headers=headers, body=body)
        return _decode(response)


def authorization_code_from_redirect(redirect_url: str, expected_state: str) -> str:
    """Return the authorization code after checking the OAuth state."""
    query = parse_qs(urlparse(redirect_url).query)
    state = query.get("state", [None])[0]
    if state != expected_state:
        raise ValueError("OAuth state does not match the expected state")
    code = query.get("code", [None])[0]
    if not code:
        raise ValueError("OAuth redirect does not contain an authorization code")
    return code


def _with_query(url: str, query: dict | None) -> str:
    if not query:
        return url
    items = [(key, value) for key, value in query.items() if value is not None]
    if not items:
        return url
    return f"{url}?{urlencode(items)}"


def _segment(value: str) -> str:
    return quote(value, safe="._-")


def _decode(response):
    text = response.body.decode() if response.body else ""
    data = None
    if text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
    if response.status >= 400:
        codes: list[str] = []
        message = text or f"HTTP {response.status}"
        if isinstance(data, dict) and isinstance(data.get("errors"), list):
            codes = [item.get("error", "") for item in data["errors"] if isinstance(item, dict)]
            messages = [
                item.get("message")
                for item in data["errors"]
                if isinstance(item, dict) and item.get("message")
            ]
            if messages:
                message = "; ".join(messages)
        elif isinstance(data, dict) and data.get("error"):
            codes = [str(data["error"])]
            message = str(data.get("error_description") or data["error"])
        raise AirBankApiError(response.status, message, codes, data)
    return data
