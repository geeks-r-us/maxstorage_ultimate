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
from custom_components.maxstorage_ultimate.swarm import (
    mac_from_eui64,
    match_worker,
    pick_ipv4,
)

from .test_init import _entry, _make_client, _setup

FIXTURES = Path(__file__).parent / "fixtures"
MASTER = json.loads((FIXTURES / "swarm_master.json").read_text())
WORKER = json.loads((FIXTURES / "swarm_worker.json").read_text())
WORKER_MAC = "aa:bb:cc:00:11:22"
WORKER_OVERVIEW = {"model": "12SMT Island (2)", "dcdc": [], "batteries": []}
WORKER_INFO = {
    "Ident": "maxstorage-w1",
    "Firmware-Version": "3.4.5",
    "Hardware-Version": "MCv2.5 Serie",
    "MasterController-Nummer": "98765",
    "Anlagenname": "Home",
}


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


def _worker_client():
    client = MagicMock()
    client.get_data = AsyncMock(return_value=WORKER)
    client.get_device_info = MagicMock(return_value=WORKER_INFO)
    client.device_info = WORKER_INFO
    client.get_device_overview = AsyncMock(return_value=WORKER_OVERVIEW)
    client.close = AsyncMock()
    return client


def _factory(master_data):
    """MaxStorageClient replacement: the worker host answers as the worker."""

    def make(host, *args, **kwargs):
        if "w1" in str(host):
            return _worker_client()
        return _make_client_with(master_data)

    return make


# storing the connection of a worker reloads the master; not needed in most tests
def no_reload():
    return patch(
        "homeassistant.config_entries.ConfigEntries.async_reload",
        AsyncMock(return_value=True),
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


async def _discover(hass, mac=WORKER_MAC, worker=WORKER, info=WORKER_INFO):
    """Run a zeroconf flow for a worker; return (result, probe client class)."""
    with (
        patch("custom_components.maxstorage_ultimate.config_flow.MaxStorageClient") as cls,
        patch("custom_components.maxstorage_ultimate.swarm.get_mac_address", return_value=mac),
        patch(
            "custom_components.maxstorage_ultimate.config_flow.socket.gethostbyname",
            side_effect=socket.gaierror,
        ),
        no_reload(),
    ):
        client = MagicMock()
        client.get_data = AsyncMock(return_value=worker)
        client.get_device_info = MagicMock(return_value=info)
        client.get_device_overview = AsyncMock(return_value=WORKER_OVERVIEW)
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

    with patch("custom_components.maxstorage_ultimate.MaxStorageClient") as cls, patch(
        "custom_components.maxstorage_ultimate.workers.MaxStorageClient"
    ) as worker_cls:
        cls.side_effect = _factory(MASTER)
        worker_cls.side_effect = _factory(MASTER)
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


async def test_mac_of_another_integration_is_linked(hass: HomeAssistant):
    """UniFi (or any other integration) having the MAC is the wanted case."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = await _setup_master(hass, MASTER)
    unifi = MockConfigEntry(domain="unifi")
    unifi.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=unifi.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, WORKER_MAC)},
        name="Inverter worker",
    )

    result, _ = await _discover(hass)  # must not raise in any version

    assert result["reason"] == "swarm_worker"
    device = _worker_device(hass, entry)
    if hasattr(dr.DeviceEntry, "is_composite_device"):
        # newer Home Assistant: devices of different integrations share the MAC
        assert (dr.CONNECTION_NETWORK_MAC, WORKER_MAC) in device.connections
        assert (DOMAIN, "maxstorage-w1") in device.identifiers
    else:
        # older Home Assistant: the connection belongs to the other device, skipped
        assert (dr.CONNECTION_NETWORK_MAC, WORKER_MAC) not in device.connections


def _info_with(*addresses, host=None):
    from ipaddress import ip_address

    parsed = [ip_address(a) for a in addresses]
    return ZeroconfServiceInfo(
        ip_address=parsed[0],
        ip_addresses=parsed,
        hostname="maxstorage-w1.local.",
        name="Worker._maxstorage._tcp.local.",
        port=80,
        properties={},
        type="_maxstorage._tcp.local.",
    )


@pytest.mark.parametrize(
    ("addresses", "expected"),
    [
        (["192.168.1.60"], "192.168.1.60"),
        (["2001:db8::60", "192.168.1.60"], "192.168.1.60"),  # IPv6 is the host
        (["fd00::60", "169.254.3.4", "192.168.1.60"], "192.168.1.60"),
        (["2001:db8::60", "169.254.3.4"], "169.254.3.4"),  # only link-local IPv4
        (["2001:db8::60"], None),  # no IPv4 at all
        ([], None),
        (["not-an-ip", "0.0.0.0"], None),
    ],
)
def test_pick_ipv4(addresses, expected):
    assert pick_ipv4(addresses) == expected


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        # from a real worker: SLAAC address built from the MAC
        ("2a02:8071:79d0:3df:219:1bff:fed1:9b9d", "00:19:1b:d1:9b:9d"),
        ("fe80::219:1bff:fed1:9b9d", "00:19:1b:d1:9b:9d"),
        ("2001:db8::60", None),  # no EUI-64 (e.g. privacy address)
        ("192.168.1.60", None),
        ("not-an-ip", None),
    ],
)
def test_mac_from_eui64(address, expected):
    assert mac_from_eui64(address) == expected


async def _discover_with(hass, addresses, resolve=None):
    """Zeroconf flow for a worker announced with the given addresses."""
    from ipaddress import ip_address

    parsed = [ip_address(a) for a in addresses]

    def fake_dns(host):
        if host == "maxstorage-w1.local":  # the discovered worker
            if resolve:
                return resolve
            raise socket.gaierror
        return host  # IPs of configured entries resolve to themselves

    info = ZeroconfServiceInfo(
        ip_address=parsed[0],
        ip_addresses=parsed,
        hostname="maxstorage-w1.local.",
        name="Worker._maxstorage._tcp.local.",
        port=80,
        properties={},
        type="_maxstorage._tcp.local.",
    )
    with (
        patch("custom_components.maxstorage_ultimate.config_flow.MaxStorageClient") as cls,
        no_reload(),
        patch(
            "custom_components.maxstorage_ultimate.swarm.get_mac_address",
            return_value=WORKER_MAC,
        ) as arp,
        patch("socket.gethostbyname", side_effect=fake_dns) as dns,
    ):
        client = MagicMock()
        client.get_data = AsyncMock(return_value=WORKER)
        client.get_device_info = MagicMock(return_value=WORKER_INFO)
        client.get_device_overview = AsyncMock(return_value=WORKER_OVERVIEW)
        client.close = AsyncMock()
        cls.return_value = client
        await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info
        )
    return arp, dns


async def test_ipv6_only_worker_resolves_ipv4_via_hostname(hass: HomeAssistant):
    entry = await _setup_master(hass, MASTER)

    arp, dns = await _discover_with(hass, ["2a02:8071:79d0:3df::1"], resolve="192.168.1.60")

    assert "maxstorage-w1.local" in [c.args[0] for c in dns.call_args_list]
    arp.assert_called_once_with("192.168.1.60")
    assert (dr.CONNECTION_NETWORK_MAC, WORKER_MAC) in _worker_device(hass, entry).connections


async def test_ipv6_only_worker_mac_is_derived_from_the_address(hass: HomeAssistant):
    """The case from a real setup: only an EUI-64 IPv6 address, name not resolvable."""
    entry = await _setup_master(hass, MASTER)

    arp, _ = await _discover_with(hass, ["2a02:8071:79d0:3df:219:1bff:fed1:9b9d"])

    arp.assert_not_called()  # arp cannot handle IPv6
    assert (dr.CONNECTION_NETWORK_MAC, "00:19:1b:d1:9b:9d") in _worker_device(hass, entry).connections


async def test_ipv6_only_worker_without_eui64_is_left_alone(hass: HomeAssistant):
    entry = await _setup_master(hass, MASTER)

    await _discover_with(hass, ["2001:db8::60"])

    assert not any(
        c[0] == dr.CONNECTION_NETWORK_MAC for c in _worker_device(hass, entry).connections
    )


async def test_ipv6_host_still_uses_the_ipv4_for_the_mac(hass: HomeAssistant):
    entry = await _setup_master(hass, MASTER)
    info = _info_with("2001:db8::60", "192.168.1.60")

    with (
        patch("custom_components.maxstorage_ultimate.config_flow.MaxStorageClient") as cls,
        patch("custom_components.maxstorage_ultimate.swarm.get_mac_address", return_value=WORKER_MAC) as lookup,
        patch(
            "custom_components.maxstorage_ultimate.config_flow.socket.gethostbyname",
            side_effect=socket.gaierror,
        ),
        no_reload(),
    ):
        client = MagicMock()
        client.get_data = AsyncMock(return_value=WORKER)
        client.get_device_info = MagicMock(return_value=WORKER_INFO)
        client.get_device_overview = AsyncMock(return_value=WORKER_OVERVIEW)
        client.close = AsyncMock()
        cls.return_value = client
        await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info
        )

    lookup.assert_called_once_with("192.168.1.60")
    assert (dr.CONNECTION_NETWORK_MAC, WORKER_MAC) in _worker_device(hass, entry).connections


async def test_worker_details_are_added(hass: HomeAssistant):
    entry = await _setup_master(hass, MASTER)

    await _discover(hass)

    device = _worker_device(hass, entry)
    assert device.sw_version == "3.4.5"
    assert device.hw_version == "MCv2.5 Serie"
    assert device.serial_number == "98765"


async def test_worker_details_are_added_even_without_mac(hass: HomeAssistant):
    entry = await _setup_master(hass, MASTER)

    await _discover(hass, mac=None)

    device = _worker_device(hass, entry)
    assert device.sw_version == "3.4.5"
    assert device.serial_number == "98765"
    assert not any(c[0] == dr.CONNECTION_NETWORK_MAC for c in device.connections)
    assert (DOMAIN, "maxstorage-w1") not in device.identifiers  # still retried


async def test_details_are_added_to_an_already_attributed_worker(hass: HomeAssistant):
    """Workers attributed by an earlier version only have the MAC and hostname."""
    entry = await _setup_master(hass, MASTER)
    await _discover(hass, info={})  # no details on the page
    device = _worker_device(hass, entry)
    assert device.sw_version is None
    assert (dr.CONNECTION_NETWORK_MAC, WORKER_MAC) in device.connections

    result, cls = await _discover(hass)  # not complete yet, so queried again

    cls.assert_called_once()
    assert result["reason"] == "swarm_worker"
    device = _worker_device(hass, entry)
    assert device.sw_version == "3.4.5"
    assert (dr.CONNECTION_NETWORK_MAC, WORKER_MAC) in device.connections

    _, cls = await _discover(hass)  # complete now
    cls.assert_not_called()


async def test_details_survive_a_reload_of_the_master(hass: HomeAssistant):
    entry = await _setup_master(hass, MASTER)
    await _discover(hass)

    with patch("custom_components.maxstorage_ultimate.MaxStorageClient") as cls, patch(
        "custom_components.maxstorage_ultimate.workers.MaxStorageClient"
    ) as worker_cls:
        cls.side_effect = _factory(MASTER)
        worker_cls.side_effect = _factory(MASTER)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    device = _worker_device(hass, entry)
    assert device.sw_version == "3.4.5"
    assert device.hw_version == "MCv2.5 Serie"
    assert device.serial_number == "98765"


async def test_worker_gets_the_full_model_name(hass: HomeAssistant):
    entry = await _setup_master(hass, MASTER)

    await _discover(hass)

    assert _worker_device(hass, entry).model == "12SMT Island (2)"


async def test_worker_model_survives_a_reload(hass: HomeAssistant):
    """The device type of the measurements ("mc2") must not replace it again."""
    entry = await _setup_master(hass, MASTER)
    await _discover(hass)

    with patch("custom_components.maxstorage_ultimate.MaxStorageClient") as cls, patch(
        "custom_components.maxstorage_ultimate.workers.MaxStorageClient"
    ) as worker_cls:
        cls.side_effect = _factory(MASTER)
        worker_cls.side_effect = _factory(MASTER)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert _worker_device(hass, entry).model == "12SMT Island (2)"


async def test_worker_without_overview_keeps_the_device_type(hass: HomeAssistant):
    entry = await _setup_master(hass, MASTER)
    assert _worker_device(hass, entry).model == "mc2"

    with patch("custom_components.maxstorage_ultimate.config_flow.MaxStorageClient") as cls, patch(
        "custom_components.maxstorage_ultimate.swarm.get_mac_address", return_value=WORKER_MAC
    ), patch("socket.gethostbyname", side_effect=lambda h: h), no_reload():
        client = MagicMock()
        client.get_data = AsyncMock(return_value=WORKER)
        client.get_device_info = MagicMock(return_value=WORKER_INFO)
        client.get_device_overview = AsyncMock(side_effect=Exception("no such page"))
        client.close = AsyncMock()
        cls.return_value = client
        await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_info()
        )

    device = _worker_device(hass, entry)
    assert device.model == "mc2"
    assert device.sw_version == "3.4.5"  # the rest still works
    assert (dr.CONNECTION_NETWORK_MAC, WORKER_MAC) in device.connections
