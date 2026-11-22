"""Sensors for Air Bank account balances and payments."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AirBankCoordinator
from .snapshot import AccountSnapshot, Reading

SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="booked_balance",
        translation_key="booked_balance",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        icon="mdi:bank",
        has_entity_name=True,
    ),
    SensorEntityDescription(
        key="available_balance",
        translation_key="available_balance",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        icon="mdi:cash",
        has_entity_name=True,
    ),
    SensorEntityDescription(
        key="incoming_payment",
        translation_key="incoming_payment",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        icon="mdi:bank-transfer-in",
        has_entity_name=True,
    ),
    SensorEntityDescription(
        key="outgoing_payment",
        translation_key="outgoing_payment",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        icon="mdi:bank-transfer-out",
        has_entity_name=True,
    ),
    SensorEntityDescription(
        key="transaction_status",
        translation_key="transaction_status",
        icon="mdi:information-outline",
        has_entity_name=True,
    ),
    SensorEntityDescription(
        key="year_incoming",
        translation_key="year_incoming",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        icon="mdi:calendar-plus",
        has_entity_name=True,
    ),
    SensorEntityDescription(
        key="year_outgoing",
        translation_key="year_outgoing",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        icon="mdi:calendar-minus",
        has_entity_name=True,
    ),
    SensorEntityDescription(
        key="year_net",
        translation_key="year_net",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        icon="mdi:calendar",
        has_entity_name=True,
    ),
    SensorEntityDescription(
        key="month_incoming",
        translation_key="month_incoming",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        icon="mdi:calendar-plus",
        has_entity_name=True,
    ),
    SensorEntityDescription(
        key="month_outgoing",
        translation_key="month_outgoing",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        icon="mdi:calendar-minus",
        has_entity_name=True,
    ),
    SensorEntityDescription(
        key="month_net",
        translation_key="month_net",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        icon="mdi:calendar-month",
        has_entity_name=True,
    ),
    SensorEntityDescription(
        key="recent_payments",
        translation_key="recent_payments",
        icon="mdi:format-list-bulleted",
        has_entity_name=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one device per account and sensors for its balances and payments."""
    coordinator: AirBankCoordinator = entry.runtime_data
    known: set[str] = set()

    def _add_accounts() -> None:
        added: list[AirBankSensor] = []
        for snapshot in coordinator.data or []:
            if snapshot.account_id in known:
                continue
            known.add(snapshot.account_id)
            added.extend(
                AirBankSensor(coordinator, snapshot.account_id, description)
                for description in SENSORS
            )
        if added:
            async_add_entities(added)

    coordinator.async_add_listener(_add_accounts)
    _add_accounts()


class AirBankSensor(CoordinatorEntity[AirBankCoordinator], SensorEntity):
    """One read-only value from an Air Bank account."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AirBankCoordinator,
        account_id: str,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._account_id = account_id
        self._attr_unique_id = f"{account_id}_{description.key}"

    @property
    def _snapshot(self) -> AccountSnapshot | None:
        for snapshot in self.coordinator.data or []:
            if snapshot.account_id == self._account_id:
                return snapshot
        return None

    @property
    def _reading(self) -> Reading | None:
        snapshot = self._snapshot
        if snapshot is None:
            return None
        return snapshot.readings()[self.entity_description.key]

    @property
    def available(self) -> bool:
        return super().available and self._snapshot is not None

    @property
    def native_value(self) -> float | str | None:
        reading = self._reading
        if reading is None:
            return None
        return reading.value

    @property
    def native_unit_of_measurement(self) -> str | None:
        reading = self._reading
        if reading is None:
            return None
        return reading.currency

    @property
    def extra_state_attributes(self) -> dict:
        reading = self._reading
        if reading is None:
            return {}
        return {key: value for key, value in reading.attributes.items() if value is not None}

    @property
    def entity_picture(self) -> str | None:
        snapshot = self._snapshot
        if snapshot is None:
            return None
        return snapshot.logo_url

    @property
    def device_info(self) -> DeviceInfo | None:
        snapshot = self._snapshot
        if snapshot is None:
            return None
        return DeviceInfo(
            identifiers={(DOMAIN, snapshot.account_id)},
            manufacturer=snapshot.institution,
            model=snapshot.product,
            name=snapshot.name,
            configuration_url=snapshot.configuration_url,
        )
