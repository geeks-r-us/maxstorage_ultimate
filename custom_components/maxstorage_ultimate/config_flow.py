"""Config flow for MaxStorage integration."""
from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .client import (
    AuthenticationFailedError,
    InvalidHostError,
    LoginLockedError,
    MaxStorageClient,
    is_swarm_worker,
    normalize_host,
)
from .swarm import async_attribute_worker, is_known_worker
from .workers import HOST, IDENT, PASSWORD, USER, stored_credentials, stored_workers
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_STORAGE_HOST,
    CONF_STORAGE_NAME,
    CONF_STORAGE_PASSWORD,
    CONF_STORAGE_USER,
    DEFAULT_PASSWORD,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USER,
    DOMAIN,
    ERROR_AUTH_INVALID,
    CONF_WORKERS,
    ERROR_CANNOT_CONNECT,
    ERROR_LOGIN_LOCKED,
    ERROR_SWARM_WORKER,
    ERROR_UNKNOWN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    SENSOR_PREFIX,
)

_LOGGER = logging.getLogger(__name__)

PROBE_TIMEOUT = 10  # seconds


class MaxStorageFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a MaxStorage config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize MaxStorage Ultimate flow."""
        _LOGGER.debug("config_flow.py:MaxStorageFlowHandler.__init__")
        self._host: str | None = None
        self._name: str | None = None
        self._user: str = ""
        self._password: str = ""
        self._unique_id: str | None = None
        self._discovery: Any = None  # zeroconf info of a discovered device
        self._uuid: str | None = None  # hostname of a discovered device
        self._data: Any = None  # response of the last login check
        self._device_info: dict[str, Any] = {}
        self._reauth_worker: str | None = None  # worker whose login is renewed

    async def maxstorage_ultimate_init(self) -> str | None:
        """Initialize MaxStorage Ultimate."""
        _LOGGER.debug("config_flow.py:MaxStorageFlowHandler.maxstorage_ultimate_init")

        client = MaxStorageClient(self._host, self._user, self._password)
        try:
            data = await client.get_data()
            self._data = data
            self._device_info = dict(client.get_device_info())
            if is_swarm_worker(data):
                return ERROR_SWARM_WORKER
            await self.async_set_unique_id(client.get_device_info()["Ident"])
        except LoginLockedError:
            return ERROR_LOGIN_LOCKED
        except AuthenticationFailedError:
            return ERROR_AUTH_INVALID
        except InvalidHostError:
            return ERROR_CANNOT_CONNECT
        except Exception as e:  # pylint: disable=broad-except
            _LOGGER.error("Unexpected exception: %s", e)
            return ERROR_UNKNOWN
        finally:
            await client.close()

        return None

    async def _async_probe(
        self, user: str = DEFAULT_USER, password: str = DEFAULT_PASSWORD
    ) -> tuple[Any, dict[str, Any], dict[str, Any] | None]:
        """Query a discovered device.

        Only the publicly documented default credentials or the credentials
        already stored for this very worker are sent, never the ones of the
        master. Returns the response, the device information (firmware, hardware,
        serial number) and the device overview (model name); (None, {}, None) if
        the device could not be queried (unknown role).
        """
        client = MaxStorageClient(self._host, user, password)
        try:
            async with asyncio.timeout(PROBE_TIMEOUT):
                data = await client.get_data()
                info = client.get_device_info()
                try:
                    overview = await client.get_device_overview()
                except Exception:  # pylint: disable=broad-except
                    overview = None  # not available on every firmware
            return (
                data,
                info if isinstance(info, dict) else {},
                overview if isinstance(overview, dict) else None,
            )
        except Exception as error:  # pylint: disable=broad-except
            _LOGGER.debug("Probe of %s failed: %r", self._host, error)
            return None, {}, None
        finally:
            await client.close()

    async def async_check_configured_entry(self) -> ConfigEntry | None:
        """Check if entry is configured."""
        assert self._host
        try:
            current_host = await self.hass.async_add_executor_job(
                socket.gethostbyname, self._host
            )

            for entry in self._async_current_entries(include_ignore=False):
                entry_host = await self.hass.async_add_executor_job(
                    socket.gethostbyname, entry.data[CONF_STORAGE_HOST]
                )
                if entry_host == current_host:
                    return entry
        except socket.gaierror:
            pass

        return None

    @callback
    def _async_create_entry(self) -> FlowResult:
        """Async create flow handler entry."""
        return self.async_create_entry(
            title=self._name,
            data={
                CONF_NAME: self._name,
                CONF_STORAGE_HOST: self._host,
                CONF_STORAGE_USER: self._user,
                CONF_STORAGE_PASSWORD: self._password,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle a flow initiated by the user."""
        _LOGGER.debug(
            "config_flow.py:MaxStorageFlowHandler.async_step_user: %s", user_input
        )

        if user_input is None:
            return self._show_setup_form_init()

        self._name = user_input[CONF_NAME]
        self._host = normalize_host(user_input[CONF_STORAGE_HOST])
        self._user = user_input[CONF_STORAGE_USER]
        self._password = user_input[CONF_STORAGE_PASSWORD]

        if not (error := await self.maxstorage_ultimate_init()):
            if await self.async_check_configured_entry():
                error = "already_configured"
        elif error == ERROR_SWARM_WORKER and await self._async_save_worker():
            return self.async_abort(reason="worker_saved")

        if error:
            return self._show_setup_form_init({"base": error})

        return self._async_create_entry()

    async def async_step_zeroconf(self, discovery_info) -> FlowResult:
        """Handle a discovered MaxStorage device via zeroconf."""
        _LOGGER.debug("Discovered MaxStorage device: %s", discovery_info)

        # Extract relevant information
        self._host = discovery_info.hostname[
            : -1 if discovery_info.hostname.endswith(".") else None
        ]
        self._name = discovery_info.name.replace("._maxstorage._tcp.local.", "")
        self.context[CONF_STORAGE_HOST] = self._host

        if uuid := discovery_info.hostname.split(".")[0]:
            await self.async_set_unique_id(uuid)
            self._abort_if_unique_id_configured()

        for progress in self._async_in_progress():
            if progress.get("context", {}).get(CONF_STORAGE_HOST) == self._host:
                return self.async_abort(reason="already_in_progress")

        if entry := await self.async_check_configured_entry():
            if uuid and not entry.unique_id:
                self.hass.config_entries.async_update_entry(entry, unique_id=uuid)
            return self.async_abort(reason="already_configured")

        # Workers are part of the master's data; do not offer them as devices.
        # Instead, use the discovery to add their MAC address to the worker device.
        self._discovery = discovery_info
        self._uuid = uuid or None
        if uuid and is_known_worker(self.hass, uuid):
            return self.async_abort(reason=ERROR_SWARM_WORKER)
        # The credentials stored for this worker, else the default ones
        stored = stored_credentials(self.hass, uuid) if uuid else None
        user = stored[USER] if stored else DEFAULT_USER
        password = stored[PASSWORD] if stored else DEFAULT_PASSWORD
        data, device_info, overview = await self._async_probe(user, password)
        if is_swarm_worker(data):
            if uuid:
                await async_attribute_worker(
                    self.hass,
                    data,
                    uuid,
                    [*discovery_info.addresses, discovery_info.host],
                    discovery_info.hostname,
                    device_info,
                    (overview or {}).get("model"),
                    connection={
                        HOST: self._host,
                        USER: user,
                        PASSWORD: password,
                        IDENT: uuid,
                    },
                )
            return self.async_abort(reason=ERROR_SWARM_WORKER)
        _LOGGER.debug(
            "Discovered %s is not a swarm worker or could not be queried with the "
            "default credentials (probe result: %s)",
            self._host,
            "no data" if data is None else "master/standalone",
        )

        self.context.update(
            {
                "title_placeholders": {"name": self._name},
                "configuration_url": f"http://{self._host}",
            }
        )

        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle user-confirmation of discovered node."""
        _LOGGER.debug(
            "config_flow.py:MaxStorageFlowHandler.async_step_confirm: %s", user_input
        )

        if user_input is None:
            return self._show_setup_form_confirm()

        self._user = user_input[CONF_STORAGE_USER]
        self._password = user_input[CONF_STORAGE_PASSWORD]

        error = await self.maxstorage_ultimate_init()

        if error == ERROR_SWARM_WORKER:
            # A worker whose login is not the default one: keep the credentials
            # entered here for the master it belongs to.
            saved = await self._async_save_worker()
            return self.async_abort(
                reason="worker_saved" if saved else ERROR_SWARM_WORKER
            )
        if error:
            return self._show_setup_form_confirm({"base": error})

        return self._async_create_entry()

    async def _async_save_worker(self) -> bool:
        """Keep the credentials of a swarm worker for the master it belongs to.

        Returns True if the worker belongs to a configured master.
        """
        info = self._device_info or {}
        ident = self._uuid or info.get("Ident")
        if not ident or not isinstance(self._data, dict):
            return False
        discovery = self._discovery
        addresses = [*discovery.addresses, discovery.host] if discovery else []
        key = await async_attribute_worker(
            self.hass,
            self._data,
            ident,
            addresses,
            discovery.hostname if discovery else self._host,
            info,
            None,
            connection={
                HOST: self._host,
                USER: self._user,
                PASSWORD: self._password,
                IDENT: ident,
            },
        )
        return key is not None

    # --- reauthentication: the login to the master or to a worker failed ---

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Ask for new credentials after a failed login."""
        self._reauth_worker = self.context.get("worker")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for the credentials of the master or of the worker."""
        entry = self._get_reauth_entry()
        worker = self._reauth_worker
        if worker:
            connection = stored_workers(entry).get(worker, {})
            host = connection.get(HOST, "")
            current_user = connection.get(USER, DEFAULT_USER)
            name = f"{entry.title} ({worker.replace('worker', 'Worker ')})"
        else:
            config = {**entry.data, **entry.options}
            host = config[CONF_STORAGE_HOST]
            current_user = config.get(CONF_STORAGE_USER, DEFAULT_USER)
            name = entry.title

        errors: dict[str, str] = {}
        if user_input is not None:
            user = user_input[CONF_STORAGE_USER]
            password = user_input[CONF_STORAGE_PASSWORD]
            error = await self._async_check_login(host, user, password)
            if error is None:
                self._async_update_credentials(entry, worker, user, password)
                return self.async_abort(reason="reauth_successful")
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STORAGE_USER, default=current_user): str,
                    vol.Required(CONF_STORAGE_PASSWORD): str,
                }
            ),
            description_placeholders={"name": name, "host": host},
            errors=errors,
        )

    async def _async_check_login(self, host: str, user: str, password: str) -> str | None:
        """Try to log in; return an error key or None."""
        client = MaxStorageClient(host, user, password)
        try:
            await client.get_data()
        except LoginLockedError:
            return ERROR_LOGIN_LOCKED
        except AuthenticationFailedError:
            return ERROR_AUTH_INVALID
        except InvalidHostError:
            return ERROR_CANNOT_CONNECT
        except Exception as e:  # pylint: disable=broad-except
            _LOGGER.error("Unexpected exception: %s", e)
            return ERROR_UNKNOWN
        finally:
            await client.close()
        return None

    def _async_update_credentials(
        self, entry: ConfigEntry, worker: str | None, user: str, password: str
    ) -> None:
        """Store new credentials of the master or of a worker (reloads the entry)."""
        if worker:
            workers = stored_workers(entry)
            workers[worker] = {**workers.get(worker, {}), USER: user, PASSWORD: password}
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_WORKERS: workers}
            )
            return
        options = dict(entry.options)
        for key, value in ((CONF_STORAGE_USER, user), (CONF_STORAGE_PASSWORD, password)):
            if key in options:  # options override the data of the entry
                options[key] = value
        self.hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_STORAGE_USER: user,
                CONF_STORAGE_PASSWORD: password,
            },
            options=options,
        )

    def _show_setup_form_init(self, errors: dict[str, str] | None = None) -> FlowResult:
        """Show the setup form to the user."""
        _LOGGER.debug(
            "config_flow.py:MaxStorageFlowHandler._show_setup_form_init: %s", errors
        )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=SENSOR_PREFIX): str,
                    vol.Required(CONF_STORAGE_HOST): str,
                    vol.Required(CONF_STORAGE_USER, default=DEFAULT_USER): str,
                    vol.Required(CONF_STORAGE_PASSWORD, default=DEFAULT_PASSWORD): str,
                },
                extra=vol.PREVENT_EXTRA,
            ),
            errors=errors or {},
        )

    def _show_setup_form_confirm(
        self, errors: dict[str, str] | None = None
    ) -> FlowResult:
        """Show the setup form to the user."""
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STORAGE_USER, default=DEFAULT_USER): str,
                    vol.Required(CONF_STORAGE_PASSWORD, default=DEFAULT_PASSWORD): str,
                }
            ),
            description_placeholders={"name": self._name},
            errors=errors or {},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> MaxStorageOptionsFlowHandler:
        """Get the options flow for this handler."""
        return MaxStorageOptionsFlowHandler()


class MaxStorageOptionsFlowHandler(OptionsFlow):
    """Handle MaxStorage options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            user_input = {
                **user_input,
                CONF_STORAGE_HOST: normalize_host(user_input[CONF_STORAGE_HOST]),
            }
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_STORAGE_HOST,
                    default=options.get(
                        CONF_STORAGE_HOST, self.config_entry.data[CONF_STORAGE_HOST]
                    ),
                ): str,
                vol.Required(
                    CONF_STORAGE_USER,
                    default=options.get(
                        CONF_STORAGE_USER, self.config_entry.data[CONF_STORAGE_USER]
                    ),
                ): str,
                vol.Required(
                    CONF_STORAGE_PASSWORD,
                    default=options.get(
                        CONF_STORAGE_PASSWORD,
                        self.config_entry.data[CONF_STORAGE_PASSWORD],
                    ),
                ): str,
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=data_schema)
