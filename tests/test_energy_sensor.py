"""Tests for the integrated energy sensors."""
from datetime import timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.maxstorage_ultimate.const import DOMAIN

from .test_init import DATA, _entry, _make_client, _setup


def _entity_id(hass: HomeAssistant, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"abc123_{key}"
    )
    assert entity_id, key
    return entity_id


async def _refresh(hass, entry, freezer, seconds, **power):
    client = hass.data[DOMAIN][entry.entry_id]["client"]
    client.get_data.return_value = {**DATA, **power}
    freezer.tick(timedelta(seconds=seconds))
    await hass.data[DOMAIN][entry.entry_id]["coordinator"].async_refresh()
    await hass.async_block_till_done()


@pytest.fixture
def client_cls():
    with patch("custom_components.maxstorage_ultimate.MaxStorageClient") as cls:
        cls.side_effect = lambda *a, **k: _make_client()
        yield cls


async def test_energy_starts_at_zero(hass: HomeAssistant, client_cls):
    await _setup(hass, _entry())
    for key in ("battery_charge_energy", "battery_discharge_energy", "plant_energy"):
        assert float(hass.states.get(_entity_id(hass, key)).state) == 0.0


async def test_energy_is_integrated_trapezoidal(
    hass: HomeAssistant, client_cls, freezer
):
    entry = _entry()
    entry.add_to_hass(hass)
    client_cls.side_effect = lambda *a, **k: _make_client_with(batteryPower=1000)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # charging 1000 W -> 3000 W over 30 s: average 2000 W * 30 s = 16.667 Wh
    await _refresh(hass, entry, freezer, 30, batteryPower=3000)
    charge = hass.states.get(_entity_id(hass, "battery_charge_energy"))
    discharge = hass.states.get(_entity_id(hass, "battery_discharge_energy"))
    assert float(charge.state) == pytest.approx(0.016667, abs=1e-6)
    assert float(discharge.state) == 0.0
    assert charge.attributes["unit_of_measurement"] == "kWh"
    assert charge.attributes["state_class"] == "total_increasing"
    assert charge.attributes["device_class"] == "energy"

    # power flips from +3000 W (charging) to -600 W (discharging): each sign
    # is integrated separately as its own power curve
    await _refresh(hass, entry, freezer, 30, batteryPower=-600)
    discharge = hass.states.get(_entity_id(hass, "battery_discharge_energy"))
    # charge side: (3000 + 0) / 2 W * 30 s = 12.5 Wh more
    charge = hass.states.get(_entity_id(hass, "battery_charge_energy"))
    assert float(charge.state) == pytest.approx(0.016667 + 0.0125, abs=1e-6)
    # discharge side: (0 + 600) / 2 W * 30 s = 2.5 Wh
    assert float(discharge.state) == pytest.approx(0.0025, abs=1e-6)


async def test_gap_is_not_integrated(hass: HomeAssistant, client_cls, freezer):
    entry = _entry()
    entry.add_to_hass(hass)
    client_cls.side_effect = lambda *a, **k: _make_client_with(plantPower=2000)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # 10 minutes without update (> 3 polling periods): nothing is added
    await _refresh(hass, entry, freezer, 600, plantPower=2000)
    assert float(hass.states.get(_entity_id(hass, "plant_energy")).state) == 0.0

    # the next regular interval counts again: 2000 W * 30 s = 16.667 Wh
    await _refresh(hass, entry, freezer, 30, plantPower=2000)
    assert float(hass.states.get(_entity_id(hass, "plant_energy")).state) == (
        pytest.approx(0.016667, abs=1e-6)
    )


async def test_total_is_restored(hass: HomeAssistant, client_cls):
    entity_id = "sensor.maxstorageultimate_plant_energy"
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(entity_id, "12.5"),
                {
                    "native_value": 12.5,
                    "native_unit_of_measurement": "kWh",
                },
            ),
        ),
    )
    await _setup(hass, _entry())
    assert float(hass.states.get(_entity_id(hass, "plant_energy")).state) == 12.5


def _make_client_with(**overrides):
    client = _make_client()
    client.get_data.return_value = {**DATA, **overrides}
    return client
