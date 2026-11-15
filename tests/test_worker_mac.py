"""Tests for attributing discovered workers to worker devices (MAC address)."""
import copy
import json
from pathlib import Path
import socket
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import pytest

from custom_components.maxstorage_ultimate.const import DOMAIN
from custom_components.maxstorage_ultimate.swarm import match_worker

from .test_init import _entry, _make_client, _setup

FIXTURES = Path(__file__).parent / "fixtures"
MASTER = json.loads((FIXTURES / "swarm_master.json").read_text())
WORKER = json.loads((FIXTURES / "swarm_worker.json").read_text())
WORKER_MAC = "aa:bb:cc:00:11:22"


def _info() -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address="192.168.1.60",
        ip_addresses=["192.168.1.60"],
        hostname="maxstorage-w1.local.",
        name="Worker._maxstorage._tcp.local.",
        port=80,
        properties={},
        type="_maxstorage._tcp.local.",
    )


def _with_workers(*blocks):
    data = copy.deepcopy(MASTER)
    del data["Swarming"]["worker1"]
    for index, overrides in enumerate(blocks, start=1):
        block = copy.deepcopy(MASTER["Swarming"]["worker1"])
        block.update(overrides)
        data["Swarming"][f"worker{index}"] = block
    return data


async def _setup_master(hass, data):
    with patch("custom_components.maxstorage_ultimate.MaxStorageClient") as cls:
        cls.side_effect = lambda *a, **k: _make_client_with(data)
        entry = _entry()
        await _setup(hass, entry)
    return entry


def _make_client_with(data):
    client = _make_client()
    client.get_data.return_value = copy.deepcopy(data)
    return client


async def _discover(hass, mac=WORKER_MAC, worker=WORKER):
    """Run a zeroconf flow for a worker; return (result, probe client class)."""
    with (
        patch("custom_components.maxstorage_ultimate.config_flow.MaxStorageClient") as cls,
        patch("custom_components.maxstorage_ultimate.swarm.get_mac_address", return_value=mac),
        patch(
            "custom_components.maxstorage_ultimate.config_flow.socket.gethostbyname",
            side_effect=socket.gaierror,
        ),
    ):
        client = MagicMock()
        client.get_data = AsyncMock(return_value=worker)
        client.close = AsyncMock()
        cls.return_value = client
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_info()
        )
    return result, cls


def _worker_device(hass, entry, key="worker1"):
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        if (DOMAIN, f"abc123_{key}") in device.identifiers:
            return device
    raise AssertionError(key)


async def test_discovered_worker_gets_mac_and_hostname(hass: HomeAssistant):
    entry = await _setup_master(hass, MASTER)

    result, _ = await _discover(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "swarm_worker"
    device = _worker_device(hass, entry)
    assert (dr.CONNECTION_NETWORK_MAC, WORKER_MAC) in device.connections
    assert (DOMAIN, "maxstorage-w1") in device.identifiers
    assert (DOMAIN, "abc123_worker1") in device.identifiers  # still the same device


async def test_known_worker_is_not_probed_again(hass: HomeAssistant):
    await _setup_master(hass, MASTER)
    await _discover(hass)

    result, cls = await _discover(hass)

    assert result["reason"] == "swarm_worker"
    cls.assert_not_called()


async def test_survives_reload_of_the_master(hass: HomeAssistant):
    entry = await _setup_master(hass, MASTER)
    await _discover(hass)

    with patch("custom_components.maxstorage_ultimate.MaxStorageClient") as cls:
        cls.side_effect = lambda *a, **k: _make_client_with(MASTER)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    device = _worker_device(hass, entry)
    assert (dr.CONNECTION_NETWORK_MAC, WORKER_MAC) in device.connections


async def test_picks_the_matching_worker_of_two(hass: HomeAssistant):
    entry = await _setup_master(
        hass,
        _with_workers(
            {"batterySoC": 18, "batteryPower": -1275},
            {"batterySoC": 7.3, "batteryPower": -520},
        ),
    )

    await _discover(hass)  # worker response: 7.1 %, -523 W

    assert (dr.CONNECTION_NETWORK_MAC, WORKER_MAC) in _worker_device(hass, entry, "worker2").connections
    assert not any(
        c[0] == dr.CONNECTION_NETWORK_MAC for c in _worker_device(hass, entry, "worker1").connections
    )


async def test_ambiguous_workers_are_left_alone(hass: HomeAssistant):
    entry = await _setup_master(
        hass,
        _with_workers(
            {"batterySoC": 7.0, "batteryPower": -500},
            {"batterySoC": 7.2, "batteryPower": -520},
        ),
    )

    result, _ = await _discover(hass)

    assert result["reason"] == "swarm_worker"  # still not offered as a device
    for key in ("worker1", "worker2"):
        assert not any(
            c[0] == dr.CONNECTION_NETWORK_MAC for c in _worker_device(hass, entry, key).connections
        )


async def test_mac_of_another_device_is_not_reused(hass: HomeAssistant):
    entry = await _setup_master(hass, MASTER)

    # the lookup returns the MAC of the master (e.g. a routed host answers with the router)
    await _discover(hass, mac="aa:bb:cc:dd:ee:ff")

    assert not any(
        c[0] == dr.CONNECTION_NETWORK_MAC for c in _worker_device(hass, entry).connections
    )


async def test_missing_mac_is_retried_on_next_discovery(hass: HomeAssistant):
    entry = await _setup_master(hass, MASTER)

    await _discover(hass, mac=None)
    assert (DOMAIN, "maxstorage-w1") not in _worker_device(hass, entry).identifiers

    _, cls = await _discover(hass)  # now the lookup works
    cls.assert_called_once()
    assert (dr.CONNECTION_NETWORK_MAC, WORKER_MAC) in _worker_device(hass, entry).connections


async def test_worker_without_configured_master_is_just_dropped(hass: HomeAssistant):
    result, _ = await _discover(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "swarm_worker"


@pytest.mark.parametrize(
    ("candidates", "worker", "expected"),
    [
        ({}, {"batteryCapacity": 12000}, None),
        ({"w1": {"batteryCapacity": 12000}}, {"batteryCapacity": 12000}, "w1"),
        ({"w1": {"batteryCapacity": 15000}}, {"batteryCapacity": 12000}, None),
        ({"w1": {"batteryCapacity": "12000"}}, {"batteryCapacity": 12000.0}, "w1"),
        ({"w1": {"batteryCapacity": 12000}}, {}, None),
        (
            {
                "w1": {"batteryCapacity": 12000, "batterySoC": 18, "batteryPower": 0},
                "w2": {"batteryCapacity": 12000, "batterySoC": 50, "batteryPower": 0},
            },
            {"batteryCapacity": 12000, "batterySoC": 50.4, "batteryPower": 0},
            "w2",
        ),
        (
            {
                "w1": {"batteryCapacity": 12000, "batterySoC": 50, "batteryPower": 0},
                "w2": {"batteryCapacity": 12000, "batterySoC": 50.3, "batteryPower": 20},
            },
            {"batteryCapacity": 12000, "batterySoC": 50.1, "batteryPower": 10},
            None,
        ),
        (
            {
                "w1": {"batteryCapacity": 12000, "batterySoC": 50, "batteryPower": -1000},
                "w2": {"batteryCapacity": 12000, "batterySoC": 50.2, "batteryPower": 0},
            },
            {"batteryCapacity": 12000, "batterySoC": 50.1, "batteryPower": -900},
            "w1",
        ),
    ],
)
def test_match_worker(candidates, worker, expected):
    assert match_worker(candidates, worker) == expected
