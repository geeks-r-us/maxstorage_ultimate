import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant

from .client import MaxStorageClient
from .const import CONF_STORAGE_HOST, CONF_STORAGE_PASSWORD, CONF_STORAGE_USER, DOMAIN
from .coordinator import MaxStorageDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor", "binary_sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MaxStorage Collector from a config entry."""
    _LOGGER.debug("__init__.py:async_setup_entry(%s)", entry.as_dict())

    client = None
    coordinator = None

    if hass.data.get(DOMAIN) is None:
        hass.data[DOMAIN] = {}

    if hass.data[DOMAIN].get(entry.entry_id) is None:
        client = MaxStorageClient(
            entry.data[CONF_STORAGE_HOST],
            entry.data[CONF_STORAGE_USER],
            entry.data[CONF_STORAGE_PASSWORD],
        )
        await client.setup()

        coordinator = MaxStorageDataUpdateCoordinator(hass, client)
        hass.data[DOMAIN][entry.entry_id] = {
            "coordinator": coordinator,
            "client": client,
        }
    else:
        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        client = hass.data[DOMAIN][entry.entry_id]["client"]

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("__init__.py:async_unload_entry(%s)", entry.as_dict())
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Close the aiohttp session
    client = hass.data[DOMAIN][entry.entry_id]["client"]
    await client.close()

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    _LOGGER.debug("__init__.py:update_listener(%s)", entry.as_dict())
    hass.data[DOMAIN][entry.entry_id].config(entry)
    entry.title = entry.options[CONF_NAME]
