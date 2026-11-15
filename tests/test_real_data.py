"""Regression tests with a sanitized real master+worker response."""
import json
from pathlib import Path
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.maxstorage_ultimate.const import DOMAIN

from .test_init import _entry, _make_client, _setup

REAL = json.loads((Path(__file__).parent / "fixtures" / "swarm_master.json").read_text())


@pytest.fixture
def client_cls():
    with patch("custom_components.maxstorage_ultimate.MaxStorageClient") as cls:

        def make(*a, **k):
            client = _make_client()
            client.get_data.return_value = REAL
            return client

        cls.side_effect = make
        yield cls


async def test_setup_with_real_response(hass: HomeAssistant, client_cls):
    await _setup(hass, _entry())
    registry = er.async_get(hass)

    def state(key):
        return hass.states.get(
            registry.async_get_entity_id("sensor", DOMAIN, f"abc123_{key}")
        ).state

    # gridPower and usagePower are strings in the real response
    assert float(state("usage_power")) == 1235.5
    assert float(state("grid_power")) == -23.5
    assert float(state("grid_import_power")) == 23.5
    assert float(state("grid_export_power")) == 0
    assert float(state("battery_soc")) == 10.4
    assert float(state("battery_charge_power")) == 0
    assert float(state("battery_discharge_power")) == 0
    assert float(state("grid_import_energy")) == 0
