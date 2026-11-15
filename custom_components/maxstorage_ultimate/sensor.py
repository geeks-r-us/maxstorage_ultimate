"""Platform for Max Storage Ultimate sensor integration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import WORKER_KEY, MaxStorageDataUpdateCoordinator

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

    # Swarm: the master response contains the data of every worker. Workers get
    # their own device, plus one "swarm" device with the sums over all devices.
    known_workers: set[str] = set()

    @callback
    def _add_swarm_entities() -> None:
        new = [key for key in coordinator.workers if key not in known_workers]
        if not new:
            return
        entities: list[MaxStorageSensor] = []
        if not known_workers:
            entities.extend(_swarm_sensors(coordinator))
        for key in new:
            known_workers.add(key)
            entities.extend(_worker_sensors(coordinator, key))
        async_add_entities(entities)

    _add_swarm_entities()
    config.async_on_unload(coordinator.async_add_listener(_add_swarm_entities))


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
        *,
        scope: str = "",
        data_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        device_info: DeviceInfo | None = None,
    ) -> None:
        """Initialize the sensor.

        scope prefixes the unique id ("worker1_", "swarm_"), data_fn selects the
        part of the coordinator data the sensor reads (default: the master).
        """
        super().__init__(coordinator)
        self.entity_description = description
        self._data_fn = data_fn or (lambda data: data)
        self._attr_unique_id = (
            f"{coordinator.api.device_info['Ident']}_{scope}{description.translation_key}"
        )
        self._attr_device_info = device_info or coordinator.device_info

    def _device_data(self) -> dict[str, Any]:
        """Return the data of the device (master, worker or swarm) of this sensor."""
        return self._data_fn(self.coordinator.data) or {}

    @property
    def native_value(self) -> str | int | float | None:
        """Return the state of the sensor."""
        try:
            return self.entity_description.value_fn(self._device_data())
        except (KeyError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes of the sensor."""
        return self.entity_description.attr_fn(self._device_data())

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
        **kwargs: Any,
    ) -> None:
        """Initialize the energy sensor."""
        super().__init__(coordinator, description, **kwargs)
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

    def _power(self) -> float | None:
        """Return the power (W) to integrate, or None if unavailable."""
        try:
            return self.entity_description.value_fn(self._device_data())
        except (KeyError, TypeError):
            return None

    def _start_sample(self) -> None:
        """Remember the current power as the start of the next interval."""
        self._last_power = self._power()
        self._last_time = dt_util.utcnow() if self._last_power is not None else None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Integrate the power since the previous update."""
        power = self._power()
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


def _number(data: dict[str, Any], key: str) -> float | None:
    """Return data[key] as a float, or None if missing or not numeric.

    The device mixes numbers and numeric strings ("-23.5") in its response.
    """
    value = data.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _battery_charge_power(data: dict[str, Any]) -> float | None:
    """Return the power flowing into the battery (W), 0 while discharging.

    The device reports batteryPower as positive while charging and negative
    while discharging.
    """
    power = _number(data, "batteryPower")
    return None if power is None else max(0, power)


def _battery_discharge_power(data: dict[str, Any]) -> float | None:
    """Return the power flowing out of the battery (W), 0 while charging."""
    power = _number(data, "batteryPower")
    return None if power is None else max(0, -power)


def _grid_import_power(data: dict[str, Any]) -> float | None:
    """Return the power drawn from the grid (W), 0 while feeding in.

    The device reports gridPower as positive while feeding in and negative
    while drawing from the grid.
    """
    power = _number(data, "gridPower")
    return None if power is None else max(0, -power)


def _grid_export_power(data: dict[str, Any]) -> float | None:
    """Return the power fed into the grid (W), 0 while drawing."""
    power = _number(data, "gridPower")
    return None if power is None else max(0, power)


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
        key="gridImportPower",
        translation_key="grid_import_power",
        icon="mdi:transmission-tower-export",
        value_fn=_grid_import_power,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
    MaxStorageSensorDescription(
        key="gridExportPower",
        translation_key="grid_export_power",
        icon="mdi:transmission-tower-import",
        value_fn=_grid_export_power,
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
        key="gridImportEnergy",
        translation_key="grid_import_energy",
        icon="mdi:transmission-tower-export",
        value_fn=_grid_import_power,
    ),
    MaxStorageSensorDescription(
        key="gridExportEnergy",
        translation_key="grid_export_energy",
        icon="mdi:transmission-tower-import",
        value_fn=_grid_export_power,
    ),
    MaxStorageSensorDescription(
        key="plantEnergy",
        translation_key="plant_energy",
        icon="mdi:solar-power",
        value_fn=lambda data: _number(data, "plantPower"),
    ),
)


# Sensors that exist per worker / for the whole swarm. Grid and usage values are
# system-wide (measured at the master), so they are not repeated.
WORKER_SENSOR_KEYS = {
    "batterySoC",
    "batteryCapacity",
    "batteryPower",
    "batteryChargePower",
    "batteryDischargePower",
    "plantPower",
    "storageDCPower",
}
WORKER_ENERGY_KEYS = {"batteryChargeEnergy", "batteryDischargeEnergy", "plantEnergy"}
SWARM_SENSOR_KEYS = WORKER_SENSOR_KEYS - {"storageDCPower"}
SWARM_ENERGY_KEYS = {"batteryChargeEnergy", "batteryDischargeEnergy"}


def swarm_data(data: dict[str, Any]) -> dict[str, Any]:
    """Return the sums over the master and all workers.

    batteryPower, batteryCapacity and plantPower are added up, the state of
    charge is weighted by the capacity of each battery.
    """
    swarming = data.get("Swarming")
    devices: list[dict[str, Any]] = [data]
    if isinstance(swarming, dict):
        devices += [
            value
            for key, value in swarming.items()
            if WORKER_KEY.match(key) and isinstance(value, dict)
        ]

    def total(key: str) -> float | None:
        values = [v for d in devices if (v := _number(d, key)) is not None]
        return sum(values) if values else None

    result: dict[str, Any] = {
        "batteryPower": total("batteryPower"),
        "batteryCapacity": total("batteryCapacity"),
        "plantPower": total("plantPower"),
    }
    weighted = [
        (soc, capacity)
        for d in devices
        if (soc := _number(d, "batterySoC")) is not None
        and (capacity := _number(d, "batteryCapacity"))
    ]
    capacity_sum = sum(capacity for _, capacity in weighted)
    result["batterySoC"] = (
        round(sum(soc * capacity for soc, capacity in weighted) / capacity_sum, 2)
        if capacity_sum
        else None
    )
    return result


def _worker_sensors(
    coordinator: MaxStorageDataUpdateCoordinator, key: str
) -> list[MaxStorageSensor]:
    """Create the sensors of one worker."""
    options = {
        "scope": f"{key}_",
        "data_fn": lambda data, key=key: (data.get("Swarming") or {}).get(key),
        "device_info": coordinator.worker_device_info(key),
    }
    sensors: list[MaxStorageSensor] = [
        MaxStorageSensor(coordinator, d, **options)
        for d in SENSOR_TYPES
        if d.key in WORKER_SENSOR_KEYS
    ]
    sensors += [
        MaxStorageEnergySensor(coordinator, d, **options)
        for d in ENERGY_SENSOR_TYPES
        if d.key in WORKER_ENERGY_KEYS
    ]
    return sensors


def _swarm_sensors(
    coordinator: MaxStorageDataUpdateCoordinator,
) -> list[MaxStorageSensor]:
    """Create the sensors with the sums over the whole swarm."""
    options = {
        "scope": "swarm_",
        "data_fn": swarm_data,
        "device_info": coordinator.swarm_device_info,
    }
    sensors: list[MaxStorageSensor] = [
        MaxStorageSensor(coordinator, d, **options)
        for d in SENSOR_TYPES
        if d.key in SWARM_SENSOR_KEYS
    ]
    sensors += [
        MaxStorageEnergySensor(coordinator, d, **options)
        for d in ENERGY_SENSOR_TYPES
        if d.key in SWARM_ENERGY_KEYS
    ]
    return sensors
