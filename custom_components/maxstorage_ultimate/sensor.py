"""Platform for Max Storage Ultimate sensor integration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    RestoreSensor,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

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
    sensors.extend(
        MaxStorageEnergySensor(coordinator, description)
        for description in ENERGY_SENSOR_TYPES
    )
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


class MaxStorageEnergySensor(MaxStorageSensor, RestoreSensor):
    """Energy (kWh) obtained by integrating a power value over time.

    The device only reports power, so the energy is accumulated here with the
    trapezoidal rule on every coordinator update. The total is restored after
    a restart. Intervals longer than three polling periods (device or network
    outage) are not integrated, as nothing is known about the power then.
    """

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 3

    def __init__(
        self,
        coordinator: MaxStorageDataUpdateCoordinator,
        description: MaxStorageSensorDescription,
    ) -> None:
        """Initialize the energy sensor."""
        super().__init__(coordinator, description)
        self._energy_kwh: float = 0.0
        self._last_power: float | None = None
        self._last_time = None

    @property
    def native_value(self) -> float:
        """Return the accumulated energy in kWh."""
        return round(self._energy_kwh, 6)

    async def async_added_to_hass(self) -> None:
        """Restore the previous total and start integrating."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None and isinstance(
            last.native_value, (int, float)
        ):
            self._energy_kwh = float(last.native_value)
        self._start_sample()

    def _start_sample(self) -> None:
        """Remember the current power as the start of the next interval."""
        self._last_power = self.entity_description.value_fn(self.coordinator.data)
        self._last_time = dt_util.utcnow() if self._last_power is not None else None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Integrate the power since the previous update."""
        power = self.entity_description.value_fn(self.coordinator.data)
        now = dt_util.utcnow()
        max_gap = 3 * (self.coordinator.update_interval.total_seconds())

        if power is None:
            self._last_power = None
            self._last_time = None
        else:
            if self._last_power is not None and self._last_time is not None:
                seconds = (now - self._last_time).total_seconds()
                if 0 < seconds <= max_gap:
                    average_w = (self._last_power + power) / 2
                    self._energy_kwh += average_w * seconds / 3_600_000
            self._last_power = power
            self._last_time = now
        self.async_write_ha_state()


def _battery_charge_power(data: dict[str, Any]) -> float | None:
    """Return the power flowing into the battery (W), 0 while discharging.

    The device reports batteryPower as positive while charging and negative
    while discharging.
    """
    power = data.get("batteryPower")
    return None if power is None else max(0, power)


def _battery_discharge_power(data: dict[str, Any]) -> float | None:
    """Return the power flowing out of the battery (W), 0 while charging."""
    power = data.get("batteryPower")
    return None if power is None else max(0, -power)


SENSOR_TYPES: tuple[MaxStorageSensorDescription, ...] = (
    MaxStorageSensorDescription(
        key="batterySoC",
        translation_key="battery_soc",
        icon="mdi:battery",
        value_fn=lambda data: data["batterySoC"],
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.BATTERY,
    ),
    MaxStorageSensorDescription(
        key="batteryCapacity",
        translation_key="battery_capacity",
        icon="mdi:battery",
        value_fn=lambda data: data["batteryCapacity"],
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
    ),
    MaxStorageSensorDescription(
        key="batteryPower",
        translation_key="battery_power",
        icon="mdi:battery",
        value_fn=lambda data: data["batteryPower"],
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
    MaxStorageSensorDescription(
        key="batteryChargePower",
        translation_key="battery_charge_power",
        icon="mdi:battery-charging",
        value_fn=_battery_charge_power,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
    MaxStorageSensorDescription(
        key="batteryDischargePower",
        translation_key="battery_discharge_power",
        icon="mdi:battery-arrow-down",
        value_fn=_battery_discharge_power,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
    MaxStorageSensorDescription(
        key="gridPower",
        translation_key="grid_power",
        icon="mdi:transmission-tower",
        value_fn=lambda data: data["gridPower"],
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
    MaxStorageSensorDescription(
        key="usagePower",
        translation_key="usage_power",
        icon="mdi:transmission-tower",
        value_fn=lambda data: data["usagePower"],
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
    MaxStorageSensorDescription(
        key="plantPower",
        translation_key="plant_power",
        icon="mdi:solar-power",
        value_fn=lambda data: data["plantPower"],
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
    MaxStorageSensorDescription(
        key="storageDCPower",
        translation_key="storage_dc_power",
        icon="mdi:solar-power",
        value_fn=lambda data: data["storageDCPower"],
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
)

ENERGY_SENSOR_TYPES: tuple[MaxStorageSensorDescription, ...] = (
    MaxStorageSensorDescription(
        key="batteryChargeEnergy",
        translation_key="battery_charge_energy",
        icon="mdi:battery-charging",
        value_fn=_battery_charge_power,
    ),
    MaxStorageSensorDescription(
        key="batteryDischargeEnergy",
        translation_key="battery_discharge_energy",
        icon="mdi:battery-arrow-down",
        value_fn=_battery_discharge_power,
    ),
    MaxStorageSensorDescription(
        key="plantEnergy",
        translation_key="plant_energy",
        icon="mdi:solar-power",
        value_fn=lambda data: data.get("plantPower"),
    ),
)

