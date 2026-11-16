"""Module provides a MaxStorageClient class for interacting with the MaxStorage web service."""
import logging
import asyncio
import os
import re
import socket
import subprocess
import time

import aiohttp
from bs4 import BeautifulSoup

_LOGGER = logging.getLogger(__name__)


def normalize_host(host: str) -> str:
    """Strip whitespace, URL scheme, path and trailing slashes from a host input.

    "http://192.168.1.5/" -> "192.168.1.5"
    """
    host = host.strip()
    if "://" in host:
        host = host.split("://", 1)[1]
    return host.split("/", 1)[0].strip()


def get_mac_address(ip_address):
    """Get the MAC address of the host."""

    if os.name == "nt":  # Windows
        ping_cmd = ["ping", "-n", "1", ip_address]
        cmd = ["arp", "-a"]

    else:  # Unix-based
        ping_cmd = ["ping", "-c", "1", ip_address]
        cmd = ["arp", "-a", ip_address]

    try:
        output = subprocess.check_output(
            ping_cmd, text=True, timeout=10
        )  # Ping the host to ensure it's in the ARP table
        _LOGGER.debug(output)
        output = subprocess.check_output(cmd, text=True, timeout=10)
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
    except subprocess.TimeoutExpired as ex:
        _LOGGER.error("Timeout getting MAC address: %s", ex)
        return None
    except FileNotFoundError as ex:
        _LOGGER.error("Error getting MAC address: %s", ex)
        return None


_COMPONENT_TITLE = re.compile(r"^(DC/DC|Batterie)\s*#\s*(\d+)\s*\|\s*(.+)$")


def parse_device_overview(html: str) -> dict:
    """Parse the response of getDeviceData.php (the device overview page).

    Returns the model name of the device and the state of every DC/DC converter
    and battery: {"model": str | None, "dcdc": [...], "batteries": [...]}, each
    component being {"index": int, "serial": str, "state": str | None}.
    """
    soup = BeautifulSoup(html, "html.parser")
    result: dict = {"model": None, "dcdc": [], "batteries": []}

    # The widget with the picture of the storage carries the model name.
    picture = soup.find("img", src=re.compile("maxstorage", re.IGNORECASE))
    box = picture.find_parent(class_="widget-box") if picture else None
    title = box.select_one(".widget-title h5") if box else None
    if title is None:
        title = soup.select_one(".widget-title h5")
    if title is not None and not _COMPONENT_TITLE.match(title.get_text(" ", strip=True)):
        result["model"] = title.get_text(" ", strip=True) or None

    for heading in soup.select(".widget-title h5"):
        match = _COMPONENT_TITLE.match(heading.get_text(" ", strip=True))
        if not match:
            continue
        kind, index, serial = match.groups()
        widget = heading.find_parent(class_="widget-box")
        state = widget.find("h4", class_="text-center") if widget else None
        component = {
            "index": int(index),
            "serial": serial.strip(),
            "state": state.get_text(strip=True) if state else None,
        }
        result["dcdc" if kind == "DC/DC" else "batteries"].append(component)
    return result


def is_swarm_worker(data: object) -> bool:
    """Return True if the device response comes from a swarm worker.

    The master reports ``Swarming.swarmJob == "master"`` and contains the data
    of all workers; a worker only reports ``"worker"``.
    """
    swarming = data.get("Swarming") if isinstance(data, dict) else None
    return isinstance(swarming, dict) and swarming.get("swarmJob") == "worker"


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
        base_url = normalize_host(base_url)
        self.base_url = base_url
        self.login_url = f"http://{base_url}/home.php"
        self.data_url = f"http://{base_url}/shared/energycontrolfunctions.php"
        self.overview_url = f"http://{base_url}/shared/getDeviceData.php"
        self.overview_referer = f"http://{base_url}/device_overview.php"
        self.username = username
        self.password = password
        self.device_info = {}
        self.mac = None
        self.last_auth_time = None
        self.TOKEN_EXPIRY = 600  # 10 minutes

    async def setup(self):
        """Set up the client."""
        loop = asyncio.get_running_loop()
        ip = await loop.run_in_executor(None, self.get_ip_address, self.base_url)
        if ip is None:
            raise InvalidHostError(f"Invalid host: {self.base_url}")
        await self.authenticate()

        self.mac = await loop.run_in_executor(None, self.get_mac_address, ip)

    def get_ip_address(self, host) -> str | None:
        """Get the IP address of the host."""
        try:
            return socket.gethostbyname(host)
        except socket.gaierror:
            return None

    def get_mac_address(self, ip_address):
        """Get the MAC address of the host."""
        return get_mac_address(ip_address)

    async def ensure_authenticated(self):
        """Ensure that the session is authenticated."""
        if not self.is_token_valid():
            await self.authenticate()

    async def authenticate(self):
        """Authenticate with the server. The session will handle the cookie."""
        data = {"username": self.username, "password": self.password}
        try:
            async with self.session.post(self.login_url, data=data) as response:
                if response.status == 200:
                    await self._read_device_info(response)
                    self.last_auth_time = time.time()
                else:
                    raise AuthenticationFailedError(
                        f"Authentication Failed with status code {response.status}: "
                        f"{await response.text()}"
                    )
        except aiohttp.ClientConnectorError as e:
            raise InvalidHostError(f"Invalid host: {self.login_url}") from e

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
        """Fetch the live data using the 'getFullSwarmLiveDataJSON' parameter.

        If the device rejects the session (401/403), authenticate again once.
        """
        await self.ensure_authenticated()
        try:
            for attempt in (1, 2):
                async with self.session.post(
                    self.data_url, data={"getFullSwarmLiveDataJSON": 1}
                ) as response:
                    if response.status in (401, 403) and attempt == 1:
                        self.last_auth_time = None
                        await self.authenticate()
                        continue
                    if response.status != 200:
                        raise HTTPError(
                            f"Failed to fetch data with status code {response.status}: "
                            f"{await response.text()}"
                        )
                    try:
                        return await response.json(content_type=None)
                    except ValueError as e:
                        raise ValueError(
                            f"Response not in JSON format: {await response.text()}"
                        ) from e
        except aiohttp.ClientConnectorError as e:
            raise InvalidHostError(f"Invalid host: {self.data_url}") from e

    async def get_device_overview(self) -> dict:
        """Read the model name and the DC/DC converter and battery states."""
        await self.ensure_authenticated()
        try:
            async with self.session.post(
                self.overview_url,
                data={"showActiveDeviceLiveData": "true"},
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": self.overview_referer,
                },
            ) as response:
                if response.status != 200:
                    raise HTTPError(
                        f"Failed to fetch the device overview with status code "
                        f"{response.status}: {await response.text()}"
                    )
                return parse_device_overview(await response.text())
        except aiohttp.ClientConnectorError as e:
            raise InvalidHostError(f"Invalid host: {self.overview_url}") from e

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
