"""Module contains the MaxStorageDataUpdateCoordinator class."""
from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import MaxStorageClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class MaxStorageDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    def __init__(self, hass: HomeAssistant, client: MaxStorageClient) -> None:
        """Initialize."""
        self.api = client
        update_interval = timedelta(minutes=0.1)  # Set your desired update interval
        super().__init__(
            hass, _LOGGER, name="MaxStorageUltimate", update_interval=update_interval
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
    def device_info(self) -> DeviceInfo:
        """Return the device information."""
        return DeviceInfo(
            configuration_url=f"http://{self.api.device_info['Ident']}.local",
            identifiers={(DOMAIN, self.api.device_info["Ident"])},
            manufacturer="SolarMax",
            model="15SMT Island(1)",  # get model from device_overview
            name="MaxStorageUltimate",  # get name from device_overview
            serial_number=self.api.device_info["MasterController-Nummer"],
            sw_version=self.api.device_info["Firmware-Version"],
            hw_version=self.api.device_info["Hardware-Version"],
        )
