import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN, SENSOR_PREFIX

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    """Set up the sensor platform."""
    _LOGGER.debug("sensor.py:async_setup_entry: %s", entry)

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    sensors: list[Entity] = [
        BatterySoCSensor(coordinator),
        BatteryCapacitySensor(coordinator),
        BatteryPowerSensor(coordinator),
        GridPowerSensor(coordinator),
        UsagePowerSensor(coordinator),
        PlantPowerSensor(coordinator),
        StorageDCPowerSensor(coordinator),
    ]
    async_add_entities(sensors)


class BaseSensor(CoordinatorEntity, Entity):
    """Base class for sensors."""

    def __init__(self, coordinator: DataUpdateCoordinator, sensor_type: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._sensor_type = sensor_type
        self._name = f"{SENSOR_PREFIX} {sensor_type}"
        self._state = None
        self._unit_of_measurement = None
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
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return self._unit_of_measurement

    @property
    def icon(self):
        """Return the icon."""
        return self._icon

    @property
    def state(self):
        """Return the state of the sensor."""
        return self.coordinator.data[self._sensor_type]


class BatterySoCSensor(BaseSensor):
    """Battery state of charge sensor."""

    def __init__(self, coordinator: DataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "batterySoC")
        self._unit_of_measurement = PERCENTAGE


class BatteryCapacitySensor(BaseSensor):
    """Battery capacity sensor."""

    def __init__(self, coordinator: DataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "batteryCapacity")
        self._unit_of_measurement = UnitOfEnergy.WATT_HOUR


class BatteryPowerSensor(BaseSensor):
    """Battery power sensor."""

    def __init__(self, coordinator: DataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "batteryPower")
        self._unit_of_measurement = UnitOfPower.WATT


class GridPowerSensor(BaseSensor):
    """Grid power sensor."""

    def __init__(self, coordinator: DataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "gridPower")
        self._unit_of_measurement = UnitOfPower.WATT


class UsagePowerSensor(BaseSensor):
    """Usage power sensor."""

    def __init__(self, coordinator: DataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "usagePower")
        self._unit_of_measurement = UnitOfPower.WATT


class PlantPowerSensor(BaseSensor):
    """Plant power sensor."""

    def __init__(self, coordinator: DataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "plantPower")
        self._unit_of_measurement = UnitOfPower.WATT


class StorageDCPowerSensor(BaseSensor):
    """Storage DC power sensor."""

    def __init__(self, coordinator: DataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "storageDCPower")
        self._unit_of_measurement = UnitOfPower.WATT
