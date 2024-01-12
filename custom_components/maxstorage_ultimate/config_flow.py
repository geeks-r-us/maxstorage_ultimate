"""Config flow for MaxStorage integration."""
from __future__ import annotations

import logging
from socket import gaierror, getaddrinfo, herror, timeout
from typing import Any

import voluptuous as vol
from voluptuous.schema_builder import Schema

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_STORAGE_HOST,
    CONF_STORAGE_PASSWORD,
    CONF_STORAGE_PORT,
    CONF_STORAGE_USER,
    DEFAULT_PASSWORD,
    DEFAULT_USER,
    DOMAIN,
    SENSOR_PREFIX,
)

_LOGGER = logging.getLogger(__name__)


def step_user_data_schema(
    data: dict[str, Any] = {
        CONF_NAME: SENSOR_PREFIX,
        CONF_STORAGE_USER: DEFAULT_USER,
        CONF_STORAGE_PASSWORD: DEFAULT_PASSWORD,
    },
) -> Schema:
    _LOGGER.debug("config_flow.py:step_user_data_schema: %s", data)
    STEP_USER_DATA_SCHEMA = vol.Schema(
        {
            vol.Required(CONF_NAME, default=data.get(CONF_NAME)): str,
            vol.Required(CONF_STORAGE_HOST, default=data.get(CONF_STORAGE_HOST)): str,
            vol.Required(CONF_STORAGE_USER, default=data.get(CONF_STORAGE_USER)): str,
            vol.Required(
                CONF_STORAGE_PASSWORD, default=data.get(CONF_STORAGE_PASSWORD)
            ): str,
        },
        extra=vol.PREVENT_EXTRA,
    )
    _LOGGER.debug(
        "config_flow.py:step_user_data_schema: STEP_USER_DATA_SCHEMA: %s",
        STEP_USER_DATA_SCHEMA,
    )
    return STEP_USER_DATA_SCHEMA


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """

    _LOGGER.debug("config_flow.py:validate_input: %s", data)

    try:
        getaddrinfo(
            data[CONF_STORAGE_HOST],
            CONF_STORAGE_PORT,
            family=0,
            type=0,
            proto=0,
            flags=0,
        )
    except herror:
        raise InvalidHost
    except gaierror:
        raise CannotConnect
    except timeout:
        raise CannotConnect

    return {"title": data[CONF_STORAGE_HOST]}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        _LOGGER.debug("config_flow.py:ConfigFlow.async_step_user: %s", user_input)

        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=step_user_data_schema()
            )

        errors = {}
        try:
            info = await validate_input(self.hass, user_input)
        except InvalidHost:
            errors["base"] = "invalid_host"
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            _LOGGER.debug(
                "config_flow.py:ConfigFlow.async_step_user: validation passed: %s",
                user_input,
            )
            return self.async_create_entry(
                title=info["title"], data=user_input, options=user_input
            )

        _LOGGER.debug(
            "config_flow.py:ConfigFlow.async_step_user: validation failed: %s",
            user_input,
        )

        return self.async_show_form(
            step_id="user",
            data_schema=step_user_data_schema(),
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidHost(HomeAssistantError):
    """Error to indicate there is invalid hostname or IP address."""
