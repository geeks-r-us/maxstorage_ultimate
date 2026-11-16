"""Tests for the login of the web interface (shared/logincheck.php)."""
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.core import HomeAssistant
import pytest

from custom_components.maxstorage_ultimate.client import (
    AuthenticationFailedError,
    LoginLockedError,
    MaxStorageClient,
)
from custom_components.maxstorage_ultimate.const import DOMAIN

from .test_client import HTML_340
from .test_overview import HTML


# ---- the login like the web interface ----------------------------------------


class WebInterface:
    """A fake device: the overview needs the session of shared/logincheck.php."""

    def __init__(self):
        self.answer = "1"  # answer of logincheck.php
        self.has_logincheck = True  # False: older firmware without the page
        self.overview_needs_login = True
        self.legacy_accepts = True  # False: home.php answers with the login page
        self.empty_next = 0  # answer the next overview requests with nothing
        self.logins = []
        self.host = ""

    async def logincheck(self, request):
        if not self.has_logincheck:
            return web.Response(status=404, text="not found")
        form = await request.post()
        self.logins.append((form.get("u"), form.get("p")))
        response = web.Response(text=self.answer)
        if self.answer == "1":
            response.set_cookie("PHPSESSID", "auth", path="/")
        return response

    async def home(self, request):
        if not self.legacy_accepts:
            return web.Response(text="<html><form id='loginform'></form></html>")
        response = web.Response(text=HTML_340)
        if "PHPSESSID" not in request.cookies:
            response.set_cookie("PHPSESSID", "guest", path="/")
        return response

    async def overview(self, request):
        if self.empty_next > 0:
            self.empty_next -= 1
            return web.Response(text="")
        if self.overview_needs_login and request.cookies.get("PHPSESSID") != "auth":
            return web.Response(text="")  # what the device does without a session
        return web.Response(text=HTML)


@pytest.fixture
async def web_interface(socket_enabled):
    device = WebInterface()
    app = web.Application()
    app.router.add_post("/shared/logincheck.php", device.logincheck)
    app.router.add_post("/home.php", device.home)
    app.router.add_post("/shared/getDeviceData.php", device.overview)
    server = TestServer(app)
    await server.start_server()
    device.host = f"127.0.0.1:{server.port}"
    yield device
    await server.close()


async def _overview(device):
    client = MaxStorageClient(device.host, "user", "secret")
    try:
        return await client.get_device_overview()
    finally:
        await client.close()


async def test_overview_needs_the_login_of_the_web_interface(web_interface):
    result = await _overview(web_interface)

    assert result["model"] == "15SMT Island (1)"
    assert len(result["dcdc"]) == 5
    assert web_interface.logins == [("user", "secret")]


async def test_older_firmware_without_logincheck(web_interface):
    web_interface.has_logincheck = False
    web_interface.overview_needs_login = False

    result = await _overview(web_interface)  # logs in through home.php only

    assert result["model"] == "15SMT Island (1)"
    assert web_interface.logins == []


async def test_the_device_info_is_still_read(web_interface):
    client = MaxStorageClient(web_interface.host, "user", "secret")
    try:
        await client.authenticate()
        assert client.get_device_info()["Ident"] == "abc123"
        assert client.is_token_valid()
    finally:
        await client.close()


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ("0", "Login refused"),
        ("2", "one-time code"),
        ("3", "one-time code"),
        ("-30", "locked for 30 seconds"),
    ],
)
async def test_refused_login(web_interface, answer, message):
    web_interface.answer = answer
    web_interface.legacy_accepts = False  # neither login works
    client = MaxStorageClient(web_interface.host, "user", "wrong")
    try:
        with pytest.raises(AuthenticationFailedError, match=message):
            await client.authenticate()
        assert not client.is_token_valid()
    finally:
        await client.close()


async def test_unknown_answer_of_logincheck_is_ignored(web_interface):
    """A page or text instead of a number is not a refusal (other firmware)."""
    web_interface.answer = "<html>some other page</html>"
    web_interface.overview_needs_login = False

    assert (await _overview(web_interface))["model"] == "15SMT Island (1)"


async def test_expired_session_is_renewed_once(web_interface):
    web_interface.empty_next = 1

    result = await _overview(web_interface)

    assert result["model"] == "15SMT Island (1)"
    assert len(web_interface.logins) == 2


async def test_page_that_stays_empty_is_not_an_error(web_interface):
    web_interface.empty_next = 5

    result = await _overview(web_interface)

    assert result == {"model": None, "dcdc": [], "batteries": []}
    assert len(web_interface.logins) == 2  # one retry only


async def test_wrong_password_is_reported_as_such_by_the_config_flow(
    hass: HomeAssistant, web_interface
):
    from homeassistant import config_entries
    from homeassistant.data_entry_flow import FlowResultType

    web_interface.answer = "0"
    web_interface.legacy_accepts = False
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(
        "custom_components.maxstorage_ultimate.config_flow.socket.gethostbyname",
        side_effect=lambda host: host.split(":")[0],
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "name": "MaxStorage",
                "maxstorage_host": web_interface.host,
                "maxstorage_user": "user",
                "maxstorage_password": "wrong",
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.parametrize("answer", ["2", "3"])
async def test_the_login_through_home_php_counts_if_a_new_password_is_wanted(
    web_interface, answer, caplog
):
    """Answers 2 and 3 mean: credentials right, but the device wants a new password
    or a one-time code. The login through home.php still works, as before."""
    web_interface.answer = answer
    client = MaxStorageClient(web_interface.host, "user", "default")
    try:
        await client.authenticate()
        await client.authenticate()  # the hint is logged once only
        assert client.get_device_info()["Ident"] == "abc123"
        assert client.is_token_valid()
    finally:
        await client.close()

    assert caplog.text.count("The login through home.php works") == 1
    assert "new password" in caplog.text


@pytest.mark.parametrize(
    ("answer", "error"),
    [("0", AuthenticationFailedError), ("5", AuthenticationFailedError), ("-30", LoginLockedError)],
)
async def test_wrong_credentials_count_although_home_php_answers(web_interface, answer, error):
    """Some firmware shows the start page through home.php for any credentials, so
    the answer of logincheck.php decides when it says the login is not valid."""
    web_interface.answer = answer
    assert web_interface.legacy_accepts  # home.php would let us in

    client = MaxStorageClient(web_interface.host, "user", "wrong")
    try:
        with pytest.raises(error):
            await client.authenticate()
        assert not client.is_token_valid()
    finally:
        await client.close()


async def test_overview_stays_empty_without_the_full_login(web_interface):
    web_interface.answer = "2"

    client = MaxStorageClient(web_interface.host, "user", "default")
    try:
        result = await client.get_device_overview()
    finally:
        await client.close()

    assert result == {"model": None, "dcdc": [], "batteries": []}
