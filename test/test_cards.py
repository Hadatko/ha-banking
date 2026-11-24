"""The dashboard lists missing cards and does not install them or create a dashboard."""

from __future__ import annotations

from typing import Any

from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
import pytest

from custom_components.ha_banking import cards, dashboard
from custom_components.ha_banking.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components in these tests."""


@pytest.fixture
async def lovelace(hass: HomeAssistant) -> Any:
    assert await async_setup_component(hass, "lovelace", {})
    resources = hass.data[LOVELACE_DATA].resources
    if not resources.loaded:
        await resources.async_load()
    return resources


async def test_missing_cards_follow_registered_resources(
    hass: HomeAssistant, lovelace: Any
) -> None:
    assert await cards.async_missing_cards(hass) == [card.name for card in cards.CARDS]
    await lovelace.async_create_item(
        {"res_type": "module", "url": "/hacsfiles/button-card/button-card.js"}
    )
    assert await cards.async_missing_cards(hass) == ["stack-in-card", "mini-graph-card"]


async def test_check_cards_opens_a_repair_without_installing(
    hass: HomeAssistant, lovelace: Any
) -> None:
    await dashboard.async_check_cards(hass)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, dashboard.ISSUE_CARDS)
    assert issue is not None
    assert issue.is_fixable is False
    assert "button-card" in issue.translation_placeholders["cards"]
    assert "stack-in-card" in issue.translation_placeholders["cards"]


async def test_get_dashboard_returns_yaml_and_writes_nothing(
    hass: HomeAssistant, lovelace: Any
) -> None:
    class Store:
        def __init__(self) -> None:
            self.config: dict | None = None

        async def async_save(self, config: dict) -> None:
            self.config = config

    store = Store()
    hass.data[LOVELACE_DATA].dashboards["ha-banking"] = store
    await dashboard.async_setup_services(hass)
    response = await hass.services.async_call(
        DOMAIN,
        dashboard.SERVICE_GET_DASHBOARD,
        {},
        blocking=True,
        return_response=True,
    )
    assert "views:" in response["yaml"]
    assert "No accounts yet." in response["yaml"]
    assert store.config is None
    assert not hasattr(dashboard, "_save_dashboard")
