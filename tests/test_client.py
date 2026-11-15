"""Tests for the MaxStorage client."""
import subprocess
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from custom_components.maxstorage_ultimate.client import (
    AuthenticationFailedError,
    DataParserError,
    HTTPError,
    InvalidHostError,
    MaxStorageClient,
    is_swarm_worker,
    normalize_host,
)

HTML_340 = """<html><body>
<b>Anlagenname:</b> Home
<b>MasterController-Nummer:</b> 12345
<b>Firmware-Version:</b> 3.4.0
<b>Hardware-Version:</b> 1.0
<b>Ident:</b> abc123
</body></html>"""

HTML_343 = """<html><body>
<div>Anlagenname:</div><div>Home</div>
<div>MasterController-Nummer:</div><div>12345</div>
<div>Firmware-Version:</div><div>3.4.3</div>
<div>Hardware-Version:</div><div>1.0</div>
<div>Ident:</div><div>abc123</div>
</body></html>"""


class FakeDevice:
    """Scripted fake Max.Storage web server."""

    def __init__(self):
        self.login = []  # queue of (status, body)
        self.data = []  # queue of (status, body)
        self.login_calls = 0
        self.data_calls = 0

    async def handle_login(self, request):
        self.login_calls += 1
        status, body = self.login.pop(0)
        return web.Response(status=status, text=body)

    async def handle_data(self, request):
        self.data_calls += 1
        status, body = self.data.pop(0)
        return web.Response(status=status, text=body)


@pytest.fixture
async def device(socket_enabled):
    """Run a fake device on a local port."""
    dev = FakeDevice()
    app = web.Application()
    app.router.add_post("/home.php", dev.handle_login)
    app.router.add_post("/shared/energycontrolfunctions.php", dev.handle_data)
    server = TestServer(app)
    await server.start_server()
    dev.host = f"127.0.0.1:{server.port}"
    yield dev
    await server.close()


@pytest.fixture
async def client(device):
    """Create a client talking to the fake device."""
    c = MaxStorageClient(device.host, "user", "pw")
    yield c
    await c.close()


@pytest.mark.parametrize(("html", "fw"), [(HTML_340, "3.4.0"), (HTML_343, "3.4.3")])
async def test_authenticate_parses_device_info(client, device, html, fw):
    device.login = [(200, html)]
    await client.authenticate()
    info = client.get_device_info()
    assert info["Ident"] == "abc123"
    assert info["Firmware-Version"] == fw
    assert info["Anlagenname"] == "Home"
    assert client.is_token_valid()


async def test_authenticate_http_error(client, device):
    device.login = [(401, "denied")]
    with pytest.raises(AuthenticationFailedError, match="denied"):
        await client.authenticate()
    assert not client.is_token_valid()


async def test_authenticate_unparsable_page(client, device):
    device.login = [(200, "<html>nothing</html>")]
    with pytest.raises(DataParserError):
        await client.authenticate()
    assert not client.is_token_valid()


async def test_get_data(client, device):
    device.login = [(200, HTML_340)]
    device.data = [(200, '{"batterySoC": 55}')]
    assert await client.get_data() == {"batterySoC": 55}


async def test_get_data_reauthenticates_on_401(client, device):
    device.login = [(200, HTML_340), (200, HTML_340)]
    device.data = [(401, ""), (200, '{"batterySoC": 1}')]
    assert await client.get_data() == {"batterySoC": 1}
    assert device.login_calls == 2


async def test_get_data_http_error(client, device):
    device.login = [(200, HTML_340)]
    device.data = [(500, "boom")]
    with pytest.raises(HTTPError, match="boom"):
        await client.get_data()


async def test_get_data_invalid_json(client, device):
    device.login = [(200, HTML_340)]
    device.data = [(200, "not json")]
    with pytest.raises(ValueError, match="not json"):
        await client.get_data()


async def test_setup_invalid_host(client):
    with patch.object(client, "get_ip_address", return_value=None):
        with pytest.raises(InvalidHostError):
            await client.setup()


async def test_setup_sets_mac(client, device):
    device.login = [(200, HTML_340)]
    with (
        patch.object(client, "get_ip_address", return_value="10.0.0.2"),
        patch.object(client, "get_mac_address", return_value="aa:bb:cc:dd:ee:ff"),
    ):
        await client.setup()
    assert client.mac == "aa:bb:cc:dd:ee:ff"


def test_get_mac_address_parses_arp(client):
    outputs = iter(["ping ok", "? (10.0.0.2) at aa:bb:cc:dd:ee:ff [ether] on eth0"])
    with patch("subprocess.check_output", side_effect=lambda *a, **k: next(outputs)):
        assert client.get_mac_address("10.0.0.2") == "aa:bb:cc:dd:ee:ff"


@pytest.mark.parametrize(
    "exc",
    [FileNotFoundError("arp"), subprocess.CalledProcessError(1, "ping"),
     subprocess.TimeoutExpired("ping", 10)],
)
def test_get_mac_address_handles_failures(client, exc):
    with patch("subprocess.check_output", side_effect=exc):
        assert client.get_mac_address("10.0.0.2") is None


async def test_unreachable_host_raises_invalid_host(socket_enabled):
    c = MaxStorageClient("127.0.0.1:1", "user", "pw")
    try:
        with pytest.raises(InvalidHostError):
            await c.get_data()
    finally:
        await c.close()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("192.168.1.5", "192.168.1.5"),
        ("  192.168.1.5  ", "192.168.1.5"),
        ("http://192.168.123.3/", "192.168.123.3"),
        ("https://soma.local", "soma.local"),
        ("HTTP://soma.fritz.box/home.php", "soma.fritz.box"),
        ("soma.local/", "soma.local"),
        ("192.168.1.5:8080/x", "192.168.1.5:8080"),
    ],
)
def test_normalize_host(raw, expected):
    assert normalize_host(raw) == expected


async def test_client_uses_normalized_host():
    c = MaxStorageClient("http://192.168.123.3/", "user", "pw")
    try:
        assert c.base_url == "192.168.123.3"
        assert c.login_url == "http://192.168.123.3/home.php"
    finally:
        await c.close()


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"Swarming": {"swarmJob": "worker"}}, True),
        ({"Swarming": {"swarmJob": "master", "worker1": {}}}, False),
        ({"Swarming": {}}, False),
        ({"Swarming": "worker"}, False),
        ({}, False),
        (None, False),
        ([], False),
    ],
)
def test_is_swarm_worker(data, expected):
    assert is_swarm_worker(data) is expected
