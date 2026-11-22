"""Choosing between the classic and the modern dashboard."""

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_banking.const import (
    CONF_DASHBOARD_STYLE,
    CONF_IMAP_HOST,
    CONF_IMAP_PASSWORD,
    CONF_IMAP_USERNAME,
    DOMAIN,
    DashboardStyle,
)
from custom_components.ha_banking.dashboard import dashboard_style

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None, hass: HomeAssistant) -> None:
    """Load custom_components; treat the manifest dependencies as already set up."""
    hass.config.components.update({"frontend", "lovelace", "persistent_notification"})


EMAIL = {
    CONF_IMAP_HOST: "imap.gmail.com",
    "imap_port": 993,
    CONF_IMAP_USERNAME: "user@example.com",
    CONF_IMAP_PASSWORD: "secret",
}


async def test_style_defaults_to_modern(hass: HomeAssistant) -> None:
    assert dashboard_style(hass) is DashboardStyle.MODERN
    MockConfigEntry(domain=DOMAIN, options={CONF_DASHBOARD_STYLE: "nonsense"}).add_to_hass(hass)
    assert dashboard_style(hass) is DashboardStyle.MODERN


async def test_the_oldest_entry_decides_the_style(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, options={CONF_DASHBOARD_STYLE: "classic"}).add_to_hass(hass)
    MockConfigEntry(domain=DOMAIN, options={CONF_DASHBOARD_STYLE: "modern"}).add_to_hass(hass)
    assert dashboard_style(hass) is DashboardStyle.CLASSIC


async def _email_flow(hass: HomeAssistant, username: str = "user@example.com"):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "email"})
    with (
        patch("custom_components.ha_banking.config_flow.open_mailbox"),
        patch("custom_components.ha_banking.async_setup_entry", return_value=True),
    ):
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {**EMAIL, CONF_IMAP_USERNAME: username}
        ), result["flow_id"]


async def test_first_entry_asks_for_the_dashboard_style(hass: HomeAssistant) -> None:
    result, flow_id = await _email_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "dashboard"
    with patch("custom_components.ha_banking.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(flow_id, {CONF_DASHBOARD_STYLE: "classic"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {CONF_DASHBOARD_STYLE: "classic"}


async def test_later_entries_share_the_style(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id="first", options={CONF_DASHBOARD_STYLE: "classic"}).add_to_hass(hass)
    result, _ = await _email_flow(hass, "second@example.com")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {CONF_DASHBOARD_STYLE: "classic"}


async def test_options_change_the_style_of_every_entry(hass: HomeAssistant) -> None:
    first = MockConfigEntry(domain=DOMAIN, options={CONF_DASHBOARD_STYLE: "modern"})
    second = MockConfigEntry(domain=DOMAIN, options={CONF_DASHBOARD_STYLE: "modern", "large_payment": 5})
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(first.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_DASHBOARD_STYLE: "classic", "install_cards": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert first.options[CONF_DASHBOARD_STYLE] == "classic"
    assert second.options == {CONF_DASHBOARD_STYLE: "classic", "large_payment": 5}
