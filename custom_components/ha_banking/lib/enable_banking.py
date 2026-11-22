"""Read-only Enable Banking client for a user's own application."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from urllib.parse import urlencode

import jwt

from ..snapshot import build_enable_banking_snapshot, history_start
from .http import HttpResponse, Urllib3Transport
from .models import ClientConfig

API_HOST = "https://api.enablebanking.com"
REAUTH_SESSION_STATUSES = {"EXPIRED", "CANCELLED", "CLOSED", "INVALID", "REVOKED"}


class EnableBankingError(Exception):
    """Error returned by the Enable Banking API."""

    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


class EnableBankingSessionExpired(EnableBankingError):
    """The stored bank session can no longer be used."""

    def __init__(self, session_status: str):
        self.session_status = session_status
        super().__init__(401, session_status)


class EnableBanking:
    """Call the account-information API with an application JWT."""

    def __init__(self, application_id: str, private_key_path: str, *, transport=None, clock=None):
        self.application_id = application_id
        self.private_key_path = private_key_path
        self.transport = transport or Urllib3Transport(ClientConfig())
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def list_aspsps(self, *, country: str, psu_type: str, service: str) -> dict:
        return self._request(
            "GET",
            "/aspsps",
            query={"country": country, "psu_type": psu_type, "service": service},
        )

    def start_authorization(self, body: dict) -> dict:
        return self._request("POST", "/auth", json_body=body)

    def authorize_session(self, code: str) -> dict:
        return self._request("POST", "/sessions", json_body={"code": code})

    def get_session(self, session_id: str) -> dict:
        return self._request("GET", f"/sessions/{session_id}")

    def list_balances(self, account_uid: str) -> dict:
        return self._request("GET", f"/accounts/{account_uid}/balances")

    def list_transactions(self, account_uid: str, *, date_from: str) -> list[dict]:
        transactions: list[dict] = []
        continuation_key = None
        for _ in range(10):
            query = {"date_from": date_from}
            if continuation_key:
                query["continuation_key"] = continuation_key
            page = self._request("GET", f"/accounts/{account_uid}/transactions", query=query)
            transactions.extend(page.get("transactions") or [])
            continuation_key = page.get("continuation_key") or None
            if not continuation_key:
                break
        return transactions

    def _request(self, method: str, path: str, *, query: dict | None = None, json_body: dict | None = None):
        headers = {
            "Authorization": f"Bearer {self._jwt()}",
            "Accept": "application/json",
            "Psu-Ip-Address": "127.0.0.1",
            "Psu-User-Agent": "HomeAssistant ha_banking",
        }
        body = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(json_body).encode()
        url = _with_query(f"{API_HOST}{path}", query)
        response = self.transport.request(method, url, headers=headers, body=body)
        return _decode(response)

    def _jwt(self) -> str:
        issued = self._clock()
        token = jwt.encode(
            {
                "iss": "enablebanking.com",
                "aud": "api.enablebanking.com",
                "iat": int(issued.timestamp()),
                "exp": int((issued + timedelta(hours=1)).timestamp()),
            },
            Path(self.private_key_path).read_text(),
            algorithm="RS256",
            headers={"kid": self.application_id, "typ": "JWT"},
        )
        if isinstance(token, bytes):
            return token.decode()
        return token


def private_key_ready(path: str) -> bool:
    """Return whether the path is a private key this client can sign with."""
    file = Path(path)
    if not file.is_file():
        return False
    try:
        jwt.encode(
            {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": 0, "exp": 60},
            file.read_text(),
            algorithm="RS256",
            headers={"kid": "00000000-0000-0000-0000-000000000000", "typ": "JWT"},
        )
    except (OSError, ValueError, jwt.PyJWTError):
        return False
    return True


def find_aspsp(aspsps: list[dict], name: str) -> dict:
    """Return the ASPSP whose name matches the user's choice."""
    wanted = " ".join(name.casefold().split())
    for bank in aspsps:
        current = " ".join(str(bank.get("name", "")).casefold().split())
        if current == wanted:
            return bank
    raise LookupError(f"{name} is not available for this Enable Banking application")


def find_air_bank(aspsps: list[dict]) -> dict:
    """Return the Czech Air Bank ASPSP record."""
    czech = [bank for bank in aspsps if str(bank.get("country", "")).upper() == "CZ"]
    try:
        return find_aspsp(czech, "Air Bank")
    except LookupError:
        return find_aspsp(czech, "Airbank")


def aspsp_names(aspsps: list[dict]) -> list[str]:
    """Unique bank names in the order Enable Banking returned them."""
    names: list[str] = []
    for bank in aspsps:
        name = str(bank.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def consent_valid_until(aspsp: dict, now: datetime) -> str:
    """Consent end accepted by this bank, one minute inside its maximum."""
    seconds = int(aspsp.get("maximum_consent_validity") or 90 * 24 * 3600)
    moment = now.astimezone(timezone.utc) + timedelta(seconds=max(seconds - 60, 60))
    return moment.isoformat()


def account_record(account: dict) -> dict | None:
    """Keep the account fields needed to poll balances later."""
    uid = account.get("uid")
    if not uid:
        return None
    identification = account.get("account_id") or {}
    return {
        "uid": uid,
        "name": account.get("details") or account.get("name") or "",
        "iban": identification.get("iban") or "",
        "currency": account.get("currency") or "CZK",
        "product": account.get("product") or account.get("cash_account_type") or "",
    }


def begin_authorization(
    bank: EnableBanking,
    *,
    aspsp_name: str,
    country: str,
    redirect_url: str,
    state: str,
    psu_type: str,
) -> str:
    """Start consent for one bank and return its URL. Only balances and transactions are requested."""
    listed = bank.list_aspsps(country=country, psu_type=psu_type, service="AIS")
    aspsp = find_aspsp(listed.get("aspsps") or [], aspsp_name)
    started = bank.start_authorization(
        {
            "access": {
                "balances": True,
                "transactions": True,
                "valid_until": consent_valid_until(aspsp, bank._clock()),
            },
            "aspsp": {"name": aspsp["name"], "country": aspsp["country"]},
            "state": state,
            "redirect_url": redirect_url,
            "psu_type": psu_type,
            "language": _consent_language(country),
        }
    )
    return started["url"]


def begin_air_bank_authorization(
    bank: EnableBanking,
    *,
    redirect_url: str,
    state: str,
    psu_type: str,
) -> str:
    """Start the Air Bank consent redirect and return its URL."""
    return begin_authorization(
        bank,
        aspsp_name="Air Bank",
        country="CZ",
        redirect_url=redirect_url,
        state=state,
        psu_type=psu_type,
    )


def _consent_language(country: str) -> str:
    return {"CZ": "cs", "SK": "sk"}.get(country.upper(), "en")


def finish_authorization(bank: EnableBanking, code: str) -> dict:
    """Exchange the callback code for a session and readable accounts."""
    session = bank.authorize_session(code)
    accounts = []
    for account in session.get("accounts") or []:
        record = account_record(account)
        if record is not None:
            accounts.append(record)
    if not accounts:
        raise EnableBankingError(422, "The bank consent did not include a readable account")
    access = session.get("access") or {}
    return {
        "session_id": session["session_id"],
        "valid_until": access.get("valid_until"),
        "accounts": accounts,
    }


def collect_enable_banking_snapshots(
    bank: EnableBanking,
    session_id: str,
    accounts: list[dict],
    institution: str = "Air Bank",
    country: str = "CZ",
):
    """Load balances and recent transactions for accounts saved at consent time."""
    session = bank.get_session(session_id)
    status = session.get("status")
    if status in REAUTH_SESSION_STATUSES:
        raise EnableBankingSessionExpired(str(status))
    if status != "AUTHORIZED":
        raise EnableBankingError(409, f"Enable Banking session is {status}")
    today = bank._clock().date()
    date_from = history_start(today).isoformat()
    snapshots = []
    for account in accounts:
        uid = account.get("uid")
        if not uid:
            continue
        balances = bank.list_balances(uid).get("balances") or []
        transactions = bank.list_transactions(uid, date_from=date_from)
        snapshots.append(
            build_enable_banking_snapshot(
                account,
                balances,
                transactions,
                today=today,
                institution=institution,
                country=country,
            )
        )
    return snapshots


def _with_query(url: str, query: dict | None) -> str:
    if not query:
        return url
    items = [(key, value) for key, value in query.items() if value is not None]
    if not items:
        return url
    return f"{url}?{urlencode(items)}"


def _decode(response: HttpResponse):
    text = response.body.decode() if response.body else ""
    data = None
    if text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
    if response.status >= 400:
        message = text or f"HTTP {response.status}"
        if isinstance(data, dict) and data.get("message"):
            message = str(data["message"])
        raise EnableBankingError(response.status, message)
    if data is None:
        raise EnableBankingError(response.status, "Enable Banking returned an empty response")
    return data
