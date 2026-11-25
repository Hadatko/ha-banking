"""OAuth URL tests that do not need a running Home Assistant."""

from types import SimpleNamespace

import pytest

from custom_components.ha_bank_watch.config_flow import EMAIL_SCHEMA
from custom_components.ha_bank_watch.const import CONF_MARK_SEEN
from custom_components.ha_bank_watch.oauth import (
    AirBankOAuth2Implementation,
    home_assistant_redirect_uri,
)


def _hass() -> SimpleNamespace:
    return SimpleNamespace(data={}, config=SimpleNamespace(components=set()))


def _implementation(hass, sandbox: bool = False) -> AirBankOAuth2Implementation:
    return AirBankOAuth2Implementation(
        hass,
        {
            "client_id": "client",
            "client_secret": "secret",
            "client_cert": "/config/airbank/client.pem",
            "client_key": "/config/airbank/client.key",
            "sandbox": sandbox,
        },
    )


def test_marking_notices_read_is_on_by_default() -> None:
    marker = next(key for key in EMAIL_SCHEMA.schema if getattr(key, "schema", None) == CONF_MARK_SEEN)
    assert marker.default() is True


def test_redirect_uses_the_configured_home_assistant_address() -> None:
    hass = SimpleNamespace(config=SimpleNamespace(external_url="https://home.example.com"))
    assert home_assistant_redirect_uri(hass) == "https://home.example.com/auth/external/callback"


@pytest.mark.asyncio
async def test_production_login_uses_internet_banking() -> None:
    url = await _implementation(_hass()).async_generate_authorize_url("flow-1")
    assert url.startswith("https://ib.airbank.cz/?")
    assert "client_id=client" in url
    assert "response_type=code" in url
    assert "redirect_uri=" in url


@pytest.mark.asyncio
async def test_client_certificate_is_loaded_once_off_the_loop(monkeypatch) -> None:
    """The SSL context is built in an executor and reused for later token calls."""
    calls: list[str] = []

    def create_default_context():
        calls.append("create")
        return SimpleNamespace(load_cert_chain=lambda *_args: calls.append("load"))

    async def executor(func):
        calls.append("executor")
        return func()

    monkeypatch.setattr(
        "custom_components.ha_bank_watch.oauth.ssl.create_default_context",
        create_default_context,
    )
    hass = _hass()
    hass.async_add_executor_job = executor
    implementation = _implementation(hass)
    first = await implementation._client_ssl()
    second = await implementation._client_ssl()
    assert first is second
    assert calls == ["executor", "create", "load"]


@pytest.mark.asyncio
async def test_sandbox_login_uses_documented_redirect_parameter() -> None:
    url = await _implementation(_hass(), sandbox=True).async_generate_authorize_url("flow-1")
    assert url.startswith("http://developers.airbank.cz/sandbox/login?")
    assert "redirectUri=" in url
    assert "redirect_uri=" not in url
