"""Module provides a MaxStorageClient class for interacting with the MaxStorage web service."""

import time

import aiohttp


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
        self.username = username
        self.password = password
        self.last_auth_time = None
        self.TOKEN_EXPIRY = 600  # 10 minutes

    async def authenticate(self):
        """Authenticate with the server. The session will handle the cookie."""
        data = {"username": self.username, "password": self.password}
        async with self.session.post(self.login_url, data=data) as response:
            if response.status == 200:
                self.last_auth_time = time.time()
            else:
                raise AuthenticationFailedError(
                    f"Authentication Failed with status code {response.status}: {response.text}"
                )

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
