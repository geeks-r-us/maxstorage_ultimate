"""Module contains the MaxStorageDataUpdateCoordinator class."""
from datetime import timedelta
import logging
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import MaxStorageClient
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

WORKER_KEY = re.compile(r"^worker(\d+)$")

# Used until the model name could be read from the device overview.
FALLBACK_MODEL = "15SMT Island(1)"
OVERVIEW_INTERVAL = timedelta(minutes=5)


class MaxStorageDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: MaxStorageClient,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize."""
        self.api = client
        self.model: str | None = None  # read from the device overview
        super().__init__(
            hass=hass,
            config_entry=config_entry,
            logger=_LOGGER,
            name=f"{DOMAIN}-{client.mac}-coordinator",
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self):
        """Fetch data from API."""
        try:
            return (
                await self.api.get_data()
            )  # Replace with your async method to fetch data
        except Exception as e:
            raise UpdateFailed(f"Error communicating with API: {e}") from e

    @property
    def device_info(self) -> dr.DeviceInfo:
        """Return the device information."""
        di = dr.DeviceInfo(
            configuration_url=f"http://{self.api.device_info['Ident']}.local",
            identifiers={(DOMAIN, self.api.device_info["Ident"])},
            manufacturer="SolarMax",
            model=self.model or FALLBACK_MODEL,
            name="MaxStorageUltimate",  # get name from device_overview
            serial_number=self.api.device_info["MasterController-Nummer"],
            sw_version=self.api.device_info["Firmware-Version"],
            hw_version=self.api.device_info["Hardware-Version"],
        )
        if self.api.mac:
            di["connections"] = {(dr.CONNECTION_NETWORK_MAC, self.api.mac)}
        return di

    @property
    def workers(self) -> dict[str, dict]:
        """Return the swarm workers ("worker1", ...) contained in the master data."""
        swarming = (self.data or {}).get("Swarming")
        if not isinstance(swarming, dict):
            return {}
        found = {
            key: value
            for key, value in swarming.items()
            if WORKER_KEY.match(key) and isinstance(value, dict)
        }
        return dict(sorted(found.items(), key=lambda kv: int(WORKER_KEY.match(kv[0])[1])))

    @property
    def master_identifier(self) -> tuple[str, str]:
        """Return the device registry identifier of the master."""
        return (DOMAIN, self.api.device_info["Ident"])

    def _via_master(self) -> dict:
        """Return the DeviceInfo fields that attach a device to the master.

        Newer Home Assistant versions want the id of the master device
        (`via_device_id`), older ones its identifier (`via_device`).
        """
        if "via_device_id" not in dr.DeviceInfo.__annotations__:
            return {"via_device": self.master_identifier}
        if self.config_entry is not None:
            registry = dr.async_get(self.hass)
            for device in dr.async_entries_for_config_entry(
                registry, self.config_entry.entry_id
            ):
                if self.master_identifier in device.identifiers:
                    return {"via_device_id": device.id}
        return {}

    def worker_device_info(self, key: str) -> dr.DeviceInfo:
        """Return the device information of a swarm worker."""
        number = WORKER_KEY.match(key)[1]
        identifier = (DOMAIN, f"{self.api.device_info['Ident']}_{key}")
        info = dr.DeviceInfo(
            identifiers={identifier},
            manufacturer="SolarMax",
            name=f"MaxStorageUltimate Worker {number}",
            **self._via_master(),
        )
        # The full model name is set when the worker is discovered; do not
        # overwrite it with the device type of the measurements.
        if not self._worker_model(identifier):
            info["model"] = str(self.workers.get(key, {}).get("deviceTyp") or "Max.Storage")
        return info

    def _worker_model(self, identifier: tuple[str, str]) -> str | None:
        """Return the model name already stored for the worker device."""
        if self.config_entry is None:
            return None
        registry = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(
            registry, self.config_entry.entry_id
        ):
            if identifier in device.identifiers:
                return device.model if device.model not in (None, "mc2") else None
        return None

    @property
    def swarm_device_info(self) -> dr.DeviceInfo:
        """Return the device information of the swarm (sums over all devices)."""
        return dr.DeviceInfo(
            identifiers={(DOMAIN, f"{self.api.device_info['Ident']}_swarm")},
            manufacturer="SolarMax",
            model="Swarm",
            name="MaxStorageUltimate Swarm",
            **self._via_master(),
        )


class MaxStorageOverviewCoordinator(DataUpdateCoordinator):
    """Reads the device overview (model, DC/DC converters, batteries).

    These values change rarely, so they are read less often than the live data.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: MaxStorageClient,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize."""
        self.api = client
        super().__init__(
            hass=hass,
            config_entry=config_entry,
            logger=_LOGGER,
            name=f"{DOMAIN}-overview-coordinator",
            update_interval=OVERVIEW_INTERVAL,
        )

    async def _async_update_data(self) -> dict:
        """Fetch the device overview."""
        try:
            return await self.api.get_device_overview()
        except Exception as e:
            raise UpdateFailed(f"Error reading the device overview: {e}") from e

    def components(self, kind: str) -> dict[int, dict]:
        """Return the DC/DC converters ("dcdc") or batteries ("batteries") by index."""
        return {c["index"]: c for c in (self.data or {}).get(kind, [])}

