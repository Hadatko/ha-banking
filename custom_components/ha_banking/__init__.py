"""Read-only Air Bank account integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session

from .config_flow import AirBankConfigFlow, implementation_from_entry
from .const import AuthMethod, CONF_AUTH_METHOD, CONF_IMAP_HOST, CONF_IMAP_USERNAME, CONF_LARGE_PAYMENT, CONF_LOW_BALANCE
from .coordinator import AirBankCoordinator
from .const import DashboardStyle
from .dashboard import async_setup_dashboard, dashboard_style
from .dashboard_classic import async_publish_graph_statistics
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
    cached = await coordinator.async_load_mailbox_cache()
    if cached:
        # Show the stored mailbox at once; the mailbox is checked for new mail in the background.
        coordinator.async_set_updated_data(cached)
    else:
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
        _publish_classic_statistics()
        hass.async_create_task(async_setup_dashboard(hass))

    def _publish_classic_statistics() -> None:
        # Only the classic charts read recorder statistics.
        if dashboard_style(hass) == DashboardStyle.CLASSIC:
            async_publish_graph_statistics(hass, coordinator.data or [])

    async def _options_updated(hass: HomeAssistant, _entry: ConfigEntry) -> None:
        _publish_classic_statistics()
        await async_setup_dashboard(hass)

    entry.async_on_unload(coordinator.async_add_listener(_on_update))
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_setup_dashboard(hass)
    _publish_classic_statistics()
    if cached:
        entry.async_create_background_task(hass, coordinator.async_refresh(), f"{entry.entry_id} mailbox sync")
    return True


async def async_remove_entry(hass: HomeAssistant, entry: AirBankConfigEntry) -> None:
    """Delete the stored mailbox cache of a removed entry."""
    if entry.data.get(CONF_AUTH_METHOD) == AuthMethod.EMAIL:
        from .mailbox_store import MailboxStore, mailbox_key

        await MailboxStore(
            hass, entry.entry_id, mailbox_key(str(entry.data.get(CONF_IMAP_HOST, "")), str(entry.data.get(CONF_IMAP_USERNAME, "")))
        ).async_remove()


async def async_unload_entry(hass: HomeAssistant, entry: AirBankConfigEntry) -> bool:
    """Unload sensors for this login."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
