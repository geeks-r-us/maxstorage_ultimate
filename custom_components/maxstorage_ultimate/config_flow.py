"""Config flow for MaxStorage integration."""
from __future__ import annotations

import logging
import socket
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .client import AuthenticationFailedError, InvalidHostError, MaxStorageClient
from .const import (
    CONF_STORAGE_HOST,
    CONF_STORAGE_NAME,
    CONF_STORAGE_PASSWORD,
    CONF_STORAGE_USER,
    CONF_STORAGE_VPN,
    DEFAULT_PASSWORD,
    DEFAULT_USER,
    DOMAIN,
    ERROR_AUTH_INVALID,
    ERROR_CANNOT_CONNECT,
    ERROR_UNKNOWN,
    SENSOR_PREFIX,
)

_LOGGER = logging.getLogger(__name__)


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

    async def maxstorage_ultimate_init(self) -> str | None:
        """Initialize MaxStorage Ultimate."""
        _LOGGER.debug("config_flow.py:MaxStorageFlowHandler.maxstorage_ultimate_init")

        try:
            client = MaxStorageClient(self._host, self._user, self._password)
            await client.get_data()
            await self.async_set_unique_id(client.get_device_info()["Ident"])
            await client.close()
        except AuthenticationFailedError:
            return ERROR_AUTH_INVALID
        except InvalidHostError:
            return ERROR_CANNOT_CONNECT
        except Exception as e:  # pylint: disable=broad-except
            _LOGGER.error("Unexpected exception: %s", e)
            return ERROR_UNKNOWN

        return None

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
        self._host = user_input[CONF_STORAGE_HOST]
        self._user = user_input[CONF_STORAGE_USER]
        self._password = user_input[CONF_STORAGE_PASSWORD]

        if not (error := await self.maxstorage_ultimate_init()):
            if await self.async_check_configured_entry():
                error = "already_configured"

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

        if error:
            return self._show_setup_form_confirm({"base": error})

        return self._async_create_entry()

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
