"""Diagnostics support for Max.Storage Ultimate."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_STORAGE_HOST,
    CONF_STORAGE_PASSWORD,
    CONF_STORAGE_USER,
    DOMAIN,
)

TO_REDACT_CONFIG = {
    CONF_STORAGE_HOST,
    CONF_STORAGE_USER,
    CONF_STORAGE_PASSWORD,
    "name",
    "unique_id",
}
# The weather block contains the plant's location (coordinates, city name).
TO_REDACT_DATA = {"WeatherData"}
TO_REDACT_DEVICE = {"Ident", "MasterController-Nummer", "Anlagenname", "mac"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Contains the raw device response (`getFullSwarmLiveDataJSON`), which helps
    to support new firmware versions and swarm setups. Credentials, host,
    name, device identifiers and the weather block (location) are redacted.
    """
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime["coordinator"]
    client = runtime["client"]

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT_CONFIG),
            "options": async_redact_data(dict(entry.options), TO_REDACT_CONFIG),
            "unique_id": "**REDACTED**" if entry.unique_id else None,
        },
        "device_info": async_redact_data(
            {**client.device_info, "mac": client.mac}, TO_REDACT_DEVICE
        ),
        "update_interval_seconds": coordinator.update_interval.total_seconds(),
        "last_update_success": coordinator.last_update_success,
        "data": async_redact_data(coordinator.data, TO_REDACT_DATA),
    }
