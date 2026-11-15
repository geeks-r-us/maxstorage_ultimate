"""Tests for diagnostics."""
import json
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.maxstorage_ultimate.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .test_init import DATA, _entry, _make_client, _setup


@pytest.fixture
def client_cls():
    with patch("custom_components.maxstorage_ultimate.MaxStorageClient") as cls:
        cls.side_effect = lambda *a, **k: _make_client()
        yield cls


async def test_diagnostics_contains_raw_data(hass: HomeAssistant, client_cls):
    entry = _entry()
    await _setup(hass, entry)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["data"] == DATA
    assert result["last_update_success"] is True
    assert result["update_interval_seconds"] == 30
    assert result["device_info"]["Firmware-Version"] == "3.4.3"


async def test_diagnostics_redacts_sensitive_values(hass: HomeAssistant, client_cls):
    entry = _entry(options={"maxstorage_host": "10.1.2.3", "maxstorage_password": "s3cret"})
    await _setup(hass, entry)

    result = await async_get_config_entry_diagnostics(hass, entry)
    dumped = json.dumps(result)

    for secret in (
        "192.168.1.50",
        "10.1.2.3",
        "solarmax.com",
        "s3cret",
        "abc123",
        "12345",
        "aa:bb:cc:dd:ee:ff",
    ):
        assert secret not in dumped, secret
    assert result["entry"]["data"]["maxstorage_password"] == "**REDACTED**"
    assert result["entry"]["options"]["maxstorage_host"] == "**REDACTED**"


async def test_diagnostics_redacts_weather_location(hass: HomeAssistant, client_cls):
    weather = {"coord": {"lon": 9.1, "lat": 48.2}, "name": "Examplecity"}
    data = {
        **DATA,
        "WeatherData": weather,
        "Swarming": {"swarmJob": "master", "worker1": {"WeatherData": weather}},
    }
    client_cls.side_effect = lambda *a, **k: _client_returning(data)
    entry = _entry()
    await _setup(hass, entry)

    dumped = json.dumps(await async_get_config_entry_diagnostics(hass, entry))
    assert "Examplecity" not in dumped
    assert "48.2" not in dumped
    assert "swarmJob" in dumped  # the rest of the raw data is kept


def _client_returning(data):
    client = _make_client()
    client.get_data.return_value = data
    return client
