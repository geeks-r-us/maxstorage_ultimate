"""Platform for Max Storage Ultimate sensor integration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MaxStorageDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, config: ConfigEntry, async_add_entities
):
    """Set up the sensor platform."""
    _LOGGER.debug("sensor.py:async_setup_entry: %s", config)

    coordinator = hass.data[DOMAIN][config.entry_id]["coordinator"]

    sensors: list[MaxStorageSensor] = [
        MaxStorageSensor(coordinator, description) for description in SENSOR_TYPES
    ]
    async_add_entities(sensors)


@dataclass(frozen=True)
class MaxStorageSensorDescriptionMixin:
    """Mixin for sensor descriptions."""

    value_fn: Callable[[dict[str, Any]], str | int | float | None]


@dataclass(frozen=True)
class MaxStorageSensorDescription(
    SensorEntityDescription, MaxStorageSensorDescriptionMixin
):
    """Describes MaxStorage sensor entity."""

    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] = lambda _: {}


class MaxStorageSensor(
    CoordinatorEntity[MaxStorageDataUpdateCoordinator], SensorEntity
):
    """Representation of a MaxStorage sensor."""

    _attr_has_entity_name = True
    entity_description: MaxStorageSensorDescription

    def __init__(
        self,
        coordinator: MaxStorageDataUpdateCoordinator,
        description: MaxStorageSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.api.device_info['Ident']}_{description.translation_key}"
        )
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> str | int | float | None:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes of the sensor."""
        return self.entity_description.attr_fn(self.coordinator.data)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


SENSOR_TYPES: tuple[MaxStorageSensorDescription, ...] = (
    MaxStorageSensorDescription(
        key="batterySoC",
        translation_key="batterySoC",
        icon="mdi:battery",
        value_fn=lambda data: data["batterySoC"],
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.BATTERY,
    ),
    MaxStorageSensorDescription(
        key="batteryCapacity",
        translation_key="batteryCapacity",
        icon="mdi:battery",
        value_fn=lambda data: data["batteryCapacity"],
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
    ),
    MaxStorageSensorDescription(
        key="batteryPower",
        translation_key="batteryPower",
        icon="mdi:battery",
        value_fn=lambda data: data["batteryPower"],
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
    MaxStorageSensorDescription(
        key="gridPower",
        translation_key="gridPower",
        icon="mdi:transmission-tower",
        value_fn=lambda data: data["gridPower"],
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
    MaxStorageSensorDescription(
        key="usagePower",
        translation_key="usagePower",
        icon="mdi:transmission-tower",
        value_fn=lambda data: data["usagePower"],
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
    MaxStorageSensorDescription(
        key="plantPower",
        translation_key="plantPower",
        icon="mdi:solar-power",
        value_fn=lambda data: data["plantPower"],
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
    MaxStorageSensorDescription(
        key="storageDCPower",
        translation_key="storageDCPower",
        icon="mdi:solar-power",
        value_fn=lambda data: data["storageDCPower"],
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
)
