from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN, SENSOR_PREFIX

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, config: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the sensor platform."""
    _LOGGER.debug("sensor.py:async_setup_entry: %s", config)

    coordinator = hass.data[DOMAIN][config.entry_id]["coordinator"]

    sensors = []
    relais_data = coordinator.data.get("Relais", {})
    names = relais_data.get("name", [])

    for index, name in enumerate(names):
        if name:  # Only add sensors for relays with a name
            sensor = RelaisSensor(coordinator, f"Relais {name}", index)
            sensors.append(sensor)

    async_add_entities(sensors)


class BaseSensor(CoordinatorEntity, BinarySensorEntity):
    """Base class for binary sensors."""

    def __init__(self, coordinator: DataUpdateCoordinator, sensor_type: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._sensor_type = sensor_type
        self._name = f"{SENSOR_PREFIX} {sensor_type}"
        self._state = None
        self._icon = None

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def unique_id(self):
        """Return the unique ID for this sensor."""
        return f"{SENSOR_PREFIX}_{self._sensor_type}"

    @property
    def icon(self):
        """Return the icon."""
        return self._icon

    @property
    def is_on(self):
        """Return true if the binary sensor is on."""
        return self._state

    @property
    def device_class(self):
        """Return the device class."""
        return self._device_class

    @property
    def available(self):
        """Return True if entity is available."""
        return self.coordinator.last_update_success


class RelaisSensor(BaseSensor):
    """Relais sensor class."""

    def __init__(
        self, coordinator: DataUpdateCoordinator, sensor_type: str, index: int
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, sensor_type)
        self._device_class = BinarySensorDeviceClass.POWER
        self._icon = "mdi:power-plug"
        self._index = index

    @property
    def is_on(self):
        """Return true if the binary sensor is on."""
        relais_data = self.coordinator.data.get("Relais", {})
        values = relais_data.get("value", [])
        if len(values) > self._index:
            return bool(values[self._index])
        return False
