"""Tests for integration setup, entities, options reload and unload."""
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.maxstorage_ultimate.client import InvalidHostError
from custom_components.maxstorage_ultimate.const import DOMAIN

DATA = {
    "batterySoC": 55,
    "batteryCapacity": 8000,
    "batteryPower": -300,
    "gridPower": 120,
    "usagePower": 900,
    "plantPower": 1500,
    "storageDCPower": 250,
    "Relais": {"name": ["Heater", "", "Pump"], "value": [1, 0, 0]},
}

ENTRY_DATA = {
    "name": "MaxStorage",
    "maxstorage_host": "192.168.1.50",
    "maxstorage_user": "user",
    "maxstorage_password": "solarmax.com",
}

SENSOR_STATES = {
    "battery_soc": "55",
    "battery_capacity": "8000",
    "battery_power": "-300",
    "grid_power": "120",
    "usage_power": "900",
    "plant_power": "1500",
    "storage_dc_power": "250",
}


def _make_client() -> MagicMock:
    client = MagicMock()
    client.setup = AsyncMock()
    client.close = AsyncMock()
    client.get_data = AsyncMock(return_value=DATA)
    client.mac = "aa:bb:cc:dd:ee:ff"
    client.device_info = {
        "Ident": "abc123",
        "MasterController-Nummer": "12345",
        "Firmware-Version": "3.4.3",
        "Hardware-Version": "1.0",
    }
    return client


@pytest.fixture
def client_cls():
    """Patch the client class used by the integration."""
    with patch("custom_components.maxstorage_ultimate.MaxStorageClient") as cls:
        cls.side_effect = lambda *a, **k: _make_client()
        yield cls


def _entry(**kwargs) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, data={**ENTRY_DATA}, unique_id="abc123", **kwargs
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_creates_sensors(hass: HomeAssistant, client_cls):
    entry = _entry()
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    for key, expected in SENSOR_STATES.items():
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"abc123_{key}")
        assert entity_id, f"sensor {key} not registered"
        assert hass.states.get(entity_id).state == expected


async def test_setup_creates_only_named_relais(hass: HomeAssistant, client_cls):
    await _setup(hass, _entry())

    registry = er.async_get(hass)
    heater = registry.async_get_entity_id("binary_sensor", DOMAIN, "abc123_relais_0")
    unnamed = registry.async_get_entity_id("binary_sensor", DOMAIN, "abc123_relais_1")
    pump = registry.async_get_entity_id("binary_sensor", DOMAIN, "abc123_relais_2")

    assert unnamed is None
    assert hass.states.get(heater).state == "on"
    assert hass.states.get(pump).state == "off"
    assert hass.states.get(heater).name.endswith("Heater")


async def test_device_registered(hass: HomeAssistant, client_cls):
    from homeassistant.helpers import device_registry as dr

    entry = _entry()
    await _setup(hass, entry)
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    device = devices[0]
    assert (DOMAIN, "abc123") in device.identifiers
    assert device.sw_version == "3.4.3"
    assert device.serial_number == "12345"
    assert (dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff") in device.connections


async def test_default_scan_interval(hass: HomeAssistant, client_cls):
    entry = _entry()
    await _setup(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    assert coordinator.update_interval == timedelta(seconds=30)


async def test_options_override_data_and_interval(hass: HomeAssistant, client_cls):
    entry = _entry(
        options={
            "maxstorage_host": "192.168.1.99",
            "maxstorage_user": "admin",
            "maxstorage_password": "secret",
            "scan_interval": 90,
        }
    )
    await _setup(hass, entry)

    client_cls.assert_called_once_with("192.168.1.99", "admin", "secret")
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    assert coordinator.update_interval == timedelta(seconds=90)


async def test_options_update_reloads_entry(hass: HomeAssistant, client_cls):
    entry = _entry()
    await _setup(hass, entry)
    first_client = hass.data[DOMAIN][entry.entry_id]["client"]

    hass.config_entries.async_update_entry(
        entry,
        options={
            "maxstorage_host": "192.168.1.60",
            "maxstorage_user": "user",
            "maxstorage_password": "solarmax.com",
            "scan_interval": 45,
        },
    )
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    first_client.close.assert_awaited_once()  # old session closed on reload
    new = hass.data[DOMAIN][entry.entry_id]
    assert new["client"] is not first_client
    assert client_cls.call_args.args[0] == "192.168.1.60"
    assert new["coordinator"].update_interval == timedelta(seconds=45)


async def test_unload_closes_client(hass: HomeAssistant, client_cls):
    entry = _entry()
    await _setup(hass, entry)
    client = hass.data[DOMAIN][entry.entry_id]["client"]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    client.close.assert_awaited_once()
    assert entry.entry_id not in hass.data[DOMAIN]


async def test_client_setup_failure_closes_client(hass: HomeAssistant):
    client = _make_client()
    client.setup.side_effect = InvalidHostError("nope")
    with patch(
        "custom_components.maxstorage_ultimate.MaxStorageClient", return_value=client
    ):
        entry = _entry()
        await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    client.close.assert_awaited_once()


async def test_first_refresh_failure_closes_client(hass: HomeAssistant):
    client = _make_client()
    client.get_data.side_effect = RuntimeError("down")
    with patch(
        "custom_components.maxstorage_ultimate.MaxStorageClient", return_value=client
    ):
        entry = _entry()
        await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    client.close.assert_awaited_once()
    assert entry.entry_id not in hass.data[DOMAIN]
