"""Attribute discovered swarm workers to the workers of a configured master.

The master response contains the data of every worker but not their network
address. When Home Assistant discovers a worker via zeroconf, its measurements
are compared with the worker blocks of the master to find out which worker it
is. The MAC address and the hostname are then added to that worker's device,
so that e.g. the UniFi integration can merge it with the network client.
"""
from __future__ import annotations

from ipaddress import ip_address
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .client import get_mac_address
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SOC_TOLERANCE = 1.0  # percent
POWER_TOLERANCE = 150.0  # watt, the master data is up to one poll old


def pick_ipv4(discovery_info: Any) -> str:
    """Return the IPv4 address of a discovered device (fallback: its host).

    Home Assistant reports the most recently updated address as host, which may
    be an IPv6 address; the MAC lookup via arp only works with IPv4.
    """
    addresses = []
    for candidate in getattr(discovery_info, "ip_addresses", None) or []:
        try:
            address = ip_address(str(candidate))
        except ValueError:
            continue
        if address.version == 4:
            addresses.append(address)
    for address in addresses:
        if not address.is_link_local and not address.is_unspecified:
            return str(address)
    return str(addresses[0]) if addresses else discovery_info.host


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
    _LOGGER.debug(
        "Discovered worker %s (%s): looking for a match among %d configured entries",
        ident,
        ip,
        len(hass.data.get(DOMAIN, {})),
    )
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
            _LOGGER.debug(
                "Discovered worker %s matches none of the unassigned workers %s "
                "of entry %s",
                ident,
                sorted(candidates),
                master,
            )
            continue

        mac = await hass.async_add_executor_job(get_mac_address, ip)
        if not mac:
            _LOGGER.debug("No MAC address for discovered worker %s (%s)", ident, ip)
            return False
        connection = (dr.CONNECTION_NETWORK_MAC, dr.format_mac(mac))
        device = by_identifier[f"{master}_{key}"]
        # A MAC shared with another device of *this* integration is rejected (e.g.
        # a routed host answering with the router's MAC, which is also the
        # master's). The same MAC on a device of another integration (e.g. UniFi)
        # is how devices are linked and is accepted by newer Home Assistant
        # versions; older ones raise a collision error, see below.
        if any(
            connection in other.connections
            for other in _entry_devices(hass, entry_id)
            if other.id != device.id
        ):
            _LOGGER.debug("MAC %s of worker %s is already used, skipping", mac, ident)
            return False

        try:
            registry.async_update_device(
                device.id,
                new_connections=device.connections | {connection},
                new_identifiers=device.identifiers | {(DOMAIN, ident)},
            )
        except dr.DeviceCollisionError:
            # Older Home Assistant versions let a connection belong to one device
            # only; another integration (e.g. UniFi) already has it.
            _LOGGER.debug(
                "MAC %s of worker %s is registered with a device of another "
                "integration; this Home Assistant version cannot share it",
                mac,
                ident,
            )
            return False
        _LOGGER.debug("Attributed discovered worker %s to %s (%s)", ident, key, mac)
        return True
    return False
