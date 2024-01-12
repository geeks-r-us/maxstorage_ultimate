"""Module contains the MaxStorageDataUpdateCoordinator class."""
from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import MaxStorageClient

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
