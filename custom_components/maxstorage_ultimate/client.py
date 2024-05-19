"""Module provides a MaxStorageClient class for interacting with the MaxStorage web service."""
import logging
import os
import re
import socket
import subprocess
import time

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
        self.base_url = base_url
        self.login_url = f"http://{base_url}/home.php"
        self.data_url = f"http://{base_url}/shared/energycontrolfunctions.php"
        self.username = username
        self.password = password
        self.device_info = {}
        self.mac = None
        self.last_auth_time = None
        self.TOKEN_EXPIRY = 600  # 10 minutes

    async def setup(self):
        """Set up the client."""
        ip = self.get_ip_address(self.base_url)
        if ip is None:
            raise InvalidHostError(f"Invalid host: {self.base_url}")
        await self.authenticate()

        self.mac = self.get_mac_address(ip)

    def get_ip_address(self, host) -> str | None:
        """Get the IP address of the host."""
        try:
            return socket.gethostbyname(host)
        except socket.gaierror:
            return None

    def get_mac_address(self, ip_address):
        """Get the MAC address of the host."""

        ping_cmd = ["ping", "-c", "1", ip_address]

        if os.name == "nt":  # Windows
            cmd = ["arp", "-a"]

        else:  # Unix-based
            cmd = ["arp", "-a", ip_address]

        try:
            output = subprocess.check_output(
                ping_cmd, text=True
            )  # Ping the host to ensure it's in the ARP table
            _LOGGER.debug(output)
            output = subprocess.check_output(cmd, text=True)
            _LOGGER.debug(output)
            # Use regex to find the MAC address
            result = re.search(r"([0-9a-fA-F]{2}[:-]){5}([0-9a-fA-F]{2})", output)
            if result:
                _LOGGER.debug("Found MAC: %s", result.group())
                return result.group()  # MAC address
            return None
        except subprocess.CalledProcessError as ex:
            _LOGGER.error("Error getting MAC address: %s", ex)
            return None

    async def ensure_authenticated(self):
        """Ensure that the session is authenticated."""
        if not self.is_token_valid():
            await self.authenticate()

    async def authenticate(self):
        """Authenticate with the server. The session will handle the cookie."""
        data = {"username": self.username, "password": self.password}
        async with self.session.post(self.login_url, data=data) as response:
            if response.status == 200:
                self.last_auth_time = time.time()
                await self._read_device_info(response)
            else:
                raise AuthenticationFailedError(
                    f"Authentication Failed with status code {response.status}: {response.text}"
                )

    async def _read_device_info(self, response: aiohttp.ClientResponse):
        """Read the device info from the response."""

        content = await response.text()
        soup = BeautifulSoup(content, "html.parser")

        # Find all potential keys (bold text could be considered a key)
        keys = soup.find_all(["b", "div"])
        current_key = None

        for element in keys:
            text = element.get_text().strip().replace(":", "")

            # If the current element is a key
            if text and (
                text
                in [
                    "Anlagenname",
                    "MasterController-Nummer",
                    "Firmware-Version",
                    "Hardware-Version",
                    "Ident",
                ]
            ):
                current_key = text
                # Directly following sibling logic for Version 3.4.0
                if element.next_sibling and not element.next_sibling.name:
                    self.device_info[current_key] = element.next_sibling.strip()
                # Following div logic for Version 3.4.3
                elif element.find_next_sibling():
                    sibling = element.find_next_sibling()
                    if sibling and not sibling.find(["b"]):
                        self.device_info[current_key] = sibling.get_text(strip=True)

        if not self.device_info:
            _LOGGER.error("Failed to parse device info from response: %s", content)
            raise DataParserError("Failed to parse device info")

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
        await self.ensure_authenticated()
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


class DataParserError(Exception):
    """Exception raised when data parsing fails."""


class AuthenticationFailedError(Exception):
    """Exception raised when authentication fails."""


class InvalidHostError(Exception):
    """Exception raised when the host is invalid."""


class HTTPError(Exception):
    """Exception raised when the HTTP status code is not 200."""
