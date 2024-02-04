"""Module contains the MaxStorageDataUpdateCoordinator class."""
from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import MaxStorageClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class MaxStorageDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    def __init__(self, hass: HomeAssistant, client: MaxStorageClient) -> None:
        """Initialize."""
        self.api = client
        super().__init__(
            hass=hass,
            logger=_LOGGER,
            name=f"{DOMAIN}-{client.mac}-coordinator",
            update_interval=timedelta(minutes=0.1),
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
    def unique_id(self) -> str:
        """Return the unique ID."""
        return self._unique_id

    @property
    def mac(self) -> str:
        """Return the MAC address."""
        return dr.format_mac(self._unique_id)
