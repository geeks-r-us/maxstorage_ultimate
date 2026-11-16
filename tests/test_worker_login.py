"""Tests for the credentials of the master and of the swarm workers."""
import copy
import socket
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.maxstorage_ultimate.client import (
    AuthenticationFailedError,
    LoginLockedError,
)
from custom_components.maxstorage_ultimate.const import DOMAIN

from .test_init import ENTRY_DATA, _make_client
from .test_worker_mac import (
    MASTER,
    WORKER,
    WORKER_INFO,
    WORKER_MAC,
    WORKER_OVERVIEW,
    _info,
    _worker_client,
    no_reload,
)

STORED = {
    "host": "maxstorage-w1.local",
    "user": "worker-user",
    "password": "worker-pw",
    "ident": "maxstorage-w1",
}
WORKER_OVERVIEW_FULL = {
    "model": "12SMT Island (2)",
    "dcdc": [
        {"index": 1, "serial": "11", "state": "In Betrieb"},
        {"index": 2, "serial": "12", "state": "Standby"},
    ],
    "batteries": [{"index": 1, "serial": "21", "state": "In Betrieb"}],
}


def _master_client():
    client = _make_client()
    client.get_data.return_value = copy.deepcopy(MASTER)
    return client


def _entry(workers=None, **extra) -> MockConfigEntry:
    data = {**ENTRY_DATA}
    if workers is not None:
        data["workers"] = workers
    return MockConfigEntry(
        domain=DOMAIN, data=data, unique_id="abc123", title="MaxStorage", **extra
    )


async def _setup(hass, entry, master=None, worker=None):
    """Set up the entry with a master client and, if given, a worker client."""
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.maxstorage_ultimate.MaxStorageClient",
            return_value=master or _master_client(),
        ),
        patch(
            "custom_components.maxstorage_ultimate.workers.MaxStorageClient",
            return_value=worker or _worker_link_client(),
        ) as worker_cls,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return worker_cls


def _worker_link_client(overview=None):
    client = _worker_client()
    client.device_info = WORKER_INFO
    client.get_device_overview = AsyncMock(return_value=overview or WORKER_OVERVIEW_FULL)
    return client


def _reauth_flows(hass):
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"].get("source") == config_entries.SOURCE_REAUTH
    ]


def _login_client(error=None):
    client = MagicMock()
    client.get_data = AsyncMock(
        side_effect=error, return_value=copy.deepcopy(MASTER)
    )
    client.close = AsyncMock()
    return client


# ---- the master: login fails ---------------------------------------------------


async def test_refused_login_at_setup_asks_for_new_credentials(hass: HomeAssistant):
    entry = _entry()
    client = _master_client()
    client.setup.side_effect = AuthenticationFailedError("Login refused")

    await _setup(hass, entry, master=client)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert len(_reauth_flows(hass)) == 1
    client.close.assert_awaited()


async def test_locked_login_at_setup_is_retried_not_reauthenticated(hass: HomeAssistant):
    entry = _entry()
    client = _master_client()
    client.setup.side_effect = LoginLockedError("locked for 30 seconds")

    await _setup(hass, entry, master=client)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert _reauth_flows(hass) == []


async def test_refused_login_while_running_asks_for_new_credentials(hass: HomeAssistant):
    entry = _entry()
    await _setup(hass, entry)
    assert _reauth_flows(hass) == []
    runtime = hass.data[DOMAIN][entry.entry_id]

    runtime["client"].get_data.side_effect = AuthenticationFailedError("password changed")
    await runtime["coordinator"].async_refresh()
    await hass.async_block_till_done()

    assert len(_reauth_flows(hass)) == 1


async def test_locked_login_while_running_does_not_ask(hass: HomeAssistant):
    entry = _entry()
    await _setup(hass, entry)
    runtime = hass.data[DOMAIN][entry.entry_id]

    runtime["client"].get_data.side_effect = LoginLockedError("locked")
    await runtime["coordinator"].async_refresh()
    await hass.async_block_till_done()

    assert _reauth_flows(hass) == []


async def test_reauth_of_the_master(hass: HomeAssistant):
    entry = _entry()
    await _setup(hass, entry)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with (
        patch(
            "custom_components.maxstorage_ultimate.config_flow.MaxStorageClient",
            return_value=_login_client(),
        ) as cls,
        no_reload(),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"maxstorage_user": "new-user", "maxstorage_password": "new-pw"},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    cls.assert_called_once_with("192.168.1.50", "new-user", "new-pw")
    assert entry.data["maxstorage_user"] == "new-user"
    assert entry.data["maxstorage_password"] == "new-pw"


async def test_reauth_changes_the_options_that_override_the_data(hass: HomeAssistant):
    entry = _entry(
        options={
            "maxstorage_host": "10.0.0.5",
            "maxstorage_user": "old",
            "maxstorage_password": "old",
            "scan_interval": 60,
        }
    )
    await _setup(hass, entry)
    result = await entry.start_reauth_flow(hass)

    with (
        patch(
            "custom_components.maxstorage_ultimate.config_flow.MaxStorageClient",
            return_value=_login_client(),
        ) as cls,
        no_reload(),
    ):
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"maxstorage_user": "new-user", "maxstorage_password": "new-pw"},
        )

    cls.assert_called_once_with("10.0.0.5", "new-user", "new-pw")  # the host of the options
    assert entry.options["maxstorage_user"] == "new-user"
    assert entry.options["maxstorage_password"] == "new-pw"
    assert entry.options["scan_interval"] == 60


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthenticationFailedError("no"), "invalid_auth"),
        (LoginLockedError("locked"), "login_locked"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_reauth_with_wrong_credentials_stays_open(hass: HomeAssistant, error, expected):
    entry = _entry()
    await _setup(hass, entry)
    result = await entry.start_reauth_flow(hass)

    with patch(
        "custom_components.maxstorage_ultimate.config_flow.MaxStorageClient",
        return_value=_login_client(error),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"maxstorage_user": "user", "maxstorage_password": "wrong"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}
    assert entry.data["maxstorage_password"] == "solarmax.com"


# ---- workers with their own login -----------------------------------------------


async def test_connected_worker_gets_sensors_and_details(hass: HomeAssistant):
    entry = _entry(workers={"worker1": STORED})

    worker_cls = await _setup(hass, entry)

    worker_cls.assert_called_once_with("maxstorage-w1.local", "worker-user", "worker-pw")
    registry = er.async_get(hass)
    battery = hass.states.get(
        registry.async_get_entity_id("sensor", DOMAIN, "abc123_worker1_batteries_1_state")
    )
    dcdc2 = hass.states.get(
        registry.async_get_entity_id("sensor", DOMAIN, "abc123_worker1_dcdc_2_state")
    )
    assert battery.state == "operating"
    assert dcdc2.state == "standby"
    assert dcdc2.attributes["device_text"] == "Standby"
    assert dcdc2.attributes["serial"] == "12"
    # the master keeps its own (empty here) overview and nothing is mixed up
    assert registry.async_get_entity_id("sensor", DOMAIN, "abc123_dcdc_1_state") is None

    device = next(
        d
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if (DOMAIN, "abc123_worker1") in d.identifiers
    )
    assert device.model == "12SMT Island (2)"
    assert device.sw_version == "3.4.5"
    assert device.hw_version == "MCv2.5 Serie"
    assert device.serial_number == "98765"
    assert device.configuration_url == "http://maxstorage-w1.local"


async def test_worker_that_is_offline_does_not_break_the_setup(hass: HomeAssistant):
    entry = _entry(workers={"worker1": STORED})
    worker = _worker_link_client()
    worker.get_device_overview.side_effect = OSError("offline")

    await _setup(hass, entry, worker=worker)

    assert entry.state is ConfigEntryState.LOADED
    assert _reauth_flows(hass) == []
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, "abc123_worker1_dcdc_1_state") is None
    assert registry.async_get_entity_id("sensor", DOMAIN, "abc123_battery_soc") is not None


async def test_worker_with_changed_password_asks_for_it(hass: HomeAssistant):
    entry = _entry(workers={"worker1": STORED})
    worker = _worker_link_client()
    worker.get_device_overview.side_effect = AuthenticationFailedError("refused")

    await _setup(hass, entry, worker=worker)

    assert entry.state is ConfigEntryState.LOADED  # the master keeps working
    flows = _reauth_flows(hass)
    assert len(flows) == 1
    assert flows[0]["context"]["worker"] == "worker1"


async def test_reauth_of_a_worker(hass: HomeAssistant):
    entry = _entry(workers={"worker1": STORED})
    await _setup(hass, entry)

    result = await entry.start_reauth_flow(hass, context={"worker": "worker1"})
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["host"] == "maxstorage-w1.local"

    with (
        patch(
            "custom_components.maxstorage_ultimate.config_flow.MaxStorageClient",
            return_value=_login_client(),
        ) as cls,
        no_reload(),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"maxstorage_user": "other", "maxstorage_password": "newer"},
        )

    assert result["reason"] == "reauth_successful"
    cls.assert_called_once_with("maxstorage-w1.local", "other", "newer")
    assert entry.data["workers"]["worker1"] == {
        **STORED,
        "user": "other",
        "password": "newer",
    }
    assert entry.data["maxstorage_password"] == "solarmax.com"  # master unchanged


async def test_unloading_closes_the_worker_connection(hass: HomeAssistant):
    entry = _entry(workers={"worker1": STORED})
    worker = _worker_link_client()
    await _setup(hass, entry, worker=worker)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    worker.close.assert_awaited()


# ---- discovery: credentials of workers --------------------------------------------


async def _discover_worker(hass, probe):
    """Run a zeroconf flow for the worker; probe is the client used for the login."""
    with (
        patch(
            "custom_components.maxstorage_ultimate.config_flow.MaxStorageClient"
        ) as cls,
        patch(
            "custom_components.maxstorage_ultimate.swarm.get_mac_address",
            return_value=WORKER_MAC,
        ),
        patch(
            "custom_components.maxstorage_ultimate.config_flow.socket.gethostbyname",
            side_effect=socket.gaierror,
        ),
        no_reload(),
    ):
        cls.side_effect = probe
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_info()
        )
    return result, cls


def _probe_for(users):
    """A worker that only accepts the given (user, password) pairs."""

    def make(host, user, password):
        client = _worker_link_client()
        client.get_data = AsyncMock(
            side_effect=None
            if (user, password) in users
            else AuthenticationFailedError("refused"),
            return_value=copy.deepcopy(WORKER),
        )
        client.get_device_info = MagicMock(return_value=WORKER_INFO)
        return client

    return make


async def test_discovered_worker_gets_the_default_credentials_stored(hass: HomeAssistant):
    entry = _entry()
    await _setup(hass, entry)

    result, _ = await _discover_worker(hass, _probe_for({("user", "solarmax.com")}))

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "swarm_worker"
    assert entry.data["workers"] == {
        "worker1": {
            "host": "maxstorage-w1.local",
            "user": "user",
            "password": "solarmax.com",
            "ident": "maxstorage-w1",
        }
    }


async def test_discovery_uses_the_stored_credentials_of_the_worker(hass: HomeAssistant):
    entry = _entry(workers={"worker1": {**STORED, "ident": "maxstorage-w1"}})
    await _setup(hass, entry)
    # firmware and model of the device are not known yet, so the worker is queried again

    _, cls = await _discover_worker(hass, _probe_for({("worker-user", "worker-pw")}))

    cls.assert_called_once_with("maxstorage-w1.local", "worker-user", "worker-pw")


async def test_worker_with_other_credentials_is_offered_and_saved(hass: HomeAssistant):
    entry = _entry()
    await _setup(hass, entry)
    probe = _probe_for({("me", "secret")})

    # the default credentials do not work: the device is offered to the user
    result, _ = await _discover_worker(hass, probe)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"

    # wrong credentials: stays open
    with patch(
        "custom_components.maxstorage_ultimate.config_flow.MaxStorageClient",
        side_effect=probe,
    ):
        again = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"maxstorage_user": "me", "maxstorage_password": "wrong"},
        )
    assert again["type"] is FlowResultType.FORM
    assert again["errors"] == {"base": "invalid_auth"}
    assert "workers" not in entry.data

    # the right ones are kept for the master the worker belongs to
    with (
        patch(
            "custom_components.maxstorage_ultimate.config_flow.MaxStorageClient",
            side_effect=probe,
        ),
        patch("custom_components.maxstorage_ultimate.swarm.get_mac_address", return_value=WORKER_MAC),
        no_reload(),
    ):
        done = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"maxstorage_user": "me", "maxstorage_password": "secret"},
        )

    assert done["type"] is FlowResultType.ABORT
    assert done["reason"] == "worker_saved"
    assert entry.data["workers"]["worker1"] == {
        "host": "maxstorage-w1.local",
        "user": "me",
        "password": "secret",
        "ident": "maxstorage-w1",
    }


async def test_worker_without_a_configured_master_is_not_saved(hass: HomeAssistant):
    probe = _probe_for({("me", "secret")})
    result, _ = await _discover_worker(hass, probe)
    assert result["step_id"] == "confirm"

    with patch(
        "custom_components.maxstorage_ultimate.config_flow.MaxStorageClient",
        side_effect=probe,
    ):
        done = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"maxstorage_user": "me", "maxstorage_password": "secret"},
        )

    assert done["type"] is FlowResultType.ABORT
    assert done["reason"] == "swarm_worker"  # add the master first


async def test_worker_entered_by_hand_is_saved_for_its_master(hass: HomeAssistant):
    entry = _entry()
    await _setup(hass, entry)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with (
        patch(
            "custom_components.maxstorage_ultimate.config_flow.MaxStorageClient",
            side_effect=_probe_for({("me", "secret")}),
        ),
        patch("custom_components.maxstorage_ultimate.swarm.get_mac_address", return_value=WORKER_MAC),
        patch("socket.gethostbyname", side_effect=lambda host: host),
        no_reload(),
    ):
        done = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "name": "Worker",
                "maxstorage_host": "http://maxstorage-w1.local/",
                "maxstorage_user": "me",
                "maxstorage_password": "secret",
            },
        )

    assert done["type"] is FlowResultType.ABORT
    assert done["reason"] == "worker_saved"
    assert entry.data["workers"]["worker1"]["host"] == "maxstorage-w1.local"
    assert entry.data["workers"]["worker1"]["user"] == "me"


async def test_diagnostics_show_what_a_worker_delivered(hass: HomeAssistant):
    import json

    from custom_components.maxstorage_ultimate.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = _entry(workers={"worker1": STORED})
    await _setup(hass, entry)

    result = await async_get_config_entry_diagnostics(hass, entry)

    worker = result["workers"]["worker1"]
    assert worker["last_update_success"] is True
    assert worker["overview"]["model"] == "12SMT Island (2)"
    assert worker["overview"]["dcdc"][0]["serial"] == "**REDACTED**"
    assert worker["device_info"]["Firmware-Version"] == "3.4.5"
    assert worker["device_info"]["MasterController-Nummer"] == "**REDACTED**"
    dumped = json.dumps(result)
    for secret in ("worker-pw", "worker-user", "maxstorage-w1", "98765"):
        assert secret not in dumped, secret


async def test_diagnostics_show_a_worker_that_does_not_answer(hass: HomeAssistant):
    from custom_components.maxstorage_ultimate.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = _entry(workers={"worker1": STORED})
    worker = _worker_link_client()
    worker.get_device_overview.side_effect = OSError("offline")
    await _setup(hass, entry, worker=worker)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["workers"]["worker1"]["last_update_success"] is False
    assert result["workers"]["worker1"]["overview"] == {}


# ---- one request for the credentials of a worker, not two -------------------------

CONNECTED = {**STORED, "ident": "maxstorage-w1"}


def _open_flows(hass):
    return {
        flow["flow_id"]: flow["context"].get("source")
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    }


async def test_connected_worker_with_a_wrong_login_gets_only_the_reconfigure_request(
    hass: HomeAssistant,
):
    entry = _entry(workers={"worker1": CONNECTED})
    worker = _worker_link_client()
    worker.get_device_overview.side_effect = AuthenticationFailedError("refused")
    await _setup(hass, entry, worker=worker)
    assert len(_reauth_flows(hass)) == 1

    # the worker announces itself again; its stored login does not work
    result, _ = await _discover_worker(hass, _probe_for(set()))

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "worker_login_required"
    assert list(_open_flows(hass).values()) == [config_entries.SOURCE_REAUTH]


async def test_reconfigure_closes_the_card_of_the_same_worker(hass: HomeAssistant):
    entry = _entry()
    await _setup(hass, entry)
    card, _ = await _discover_worker(hass, _probe_for({("me", "secret")}))
    assert card["step_id"] == "confirm"  # an older version stored the connection later:
    with no_reload():
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "workers": {"worker1": CONNECTED}}
        )
    reauth = await entry.start_reauth_flow(hass, context={"worker": "worker1"})
    assert {card["flow_id"], reauth["flow_id"]} <= set(_open_flows(hass))

    with (
        patch(
            "custom_components.maxstorage_ultimate.config_flow.MaxStorageClient",
            return_value=_login_client(),
        ),
        no_reload(),
    ):
        await hass.config_entries.flow.async_configure(
            reauth["flow_id"],
            {"maxstorage_user": "me", "maxstorage_password": "secret"},
        )

    assert card["flow_id"] not in _open_flows(hass)
    assert reauth["flow_id"] not in _open_flows(hass)


async def test_card_closes_the_reconfigure_request_of_the_same_worker(hass: HomeAssistant):
    entry = _entry()
    await _setup(hass, entry)
    probe = _probe_for({("me", "secret")})
    card, _ = await _discover_worker(hass, probe)
    with no_reload():
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "workers": {"worker1": CONNECTED}}
        )
    reauth = await entry.start_reauth_flow(hass, context={"worker": "worker1"})

    with (
        patch(
            "custom_components.maxstorage_ultimate.config_flow.MaxStorageClient",
            side_effect=probe,
        ),
        patch(
            "custom_components.maxstorage_ultimate.swarm.get_mac_address",
            return_value=WORKER_MAC,
        ),
        no_reload(),
    ):
        done = await hass.config_entries.flow.async_configure(
            card["flow_id"],
            {"maxstorage_user": "me", "maxstorage_password": "secret"},
        )

    assert done["reason"] == "worker_saved"
    assert reauth["flow_id"] not in _open_flows(hass)
    assert entry.data["workers"]["worker1"]["user"] == "me"


async def test_other_workers_are_not_touched(hass: HomeAssistant):
    """Only the requests for the worker whose credentials were entered are closed."""
    two = {"worker1": CONNECTED, "worker2": {**STORED, "ident": "maxstorage-w2"}}
    entry = _entry(workers=two)
    await _setup(hass, entry)
    other = await entry.start_reauth_flow(hass, context={"worker": "worker2"})
    mine = await entry.start_reauth_flow(hass, context={"worker": "worker1"})

    with (
        patch(
            "custom_components.maxstorage_ultimate.config_flow.MaxStorageClient",
            return_value=_login_client(),
        ),
        no_reload(),
    ):
        await hass.config_entries.flow.async_configure(
            mine["flow_id"],
            {"maxstorage_user": "me", "maxstorage_password": "secret"},
        )

    assert other["flow_id"] in _open_flows(hass)
