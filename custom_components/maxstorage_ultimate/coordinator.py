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
            model="15SMT Island(1)",  # get model from device_overview
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
        return dr.DeviceInfo(
            identifiers={(DOMAIN, f"{self.api.device_info['Ident']}_{key}")},
            manufacturer="SolarMax",
            model=str(self.workers.get(key, {}).get("deviceTyp") or "Max.Storage"),
            name=f"MaxStorageUltimate Worker {number}",
            **self._via_master(),
        )

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

