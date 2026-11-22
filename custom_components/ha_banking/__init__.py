"""Read-only Air Bank account integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session

from .config_flow import AirBankConfigFlow, implementation_from_entry
from .const import AuthMethod, CONF_AUTH_METHOD, CONF_LARGE_PAYMENT, CONF_LOW_BALANCE
from .coordinator import AirBankCoordinator
from .dashboard import async_publish_graph_statistics, async_setup_dashboard
from .payments import (
    known_payments,
    low_balance_alerted,
    optional_amount,
    publish_low_balance,
    publish_new_payments,
    seed_low_balance,
    seed_payments,
)

PLATFORMS = [Platform.SENSOR]

type AirBankConfigEntry = ConfigEntry[AirBankCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: AirBankConfigEntry) -> bool:
    """Set up account polling for one Air Bank login."""
    if entry.data.get(CONF_AUTH_METHOD) in {AuthMethod.ENABLE_BANKING, AuthMethod.EMAIL, AuthMethod.CSAS}:
        coordinator = AirBankCoordinator(hass, entry, None)
    else:
        implementation = implementation_from_entry(hass, entry)
        AirBankConfigFlow.async_register_implementation(hass, implementation)
        session = OAuth2Session(hass, entry, implementation)
        coordinator = AirBankCoordinator(hass, entry, session)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    seed_payments(known_payments(hass), coordinator.data or [])

    def _limits() -> tuple[float | None, float | None]:
        options = entry.options
        return optional_amount(options.get(CONF_LARGE_PAYMENT)), optional_amount(options.get(CONF_LOW_BALANCE))

    seed_low_balance(low_balance_alerted(hass), coordinator.data or [], _limits()[1])

    def _on_update() -> None:
        minimum, low = _limits()
        publish_new_payments(hass, coordinator.data or [], minimum)
        publish_low_balance(hass, coordinator.data or [], low)
        async_publish_graph_statistics(hass, coordinator.data or [])
        hass.async_create_task(async_setup_dashboard(hass))

    entry.async_on_unload(coordinator.async_add_listener(_on_update))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_setup_dashboard(hass)
    async_publish_graph_statistics(hass, coordinator.data or [])
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AirBankConfigEntry) -> bool:
    """Unload sensors for this login."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
