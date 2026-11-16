import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .client import AuthenticationFailedError, LoginLockedError, MaxStorageClient
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_STORAGE_HOST,
    CONF_STORAGE_PASSWORD,
    CONF_STORAGE_USER,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .workers import async_close_worker_links, async_setup_worker_links
from .coordinator import (
    MaxStorageDataUpdateCoordinator,
    MaxStorageOverviewCoordinator,
)

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
        # Options (set via the options flow) override the original entry data
        config = {**entry.data, **entry.options}
        client = MaxStorageClient(
            config[CONF_STORAGE_HOST],
            config[CONF_STORAGE_USER],
            config[CONF_STORAGE_PASSWORD],
        )
        try:
            await client.setup()
        except LoginLockedError as e:
            await client.close()
            raise ConfigEntryNotReady(str(e)) from e
        except AuthenticationFailedError as e:
            await client.close()
            raise ConfigEntryAuthFailed(str(e)) from e
        except Exception:
            await client.close()
            raise

        coordinator = MaxStorageDataUpdateCoordinator(
            hass,
            client,
            config.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            entry,
        )
        hass.data[DOMAIN][entry.entry_id] = {
            "coordinator": coordinator,
            "client": client,
        }
    else:
        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        client = hass.data[DOMAIN][entry.entry_id]["client"]

    # Fetch initial data
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        await client.close()
        raise

    # Model name, DC/DC converters and batteries come from the device overview.
    # Not every firmware provides it, so a failure does not stop the setup.
    runtime = hass.data[DOMAIN][entry.entry_id]
    overview = runtime.get("overview")
    if overview is None:
        overview = runtime["overview"] = MaxStorageOverviewCoordinator(
            hass, client, entry
        )
    await overview.async_refresh()
    if overview.data and overview.data.get("model"):
        coordinator.model = overview.data["model"]

    # Workers with stored credentials: own login, device overview, firmware, model
    links = await async_setup_worker_links(hass, entry)
    runtime["workers"] = links
    coordinator.worker_links = links

    # Workers and the swarm refer to the master with via_device, so it has to exist.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **coordinator.device_info
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("__init__.py:async_unload_entry(%s)", entry.as_dict())
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        # Close the aiohttp sessions
        await data["client"].close()
        await async_close_worker_links(data.get("workers", {}))
    return unload_ok


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update by reloading the entry with the new settings."""
    _LOGGER.debug("__init__.py:update_listener(%s)", entry.as_dict())
    await hass.config_entries.async_reload(entry.entry_id)
