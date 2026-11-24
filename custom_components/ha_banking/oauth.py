"""OAuth2 for Air Bank, including the required client certificate."""

from __future__ import annotations

from collections.abc import Mapping
import ssl
from typing import Any, cast

from aiohttp import ClientError, ClientSession
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_entry_oauth2_flow import (
    AUTH_CALLBACK_PATH,
    MY_AUTH_CALLBACK_PATH,
    LocalOAuth2Implementation,
    _encode_jwt,
    async_get_redirect_uri,
)
from yarl import URL

from .const import CONF_CERT, CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_KEY, CONF_SANDBOX, DOMAIN

API_HOST = "https://api.airbank.cz"


def home_assistant_redirect_uri(hass: HomeAssistant) -> str:
    """Return the redirect URI this Home Assistant instance expects."""
    external_url = getattr(hass.config, "external_url", None)
    if external_url:
        return f"{str(external_url).rstrip('/')}{AUTH_CALLBACK_PATH}"
    try:
        return async_get_redirect_uri(hass)
    except RuntimeError:
        return MY_AUTH_CALLBACK_PATH


def authorization_state(hass: HomeAssistant, flow_id: str) -> str:
    """Encode the OAuth state so the Home Assistant callback can resume the flow."""
    return _encode_jwt(
        hass,
        {"flow_id": flow_id, "redirect_uri": home_assistant_redirect_uri(hass)},
    )


class AirBankOAuth2Implementation(LocalOAuth2Implementation):
    """Exchange Air Bank authorization codes with a PSD2 client certificate."""

    def __init__(self, hass: HomeAssistant, data: Mapping[str, Any]) -> None:
        sandbox = bool(data.get(CONF_SANDBOX))
        base = f"{API_HOST}/sandbox" if sandbox else API_HOST
        super().__init__(
            hass,
            DOMAIN,
            data[CONF_CLIENT_ID],
            data[CONF_CLIENT_SECRET],
            "https://ib.airbank.cz/",
            f"{base}/oauth2/token",
        )
        self.sandbox = sandbox
        self.client_cert = data[CONF_CERT]
        self.client_key = data[CONF_KEY]
        self._ssl_context: ssl.SSLContext | None = None

    @property
    def name(self) -> str:
        return "Air Bank"

    @property
    def redirect_uri(self) -> str:
        return home_assistant_redirect_uri(self.hass)

    async def async_generate_authorize_url(self, flow_id: str) -> str:
        if not self.sandbox:
            return await super().async_generate_authorize_url(flow_id)
        redirect_uri = self.redirect_uri
        state = _encode_jwt(self.hass, {"flow_id": flow_id, "redirect_uri": redirect_uri})
        return str(
            URL("http://developers.airbank.cz/sandbox/login").with_query(
                {"state": state, "redirectUri": redirect_uri}
            )
        )

    async def _client_ssl(self) -> ssl.SSLContext:
        """Load the client certificate once, off the event loop, and reuse it."""
        if self._ssl_context is None:
            cert = self.client_cert
            key = self.client_key

            def build() -> ssl.SSLContext:
                context = ssl.create_default_context()
                context.load_cert_chain(cert, key)
                return context

            self._ssl_context = await self.hass.async_add_executor_job(build)
        return self._ssl_context

    async def _token_request(self, data: dict) -> dict:
        payload = dict(data)
        payload["client_id"] = self.client_id
        if self.client_secret:
            payload["client_secret"] = self.client_secret
        context = await self._client_ssl()
        async with ClientSession() as session:
            response = await session.post(self.token_url, json=payload, ssl=context)
            if response.status >= 400:
                try:
                    error_body = await response.json()
                except (ClientError, ValueError):
                    error_body = {}
                error_code = error_body.get("error", "unknown")
                error_description = error_body.get("error_description", "unknown error")
                response.release()
                raise ClientError(f"{error_code}: {error_description}")
            return cast(dict, await response.json())
