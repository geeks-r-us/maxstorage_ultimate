"""Direct connections to swarm workers.

The master response contains the live data of every worker, but not the device
overview (model, DC/DC converters, batteries), the firmware or the serial number.
For those, a worker is logged in separately with credentials stored in the
config entry. The credentials come from the discovery (default credentials) or
from the user (config flow, reauthentication).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .client import MaxStorageClient
from .const import CONF_WORKERS
from .coordinator import MaxStorageOverviewCoordinator

_LOGGER = logging.getLogger(__name__)

# keys of a stored connection
HOST = "host"
USER = "user"
PASSWORD = "password"
IDENT = "ident"


@dataclass
class WorkerLink:
    """Login to one worker and its device overview."""

    key: str
    host: str
    client: MaxStorageClient
    overview: MaxStorageOverviewCoordinator

    def device_fields(self) -> dict[str, Any]:
        """Return the device registry fields known from the worker itself."""
        info = self.client.device_info or {}
        fields: dict[str, Any] = {"configuration_url": f"http://{self.host}"}
        for field, key in (
            ("sw_version", "Firmware-Version"),
            ("hw_version", "Hardware-Version"),
            ("serial_number", "MasterController-Nummer"),
        ):
            if info.get(key):
                fields[field] = str(info[key])
        model = (self.overview.data or {}).get("model")
        if model:
            fields["model"] = model
        return fields


def stored_workers(entry: ConfigEntry) -> dict[str, dict[str, str]]:
    """Return the stored connections of the workers by worker key."""
    workers = entry.data.get(CONF_WORKERS)
    return dict(workers) if isinstance(workers, dict) else {}


def stored_credentials(hass: HomeAssistant, ident: str) -> dict[str, str] | None:
    """Return the stored connection of the worker with this hostname, if any."""
    for entry in hass.config_entries.async_entries("maxstorage_ultimate"):
        for connection in stored_workers(entry).values():
            if connection.get(IDENT) == ident:
                return connection
    return None


async def async_setup_worker_links(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, WorkerLink]:
    """Log in to every stored worker and read its device overview."""
    links: dict[str, WorkerLink] = {}
    for key, connection in stored_workers(entry).items():
        host = connection.get(HOST)
        if not host:
            continue
        client = MaxStorageClient(
            host, connection.get(USER, ""), connection.get(PASSWORD, "")
        )
        links[key] = WorkerLink(
            key=key,
            host=host,
            client=client,
            overview=MaxStorageOverviewCoordinator(
                hass, client, entry, worker_key=key
            ),
        )
    if links:
        # A failing worker (offline, wrong password) must not stop the setup.
        await asyncio.gather(
            *(link.overview.async_refresh() for link in links.values())
        )
    return links


async def async_close_worker_links(links: dict[str, WorkerLink]) -> None:
    """Close the sessions of all worker connections."""
    for link in links.values():
        await link.client.close()
