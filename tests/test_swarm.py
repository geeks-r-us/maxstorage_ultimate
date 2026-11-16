"""Tests for swarm support (master response containing worker data)."""
import copy
from datetime import timedelta
import json
from pathlib import Path
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest

from custom_components.maxstorage_ultimate.const import DOMAIN
from custom_components.maxstorage_ultimate.sensor import swarm_data

from .test_init import DATA, _entry, _make_client, _setup

REAL = json.loads((Path(__file__).parent / "fixtures" / "swarm_master.json").read_text())


def _client(data):
    client = _make_client()
    client.get_data.return_value = copy.deepcopy(data)
    return client


@pytest.fixture
def real_client():
    with patch("custom_components.maxstorage_ultimate.MaxStorageClient") as cls:
        cls.side_effect = lambda *a, **k: _client(REAL)
        yield cls


def _state(hass, key):
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"abc123_{key}")
    assert entity_id, key
    return hass.states.get(entity_id)


async def test_devices_are_created(hass: HomeAssistant, real_client, caplog):
    entry = _entry()
    await _setup(hass, entry)
    assert "via_device" not in caplog.text  # no deprecation warning

    devices = {
        next(iter(d.identifiers))[1]: d
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    }
    assert set(devices) == {"abc123", "abc123_worker1", "abc123_swarm"}
    master = devices["abc123"]
    assert devices["abc123_worker1"].via_device_id == master.id
    assert devices["abc123_swarm"].via_device_id == master.id
    assert devices["abc123_worker1"].name == "MaxStorageUltimate Worker 1"


async def test_worker_sensors(hass: HomeAssistant, real_client):
    await _setup(hass, _entry())

    assert float(_state(hass, "worker1_battery_soc").state) == 18
    assert float(_state(hass, "worker1_battery_capacity").state) == 12000
    assert float(_state(hass, "worker1_battery_power").state) == -1275
    assert float(_state(hass, "worker1_battery_charge_power").state) == 0
    assert float(_state(hass, "worker1_battery_discharge_power").state) == 1275
    assert float(_state(hass, "worker1_battery_discharge_energy").state) == 0
    # grid and usage are system-wide: only on the master
    er_ = er.async_get(hass)
    assert er_.async_get_entity_id("sensor", DOMAIN, "abc123_worker1_grid_power") is None
    # master keeps its own values and unique ids
    assert float(_state(hass, "battery_soc").state) == 10.4
    assert float(_state(hass, "battery_power").state) == 0


async def test_swarm_sensors(hass: HomeAssistant, real_client):
    await _setup(hass, _entry())

    assert float(_state(hass, "swarm_battery_power").state) == -1275
    assert float(_state(hass, "swarm_battery_discharge_power").state) == 1275
    assert float(_state(hass, "swarm_battery_charge_power").state) == 0
    assert float(_state(hass, "swarm_battery_capacity").state) == 27000
    # (10.4 * 15000 + 18 * 12000) / 27000
    assert float(_state(hass, "swarm_battery_soc").state) == pytest.approx(13.78, abs=0.01)
    assert float(_state(hass, "swarm_battery_discharge_energy").state) == 0


async def test_no_swarm_entities_without_workers(hass: HomeAssistant):
    with patch("custom_components.maxstorage_ultimate.MaxStorageClient") as cls:
        cls.side_effect = lambda *a, **k: _client(DATA)
        entry = _entry()
        await _setup(hass, entry)

    registry = er.async_get(hass)
    unique_ids = {e.unique_id for e in registry.entities.values()}
    assert not any("worker" in u or "swarm" in u for u in unique_ids)
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1


async def test_worker_appearing_later_is_added(hass: HomeAssistant):
    with patch("custom_components.maxstorage_ultimate.MaxStorageClient") as cls:
        cls.side_effect = lambda *a, **k: _client(DATA)
        entry = _entry()
        await _setup(hass, entry)

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, "abc123_worker1_battery_soc") is None

    data = hass.data[DOMAIN][entry.entry_id]
    data["client"].get_data.return_value = copy.deepcopy(REAL)
    await data["coordinator"].async_refresh()
    await hass.async_block_till_done()

    assert float(_state(hass, "worker1_battery_soc").state) == 18
    assert float(_state(hass, "swarm_battery_capacity").state) == 27000


async def test_worker_disappearing_makes_sensors_unknown(hass: HomeAssistant, real_client):
    entry = _entry()
    await _setup(hass, entry)

    gone = copy.deepcopy(REAL)
    del gone["Swarming"]["worker1"]
    data = hass.data[DOMAIN][entry.entry_id]
    data["client"].get_data.return_value = gone
    await data["coordinator"].async_refresh()
    await hass.async_block_till_done()

    assert _state(hass, "worker1_battery_soc").state == "unknown"
    assert float(_state(hass, "swarm_battery_capacity").state) == 15000


def test_swarm_data_sums_and_weights():
    result = swarm_data(
        {
            "batteryPower": "100",
            "batteryCapacity": 10000,
            "batterySoC": 50,
            "plantPower": 500,
            "Swarming": {
                "swarmJob": "master",
                "worker1": {"batteryPower": -300, "batteryCapacity": 30000, "batterySoC": 10, "plantPower": "250"},
                "worker2": {"batteryPower": "-", "batteryCapacity": 0, "batterySoC": 99},
            },
        }
    )
    assert result["batteryPower"] == -200
    assert result["batteryCapacity"] == 40000
    assert result["plantPower"] == 750
    assert result["batterySoC"] == pytest.approx(20.0)  # (50*10000 + 10*30000) / 40000


def test_swarm_data_without_values():
    result = swarm_data({"Swarming": {"swarmJob": "master"}})
    assert result == {
        "batteryPower": None,
        "batteryCapacity": None,
        "plantPower": None,
        "batterySoC": None,
    }
