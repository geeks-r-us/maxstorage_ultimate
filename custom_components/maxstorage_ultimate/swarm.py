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
import socket
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .client import get_mac_address
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SOC_TOLERANCE = 1.0  # percent
POWER_TOLERANCE = 150.0  # watt, the master data is up to one poll old


def pick_ipv4(addresses: list[Any]) -> str | None:
    """Return the first usable IPv4 address of a discovered device, or None.

    The MAC lookup via arp only works with IPv4, while Home Assistant reports
    the most recently updated address as host, which may be an IPv6 address.
    """
    found = []
    for candidate in addresses:
        try:
            address = ip_address(str(candidate))
        except ValueError:
            continue
        if address.version == 4 and not address.is_unspecified:
            found.append(address)
    for address in found:
        if not address.is_link_local:
            return str(address)
    return str(found[0]) if found else None


def mac_from_eui64(address: str) -> str | None:
    """Derive the MAC address from an IPv6 address built from it (EUI-64).

    A SLAAC address without privacy extensions has the MAC address in its last
    64 bits with ff:fe inserted in the middle and the universal/local bit
    flipped. Returns None for any other address.
    """
    try:
        parsed = ip_address(str(address))
    except ValueError:
        return None
    if parsed.version != 6:
        return None
    iid = parsed.packed[8:]
    if iid[3:5] != b"\xff\xfe":
        return None
    mac = bytes([iid[0] ^ 0x02]) + iid[1:3] + iid[5:]
    return ":".join(f"{octet:02x}" for octet in mac)


def _resolve_ipv4(hostname: str) -> str | None:
    try:
        return socket.gethostbyname(hostname)
    except OSError:
        return None


async def async_lookup_mac(
    hass: HomeAssistant, addresses: list[Any], hostname: str | None
) -> str | None:
    """Find the MAC address of a discovered device.

    1. arp with an IPv4 address from the discovery,
    2. arp with the IPv4 address the hostname resolves to (as for the master),
    3. derived from an EUI-64 IPv6 address of the discovery.
    """
    ipv4 = pick_ipv4(addresses)
    if ipv4 is None and hostname:
        ipv4 = await hass.async_add_executor_job(_resolve_ipv4, hostname.rstrip("."))
    if ipv4:
        mac = await hass.async_add_executor_job(get_mac_address, ipv4)
        if mac:
            return mac
    for address in addresses:
        if mac := mac_from_eui64(address):
            return mac
    return None


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


# Fields of the device page (home.php) and the device registry arguments they fill,
# the same mapping as for the master (see coordinator.device_info).
_DEVICE_DETAILS = {
    "sw_version": "Firmware-Version",
    "hw_version": "Hardware-Version",
    "serial_number": "MasterController-Nummer",
}


def _device_details(device_info: dict[str, Any] | None) -> dict[str, str]:
    """Return the device registry arguments for firmware, hardware and serial."""
    if not isinstance(device_info, dict):
        return {}
    return {
        argument: str(value)
        for argument, key in _DEVICE_DETAILS.items()
        if (value := device_info.get(key))
    }


def is_known_worker(hass: HomeAssistant, ident: str) -> bool:
    """Return True if a worker with this hostname is complete.

    Complete means: attributed (hostname and MAC), its firmware version and its
    full model name (not just the device type "mc2") are known. Otherwise it is
    queried again on its next announcement.
    """
    for entry_id in hass.data.get(DOMAIN, {}):
        for device in _entry_devices(hass, entry_id):
            if (
                (DOMAIN, ident) in device.identifiers
                and device.sw_version
                and device.model not in (None, "mc2")
            ):
                return True
    return False


async def async_attribute_worker(
    hass: HomeAssistant,
    worker_data: dict[str, Any],
    ident: str,
    addresses: list[Any],
    hostname: str | None = None,
    device_info: dict[str, Any] | None = None,
    model: str | None = None,
) -> bool:
    """Complete the device of a discovered worker.

    Adds firmware, hardware version, serial number and model name from the
    worker's device pages, and the MAC address and hostname. The worker is recognised by its
    hostname if it was attributed before, otherwise by its measurements.
    """
    registry = dr.async_get(hass)
    details = _device_details(device_info)
    if model:
        details["model"] = model
    _LOGGER.debug(
        "Discovered worker %s (%s): looking for a match among %d configured entries",
        ident,
        addresses,
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

        device = by_identifier.get(ident)
        if device is not None:  # attributed before, only the details are missing
            if details:
                registry.async_update_device(device.id, **details)
                _LOGGER.debug("Updated details of worker %s: %s", ident, details)
            return True

        candidates: dict[str, dict[str, Any]] = {}
        for key, block in coordinator.workers.items():
            candidate = by_identifier.get(f"{master}_{key}")
            if candidate is None:
                continue
            attributed = [i for d, i in candidate.identifiers if d == DOMAIN]
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

        device = by_identifier[f"{master}_{key}"]
        if details:
            registry.async_update_device(device.id, **details)
            _LOGGER.debug("Set details of %s: %s", key, details)

        mac = await async_lookup_mac(hass, addresses, hostname)
        if not mac:
            _LOGGER.debug(
                "No MAC address for discovered worker %s (%s)", ident, addresses
            )
            return False
        connection = (dr.CONNECTION_NETWORK_MAC, dr.format_mac(mac))
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
