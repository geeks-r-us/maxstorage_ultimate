"""Tests for the device overview: model name, DC/DC converters and batteries."""
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest

from custom_components.maxstorage_ultimate.client import (
    AuthenticationFailedError,
    HTTPError,
    MaxStorageClient,
    parse_device_overview,
)
from custom_components.maxstorage_ultimate.const import DOMAIN

from .test_client import HTML_340
from .test_init import _entry, _make_client, _setup

HTML = (Path(__file__).parent / "fixtures" / "device_overview.html").read_text(
    encoding="utf-8"
)
OVERVIEW = parse_device_overview(HTML)


def test_parse_model_and_components():
    result = parse_device_overview(HTML)

    assert result["model"] == "15SMT Island (1)"
    assert [c["index"] for c in result["dcdc"]] == [1, 2, 3, 4, 5]
    assert [c["index"] for c in result["batteries"]] == [1, 2, 3, 4, 5]
    assert result["dcdc"][0] == {"index": 1, "serial": "10000001", "state": "In Betrieb"}
    assert result["batteries"][4] == {"index": 5, "serial": "2005", "state": "In Betrieb"}


def test_parse_ignores_other_widgets():
    """Meter, heater and wallbox are not components."""
    result = parse_device_overview(HTML)
    assert len(result["dcdc"]) + len(result["batteries"]) == 10


def test_parse_other_states():
    html = HTML.replace(
        "<h4 class='text-center'>In Betrieb</h4>", "<h4 class='text-center'>Fehler</h4>", 1
    )
    assert parse_device_overview(html)["dcdc"][0]["state"] == "Fehler"


@pytest.mark.parametrize("html", ["", "<html></html>", "not html at all"])
def test_parse_without_overview(html):
    assert parse_device_overview(html) == {"model": None, "dcdc": [], "batteries": []}


def test_parse_component_without_state():
    html = (
        '<div class="widget-box"><div class="widget-title"><h5>Batterie #1  | 77</h5>'
        "</div></div>"
    )
    assert parse_device_overview(html)["batteries"] == [
        {"index": 1, "serial": "77", "state": None}
    ]


# ---- the HTTP request -------------------------------------------------------


@pytest.fixture
async def overview_server(socket_enabled):
    seen = {}

    async def login(request):
        return web.Response(text=HTML_340)

    async def overview(request):
        seen["form"] = dict(await request.post())
        seen["headers"] = dict(request.headers)
        if seen.get("fail"):
            return web.Response(status=500, text="boom")
        return web.Response(text=HTML)

    app = web.Application()
    app.router.add_post("/home.php", login)
    app.router.add_post("/shared/getDeviceData.php", overview)
    server = TestServer(app)
    await server.start_server()
    seen["host"] = f"127.0.0.1:{server.port}"
    yield seen
    await server.close()


async def test_client_reads_the_overview(overview_server):
    client = MaxStorageClient(overview_server["host"], "user", "pw")
    try:
        result = await client.get_device_overview()
    finally:
        await client.close()

    assert result["model"] == "15SMT Island (1)"
    assert overview_server["form"] == {"showActiveDeviceLiveData": "true"}
    assert overview_server["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert overview_server["headers"]["Referer"].endswith("/device_overview.php")


async def test_client_overview_http_error(overview_server):
    overview_server["fail"] = True
    client = MaxStorageClient(overview_server["host"], "user", "pw")
    try:
        with pytest.raises(HTTPError, match="boom"):
            await client.get_device_overview()
    finally:
        await client.close()


# ---- the integration --------------------------------------------------------


def _client_with(overview):
    client = _make_client()
    client.get_device_overview = AsyncMock(return_value=overview)
    return client


@pytest.fixture
def client_cls():
    with patch("custom_components.maxstorage_ultimate.MaxStorageClient") as cls:
        cls.side_effect = lambda *a, **k: _client_with(OVERVIEW)
        yield cls


def _entity_id(hass, key):
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"abc123_{key}")
    assert entity_id, key
    return entity_id


async def test_component_sensors(hass: HomeAssistant, client_cls):
    await _setup(hass, _entry())

    for kind in ("dcdc", "batteries"):
        for index in range(1, 6):
            state = hass.states.get(_entity_id(hass, f"{kind}_{index}_state"))
            assert state.state == "operating"
            assert state.attributes["device_text"] == "In Betrieb"
    dcdc = hass.states.get(_entity_id(hass, "dcdc_2_state"))
    battery = hass.states.get(_entity_id(hass, "batteries_3_state"))
    assert dcdc.attributes["serial"] == "10000002"
    assert battery.attributes["serial"] == "2003"
    assert dcdc.name.endswith("DC/DC converter 2 state")
    assert battery.name.endswith("Battery 3 state")

    entry = er.async_get(hass).async_get(dcdc.entity_id)
    assert entry.entity_category.value == "diagnostic"


async def test_model_name_comes_from_the_overview(hass: HomeAssistant, client_cls):
    entry = _entry()
    await _setup(hass, entry)

    master = next(
        d
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if (DOMAIN, "abc123") in d.identifiers
    )
    assert master.model == "15SMT Island (1)"


async def test_state_change_is_picked_up(hass: HomeAssistant, client_cls):
    entry = _entry()
    await _setup(hass, entry)
    runtime = hass.data[DOMAIN][entry.entry_id]

    changed = {
        **OVERVIEW,
        "batteries": [
            {**b, "state": "Fehler"} if b["index"] == 2 else b
            for b in OVERVIEW["batteries"]
        ],
    }
    runtime["client"].get_device_overview.return_value = changed
    await runtime["overview"].async_refresh()
    await hass.async_block_till_done()

    fault = hass.states.get(_entity_id(hass, "batteries_2_state"))
    assert fault.state == "fault"
    assert fault.attributes["device_text"] == "Fehler"
    assert hass.states.get(_entity_id(hass, "batteries_1_state")).state == "operating"


async def test_new_component_is_added_later(hass: HomeAssistant):
    few = {**OVERVIEW, "dcdc": OVERVIEW["dcdc"][:2]}
    with patch("custom_components.maxstorage_ultimate.MaxStorageClient") as cls:
        cls.side_effect = lambda *a, **k: _client_with(few)
        entry = _entry()
        await _setup(hass, entry)
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, "abc123_dcdc_3_state") is None

    runtime = hass.data[DOMAIN][entry.entry_id]
    runtime["client"].get_device_overview.return_value = OVERVIEW
    await runtime["overview"].async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(_entity_id(hass, "dcdc_3_state")).state == "operating"


async def test_setup_works_without_an_overview(hass: HomeAssistant):
    """Firmware without the page: no component sensors, old model name."""
    client = _make_client()
    client.get_device_overview = AsyncMock(side_effect=HTTPError("404"))
    with patch("custom_components.maxstorage_ultimate.MaxStorageClient", return_value=client):
        entry = _entry()
        await _setup(hass, entry)

    assert entry.state.value == "loaded"
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, "abc123_dcdc_1_state") is None
    master = next(
        d
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if (DOMAIN, "abc123") in d.identifiers
    )
    assert master.model == "15SMT Island(1)"
    assert float(hass.states.get(_entity_id(hass, "battery_soc")).state) == 55


async def test_overview_refresh_failure_keeps_the_last_states(hass: HomeAssistant, client_cls):
    entry = _entry()
    await _setup(hass, entry)
    runtime = hass.data[DOMAIN][entry.entry_id]

    runtime["client"].get_device_overview.side_effect = HTTPError("boom")
    await runtime["overview"].async_refresh()
    await hass.async_block_till_done()

    # the live data keeps working, the component sensors become unavailable
    assert float(hass.states.get(_entity_id(hass, "battery_soc")).state) == 55
    assert hass.states.get(_entity_id(hass, "dcdc_1_state")).state == "unavailable"


# ---- the state text depends on the language set on the device -------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("In Betrieb", "operating"),
        ("in betrieb", "operating"),
        ("  In  Betrieb ", "operating"),
        ("In operation", "operating"),
        ("Operating", "operating"),
        ("Standby", "standby"),
        ("Stand-by", "standby"),
        ("STANDBY", "standby"),
        ("Fehler", "fault"),
        ("Störung", "fault"),
        ("Fault", "fault"),
        ("Aus", "off"),
        ("Off", "off"),
        ("Etwas ganz anderes", "other"),
        ("", None),
        (None, None),
    ],
)
def test_component_state_is_language_independent(text, expected):
    from custom_components.maxstorage_ultimate.sensor import component_state

    assert component_state(text) == expected


async def test_state_is_an_enum_with_translatable_values(hass: HomeAssistant, client_cls):
    await _setup(hass, _entry())

    state = hass.states.get(_entity_id(hass, "dcdc_1_state"))

    assert state.attributes["device_class"] == "enum"
    assert state.attributes["options"] == ["operating", "standby", "fault", "off", "other"]


async def test_unknown_text_is_shown_as_other_and_reported_once(
    hass: HomeAssistant, client_cls, caplog
):
    entry = _entry()
    await _setup(hass, entry)
    runtime = hass.data[DOMAIN][entry.entry_id]
    odd = {
        **OVERVIEW,
        "dcdc": [{**c, "state": "Kalibrierung"} if c["index"] == 1 else c for c in OVERVIEW["dcdc"]],
    }

    runtime["client"].get_device_overview.return_value = odd
    for _ in range(3):
        await runtime["overview"].async_refresh()
        await hass.async_block_till_done()

    state = hass.states.get(_entity_id(hass, "dcdc_1_state"))
    assert state.state == "other"
    assert state.attributes["device_text"] == "Kalibrierung"
    assert caplog.text.count("Unknown state text 'Kalibrierung'") == 1
    assert hass.states.get(_entity_id(hass, "dcdc_2_state")).state == "operating"
