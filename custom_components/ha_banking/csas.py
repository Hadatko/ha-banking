"""Read-only Česká spořitelna Premium Accounts API.

The user brings an application created on the Erste developer portal. This module
lists payment accounts, balances, and transactions. It does not initiate payments.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol
from urllib.parse import urlencode
import json

import urllib3

from .const import AuthMethod
from .lib.http import HttpResponse
from .snapshot import build_account_snapshot, history_start

SANDBOX_ACCOUNTS = "https://webapi.developers.erstegroup.com/api/csas/public/sandbox/v3/accounts"
PRODUCTION_ACCOUNTS = "https://www.csas.cz/webapi/api/v3/accounts"
SANDBOX_IDP = "https://webapi.developers.erstegroup.com/api/csas/sandbox/v1/sandbox-idp"
PRODUCTION_IDP = "https://bezpecnost.csas.cz/api/psd2/fl/oidc/v1"
ACCOUNT_SCOPE = "siblings.accounts"
INSTITUTION = "Česká spořitelna"
CONFIGURATION_URL = "https://george.csas.cz/"


class CeskaSporitelnaError(Exception):
    """The bank rejected a token or an account request."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Transport(Protocol):
    """HTTPS call used by tests and by the live client."""

    def request(
        self, method: str, url: str, headers: dict | None = None, body: bytes | None = None
    ) -> HttpResponse: ...


def sign_in_menu() -> tuple[AuthMethod, ...]:
    """The three sign-in methods shown first."""
    return (AuthMethod.EMAIL, AuthMethod.ENABLE_BANKING, AuthMethod.BANK_API)


def bank_api_menu() -> tuple[AuthMethod, ...]:
    """Banks reached through their own API."""
    return (AuthMethod.DIRECT, AuthMethod.CSAS)


def authorization_url(*, sandbox: bool, client_id: str, redirect_uri: str, state: str) -> str:
    """OAuth authorize URL. The scope is account information only."""
    query = urlencode(
        {
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
            "scope": ACCOUNT_SCOPE,
            "state": state,
        }
    )
    return f"{_idp(sandbox)}/auth?{query}"


class CeskaSporitelna:
    """One application registered for Česká spořitelna."""

    def __init__(
        self,
        *,
        sandbox: bool,
        client_id: str,
        client_secret: str,
        web_api_key: str,
        transport: Transport | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.client_id = client_id
        self.client_secret = client_secret
        self.web_api_key = web_api_key
        self._transport = transport or Urllib3Transport()

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        """Trade the login code for an access token and a refresh token."""
        return self._token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
        )

    def refresh(self, refresh_token: str) -> dict:
        """Ask for a new access token. The refresh token lasts 180 days."""
        return self._token(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
        )

    def accounts(self, access_token: str) -> list[dict]:
        """Every consented payment account."""
        return _pages(self._get, "/my/accounts", access_token, "accounts")

    def balances(self, access_token: str, account_id: str) -> list[dict]:
        payload = self._get(f"/my/accounts/{account_id}/balance", access_token)
        return list(payload.get("balances") or [])

    def transactions(self, access_token: str, account_id: str, start: date, end: date) -> list[dict]:
        """Booked movements in the window. Pages follow nextPage."""
        collected: list[dict] = []
        page = 0
        for _ in range(20):
            payload = self._get(
                f"/my/accounts/{account_id}/transactions",
                access_token,
                {"fromDate": start.isoformat(), "toDate": end.isoformat(), "page": page, "size": 100},
            )
            collected.extend(payload.get("transactions") or [])
            next_page = payload.get("nextPage")
            if next_page is None or next_page == page:
                break
            page = next_page
        return collected

    def _token(self, form: dict[str, str]) -> dict:
        response = self._transport.request(
            "POST",
            f"{_idp(self.sandbox)}/token",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            body=urlencode(form).encode(),
        )
        return _json(response)

    def _get(self, path: str, access_token: str, query: dict | None = None) -> dict:
        url = f"{_accounts(self.sandbox)}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        response = self._transport.request(
            "GET",
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "web-api-key": self.web_api_key,
                "Accept": "application/json",
            },
        )
        return _json(response)


class Urllib3Transport:
    """Plain HTTPS. This API does not use a purchased client certificate."""

    def __init__(self) -> None:
        self._http = urllib3.PoolManager(timeout=urllib3.Timeout(connect=10, read=30))

    def request(
        self, method: str, url: str, headers: dict | None = None, body: bytes | None = None
    ) -> HttpResponse:
        response = self._http.request(method, url, body=body, headers=headers or {})
        return HttpResponse(response.status, response.data, dict(response.headers))


def collect_csas_snapshots(bank: CeskaSporitelna, access_token: str, today: date | None = None) -> list:
    """Load accounts, balances, and transactions into the shared sensor view."""
    today = today or date.today()
    start = history_start(today)
    snapshots = []
    for account in bank.accounts(access_token):
        account_id = str(account["id"])
        snapshots.append(
            build_account_snapshot(
                account,
                bank.balances(access_token, account_id),
                bank.transactions(access_token, account_id, start, today),
                today,
                institution=INSTITUTION,
                country="CZ",
                configuration_url=CONFIGURATION_URL,
            )
        )
    return snapshots


def _pages(get, path: str, access_token: str, key: str) -> list[dict]:
    collected: list[dict] = []
    page = 0
    for _ in range(20):
        payload = get(path, access_token, {"page": page, "size": 50})
        collected.extend(payload.get(key) or [])
        next_page = payload.get("nextPage")
        if next_page is None or next_page == page:
            break
        page = next_page
    return collected


def _accounts(sandbox: bool) -> str:
    return SANDBOX_ACCOUNTS if sandbox else PRODUCTION_ACCOUNTS


def _idp(sandbox: bool) -> str:
    return SANDBOX_IDP if sandbox else PRODUCTION_IDP


def _json(response: HttpResponse) -> dict:
    if response.status < 200 or response.status >= 300:
        raise CeskaSporitelnaError("Česká spořitelna rejected the request", response.status)
    try:
        payload = json.loads(response.body.decode() or "{}")
    except json.JSONDecodeError as err:
        raise CeskaSporitelnaError("Česká spořitelna returned a response that is not JSON", response.status) from err
    if not isinstance(payload, dict):
        raise CeskaSporitelnaError("Česká spořitelna returned an unexpected response", response.status)
    return payload
