"""Platform for MaxStorage Ultimate binary sensor integration."""
from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN
from .coordinator import MaxStorageDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, config: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the sensor platform."""
    _LOGGER.debug("sensor.py:async_setup_entry: %s", config)

    coordinator = hass.data[DOMAIN][config.entry_id]["coordinator"]

    sensors: list[BinarySensorEntity] = []
    relais_data = coordinator.data.get("Relais", {})
    names = relais_data.get("name", [])

    for index, name in enumerate(names):
        if name:  # Only add sensors for relays with a name
            description = MaxStorageBinarySensorDescription(
                key="relais_{name}",
                icon="mdi:power-plug",
                device_class=BinarySensorDeviceClass.POWER,
                value_fn=lambda data, idx=index: bool(
                    data.get("Relais", {}).get("value", [])[idx]
                ),
                name=name,
            )

            sensor = MaxStorageBinarySensor(coordinator, description)
            sensors.append(sensor)

    async_add_entities(sensors)


@dataclass(frozen=True)
class MaxStorageBinarySensorDescriptionMixin:
    """Mixin for sensor descriptions."""

    value_fn: Callable[[dict[str, Any]], str | int | float | None]


@dataclass(frozen=True)
class MaxStorageBinarySensorDescription(
    BinarySensorEntityDescription, MaxStorageBinarySensorDescriptionMixin
):
    """Describes MaxStorage sensor entity."""

    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] = lambda _: {}


class MaxStorageBinarySensor(
    CoordinatorEntity[MaxStorageDataUpdateCoordinator], BinarySensorEntity
):
    """Representation of MaxStorage binary sensors."""

    _attr_has_entity_name = True
    entity_description: MaxStorageBinarySensorDescription

    def __init__(
        self,
        coordinator: MaxStorageDataUpdateCoordinator,
        description: MaxStorageBinarySensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.api.device_info['Ident']}_{description.name}"
        )
        self._attr_device_info = coordinator.device_info

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes of the sensor."""
        return self.entity_description.attr_fn(self.coordinator.data)

    @property
    def is_on(self):
        """Return true if the binary sensor is on."""
        return self.entity_description.value_fn(self.coordinator.data)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
