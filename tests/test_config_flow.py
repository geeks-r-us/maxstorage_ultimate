"""Tests for the config flow and options flow."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.maxstorage_ultimate.client import (
    AuthenticationFailedError,
    InvalidHostError,
)
from custom_components.maxstorage_ultimate.const import DOMAIN

USER_INPUT = {
    "name": "MaxStorage",
    "maxstorage_host": "192.168.1.50",
    "maxstorage_user": "user",
    "maxstorage_password": "solarmax.com",
}


@pytest.fixture
def mock_client():
    """Patch the client used by the config flow."""
    with patch(
        "custom_components.maxstorage_ultimate.config_flow.MaxStorageClient"
    ) as cls:
        client = MagicMock()
        client.get_data = AsyncMock(return_value={})
        client.close = AsyncMock()
        client.get_device_info.return_value = {"Ident": "abc123"}
        cls.return_value = client
        yield client


@pytest.fixture(autouse=True)
def mock_setup_entry():
    """Don't set up the integration when an entry is created."""
    with patch(
        "custom_components.maxstorage_ultimate.async_setup_entry", return_value=True
    ) as m:
        yield m


@pytest.fixture(autouse=True)
def mock_dns():
    """Resolve hosts deterministically: IPs map to themselves, names fail."""
    import socket

    def fake(host):
        if host.replace(".", "").isdigit():
            return host
        raise socket.gaierror

    with patch(
        "custom_components.maxstorage_ultimate.config_flow.socket.gethostbyname",
        side_effect=fake,
    ):
        yield


async def test_user_flow_success(hass: HomeAssistant, mock_client):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "MaxStorage"
    assert result["data"]["maxstorage_host"] == "192.168.1.50"
    assert result["result"].unique_id == "abc123"
    mock_client.close.assert_awaited()


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (AuthenticationFailedError("bad"), "invalid_auth"),
        (InvalidHostError("bad"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_errors_and_recovery(hass: HomeAssistant, mock_client, exc, error):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    mock_client.get_data.side_effect = exc
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    mock_client.close.assert_awaited()  # session is not leaked on errors

    mock_client.get_data.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_already_configured(hass: HomeAssistant, mock_client):
    MockConfigEntry(
        domain=DOMAIN, data={**USER_INPUT}, unique_id="abc123"
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "already_configured"}


def _zeroconf_info() -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address="192.168.1.50",
        ip_addresses=["192.168.1.50"],
        hostname="maxstorage-abc.local.",
        name="Home._maxstorage._tcp.local.",
        port=80,
        properties={},
        type="_maxstorage._tcp.local.",
    )


async def test_zeroconf_flow(hass: HomeAssistant, mock_client):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=_zeroconf_info(),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"maxstorage_user": "user", "maxstorage_password": "solarmax.com"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home"
    assert result["data"]["maxstorage_host"] == "maxstorage-abc.local"


async def test_zeroconf_confirm_error(hass: HomeAssistant, mock_client):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=_zeroconf_info(),
    )
    mock_client.get_data.side_effect = AuthenticationFailedError("bad")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"maxstorage_user": "user", "maxstorage_password": "wrong"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_zeroconf_already_configured(hass: HomeAssistant, mock_client):
    MockConfigEntry(
        domain=DOMAIN, data={**USER_INPUT}, unique_id="maxstorage-abc"
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=_zeroconf_info(),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, data={**USER_INPUT}, unique_id="abc123")
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    new = {
        "maxstorage_host": "192.168.1.60",
        "maxstorage_user": "admin",
        "maxstorage_password": "secret",
        "scan_interval": 60,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], new
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == new


async def test_options_flow_rejects_invalid_interval(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, data={**USER_INPUT}, unique_id="abc123")
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "maxstorage_host": "h",
                "maxstorage_user": "u",
                "maxstorage_password": "p",
                "scan_interval": 1,
            },
        )


async def test_user_flow_normalizes_host(hass: HomeAssistant, mock_client):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, "maxstorage_host": " http://192.168.1.50/ "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["maxstorage_host"] == "192.168.1.50"


async def test_options_flow_normalizes_host(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, data={**USER_INPUT}, unique_id="abc123")
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "maxstorage_host": "http://192.168.123.3/",
            "maxstorage_user": "user",
            "maxstorage_password": "pw",
            "scan_interval": 30,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["maxstorage_host"] == "192.168.123.3"


WORKER = json.loads((Path(__file__).parent / "fixtures" / "swarm_worker.json").read_text())
MASTER = json.loads((Path(__file__).parent / "fixtures" / "swarm_master.json").read_text())


async def test_user_flow_rejects_swarm_worker(hass: HomeAssistant, mock_client):
    mock_client.get_data.return_value = WORKER
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "swarm_worker"}
    mock_client.close.assert_awaited()

    # entering the master instead works
    mock_client.get_data.return_value = MASTER
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_zeroconf_worker_is_not_offered(hass: HomeAssistant, mock_client):
    mock_client.get_data.return_value = WORKER
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=_zeroconf_info(),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "swarm_worker"


async def test_zeroconf_master_is_offered(hass: HomeAssistant, mock_client):
    mock_client.get_data.return_value = MASTER
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=_zeroconf_info(),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"


async def test_zeroconf_unknown_role_is_still_offered(hass: HomeAssistant, mock_client):
    """With changed credentials the probe fails; the device must still be offered."""
    mock_client.get_data.side_effect = AuthenticationFailedError("bad")
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=_zeroconf_info(),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    mock_client.close.assert_awaited()


async def test_zeroconf_confirm_with_worker_aborts(hass: HomeAssistant, mock_client):
    # the probe with default credentials fails, the entered credentials work
    mock_client.get_data.side_effect = [AuthenticationFailedError("bad"), WORKER]
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=_zeroconf_info(),
    )
    assert result["step_id"] == "confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"maxstorage_user": "me", "maxstorage_password": "custom"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "swarm_worker"


async def test_zeroconf_probe_only_sends_default_credentials(hass: HomeAssistant):
    """Credentials of existing entries must never be sent to a discovered host."""
    MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, "maxstorage_user": "secret-user", "maxstorage_password": "secret-pw"},
        unique_id="other",
    ).add_to_hass(hass)

    with patch(
        "custom_components.maxstorage_ultimate.config_flow.MaxStorageClient"
    ) as cls:
        cls.return_value.get_data = AsyncMock(return_value=MASTER)
        cls.return_value.close = AsyncMock()
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_info(),
        )

    cls.assert_called_once_with("maxstorage-abc.local", "user", "solarmax.com")
