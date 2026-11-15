"""Attribute discovered swarm workers to the workers of a configured master.

The master response contains the data of every worker but not their network
address. When Home Assistant discovers a worker via zeroconf, its measurements
are compared with the worker blocks of the master to find out which worker it
is. The MAC address and the hostname are then added to that worker's device,
so that e.g. the UniFi integration can merge it with the network client.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .client import get_mac_address
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SOC_TOLERANCE = 1.0  # percent
POWER_TOLERANCE = 150.0  # watt, the master data is up to one poll old


def _num(data: dict[str, Any], key: str) -> float | None:
    try:
        return float(data[key])
    except (KeyError, TypeError, ValueError):
        return None


def match_worker(
    candidates: dict[str, dict[str, Any]], worker_data: dict[str, Any]
) -> str | None:
    """Return the key of the candidate that matches the data of a worker.

    The battery capacity must be equal. If several candidates remain, the state
    of charge and the power have to single out exactly one; otherwise the
    attribution is ambiguous and None is returned.
    """
    capacity = _num(worker_data, "batteryCapacity")
    if capacity is None:
        return None
    remaining = {
        key: block
        for key, block in candidates.items()
        if _num(block, "batteryCapacity") == capacity
    }
    if len(remaining) <= 1:
        return next(iter(remaining), None)

    for field, tolerance in (("batterySoC", SOC_TOLERANCE), ("batteryPower", POWER_TOLERANCE)):
        value = _num(worker_data, field)
        if value is None:
            continue
        close = {
            key: block
            for key, block in remaining.items()
            if (other := _num(block, field)) is not None
            and abs(other - value) <= tolerance
        }
        if close:
            remaining = close
        if len(remaining) == 1:
            return next(iter(remaining))
    return None


def _entry_devices(hass: HomeAssistant, entry_id: str) -> list[dr.DeviceEntry]:
    return dr.async_entries_for_config_entry(dr.async_get(hass), entry_id)


def is_known_worker(hass: HomeAssistant, ident: str) -> bool:
    """Return True if a worker with this hostname was already attributed."""
    for entry_id in hass.data.get(DOMAIN, {}):
        for device in _entry_devices(hass, entry_id):
            if (DOMAIN, ident) in device.identifiers:
                return True
    return False


async def async_attribute_worker(
    hass: HomeAssistant, worker_data: dict[str, Any], ident: str, ip: str
) -> bool:
    """Add MAC address and hostname to the worker device that matches worker_data."""
    registry = dr.async_get(hass)
    for entry_id, runtime in hass.data.get(DOMAIN, {}).items():
        coordinator = runtime["coordinator"]
        master = coordinator.api.device_info["Ident"]
        by_identifier = {
            identifier: device
            for device in _entry_devices(hass, entry_id)
            for domain, identifier in device.identifiers
            if domain == DOMAIN
        }

        candidates: dict[str, dict[str, Any]] = {}
        for key, block in coordinator.workers.items():
            device = by_identifier.get(f"{master}_{key}")
            if device is None:
                continue
            attributed = [i for d, i in device.identifiers if d == DOMAIN]
            if len(attributed) > 1:  # already has a hostname identifier
                continue
            candidates[key] = block

        key = match_worker(candidates, worker_data)
        if key is None:
            continue

        mac = await hass.async_add_executor_job(get_mac_address, ip)
        if not mac:
            _LOGGER.debug("No MAC address for discovered worker %s (%s)", ident, ip)
            return False
        mac = dr.format_mac(mac)
        if any(
            (dr.CONNECTION_NETWORK_MAC, mac) in other.connections
            for other in registry.devices.values()
        ):
            _LOGGER.debug("MAC %s of worker %s is already used, skipping", mac, ident)
            return False

        registry.async_update_device(
            by_identifier[f"{master}_{key}"].id,
            merge_connections={(dr.CONNECTION_NETWORK_MAC, mac)},
            merge_identifiers={(DOMAIN, ident)},
        )
        _LOGGER.debug("Attributed discovered worker %s to %s (%s)", ident, key, mac)
        return True
    return False
