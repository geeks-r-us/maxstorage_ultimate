"""Module provides a MaxStorageClient class for interacting with the MaxStorage web service."""

import gzip
import logging
import time
from urllib.parse import urlencode

import aiohttp
from bs4 import BeautifulSoup

_LOGGER = logging.getLogger(__name__)


class MaxStorageClient:
    """Client for interacting with the MaxStorage web service."""

    def __init__(self, base_url, username, password) -> None:
        """Initialize the MaxStorageClient object.

        Args:
            base_url (str): The base URL of the web service.
            username (str): The username for authentication.
            password (str): The password for authentication.
        """
        cookie_jar = aiohttp.CookieJar(unsafe=True)
        self.session = aiohttp.ClientSession(cookie_jar=cookie_jar)
        self.login_url = f"http://{base_url}/home.php"
        self.data_url = f"http://{base_url}/shared/energycontrolfunctions.php"
        self.device_overview_url = f"http://{base_url}/shared/getDeviceData.php"
        self.username = username
        self.password = password
        self.device_info = {}
        self.last_auth_time = None
        self.TOKEN_EXPIRY = 600  # 10 minutes

    async def authenticate(self):
        """Authenticate with the server. The session will handle the cookie."""
        data = {"username": self.username, "password": self.password}
        async with self.session.post(self.login_url, data=data) as response:
            if response.status == 200:
                self.last_auth_time = time.time()
                await self._read_device_info(response)
                await self._read_device_overview()
            else:
                raise AuthenticationFailedError(
                    f"Authentication Failed with status code {response.status}: {response.text}"
                )

    async def _read_device_info(self, response: aiohttp.ClientResponse):
        """Read the device info from the response."""

        content = await response.text()
        soup = BeautifulSoup(content, "html.parser")

        elements = soup.find_all("p", style="white-space: normal;padding: 5px")
        for element in elements:
            entries = element.find_all("b")
            for entry in entries:
                if entry.next_sibling is not None:
                    self.device_info[
                        entry.text.strip().replace(":", "")
                    ] = entry.next_sibling.strip()

    async def _read_device_overview(
        self,
    ):
        """Read the device overview from the response.""" ""

        await self.session.post(
            "http://192.168.178.43/device_overview.php"
        )  # This is needed to set the session cookie?

        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": "http://192.168.178.43/device_overview.php",
            "Origin": "http://192.168.178.43",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "Accept-Encoding": "gzip, deflate",
        }
        data = "showActiveDeviceLiveData=true"

        async with self.session.post(
            self.device_overview_url,
            data=data,
            headers=headers,
        ) as response:
            _LOGGER.debug(f"Response Status: {response.status}")
            _LOGGER.debug(f"Response Headers: {response.headers}")
            if response.status == 200:
                try:
                    if response.headers.get("Content-Encoding") == "gzip":
                        content = gzip.decompress(await response.read())
                    else:
                        content = await response.text()

                    soup = BeautifulSoup(content, "html.parser")

                except ValueError as e:
                    raise ValueError(
                        f"Response not in JSON format: {response.text}"
                    ) from e
            else:
                raise HTTPError(
                    f"Failed to fetch data with status code {response.status}: {response.text}"
                )

    def get_device_info(self):
        """Return the device info."""
        return self.device_info

    def is_token_valid(self):
        """Check if the token (session) is still valid."""
        return (
            self.last_auth_time is not None
            and time.time() < self.last_auth_time + self.TOKEN_EXPIRY
        )

    async def get_data(self):
        """Make a POST request to the data endpoint using the session with the 'getFullSwarmLiveDataJSON' parameter."""
        if not self.is_token_valid():
            await self.authenticate()
        try:
            async with self.session.post(
                self.data_url, data={"getFullSwarmLiveDataJSON": 1}
            ) as response:
                if response.status == 200:
                    try:
                        return await response.json(content_type=None)
                    except ValueError as e:
                        raise ValueError(
                            f"Response not in JSON format: {response.text}"
                        ) from e
                else:
                    raise HTTPError(
                        f"Failed to fetch data with status code {response.status}: {response.text}"
                    )
        except aiohttp.ClientConnectorError as e:
            raise InvalidHostError(f"Invalid host: {self.data_url}") from e

    async def close(self):
        """Close the aiohttp session."""
        await self.session.close()


class AuthenticationFailedError(Exception):
    """Exception raised when authentication fails."""


class InvalidHostError(Exception):
    """Exception raised when the host is invalid."""


class HTTPError(Exception):
    """Exception raised when the HTTP status code is not 200."""
