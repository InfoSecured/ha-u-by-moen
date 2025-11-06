"""API client for U by Moen."""
import logging
import asyncio
from typing import Any, Dict, List, Optional
import aiohttp
import json
from threading import Thread

import pysher

from .const import (
    API_BASE_URL,
    API_AUTHENTICATE,
    API_CREDENTIALS,
    API_SHOWERS,
    API_SHOWER_DETAIL,
    API_PUSHER_AUTH,
    PUSHER_CHANNEL_PREFIX,
)

_LOGGER = logging.getLogger(__name__)


class MoenApiError(Exception):
    """Base exception for Moen API errors."""


class MoenAuthError(MoenApiError):
    """Exception for authentication errors."""


class MoenApi:
    """API client for U by Moen devices."""

    def __init__(self, email: str, password: str, session: aiohttp.ClientSession):
        """Initialize the API client."""
        self._email = email
        self._password = password
        self._session = session
        self._token: Optional[str] = None
        self._pusher_key: Optional[str] = None
        self._pusher_cluster: Optional[str] = None
        self._pusher = None
        self._pusher_thread = None
        self._channels: Dict[str, Any] = {}
        self._device_callbacks: Dict[str, callable] = {}

    async def authenticate(self) -> str:
        """Authenticate with the Moen API and return the token."""
        url = f"{API_BASE_URL}{API_AUTHENTICATE}"
        params = {"email": self._email, "password": self._password}

        try:
            async with self._session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                self._token = data.get("token")

                if not self._token:
                    raise MoenAuthError("No token received from authentication")

                _LOGGER.debug("Successfully authenticated with Moen API")
                return self._token

        except aiohttp.ClientError as err:
            raise MoenAuthError(f"Authentication failed: {err}") from err

    async def get_pusher_credentials(self) -> Dict[str, str]:
        """Get Pusher credentials for WebSocket connection."""
        if not self._token:
            await self.authenticate()

        url = f"{API_BASE_URL}{API_CREDENTIALS}"
        headers = {"User-Token": self._token}

        try:
            async with self._session.get(url, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
                self._pusher_key = data.get("app_key")
                self._pusher_cluster = data.get("cluster")

                _LOGGER.debug(
                    "Got Pusher credentials: key=%s, cluster=%s",
                    self._pusher_key,
                    self._pusher_cluster,
                )
                return data

        except aiohttp.ClientError as err:
            raise MoenApiError(f"Failed to get Pusher credentials: {err}") from err

    async def get_devices(self) -> List[Dict[str, Any]]:
        """Get list of all devices."""
        if not self._token:
            await self.authenticate()

        url = f"{API_BASE_URL}{API_SHOWERS}"
        headers = {"User-Token": self._token}

        try:
            async with self._session.get(url, headers=headers) as response:
                response.raise_for_status()
                devices = await response.json()
                _LOGGER.debug("Found %d devices", len(devices))
                return devices

        except aiohttp.ClientError as err:
            raise MoenApiError(f"Failed to get devices: {err}") from err

    async def get_device_details(self, serial_number: str) -> Dict[str, Any]:
        """Get detailed information for a specific device."""
        if not self._token:
            await self.authenticate()

        url = f"{API_BASE_URL}{API_SHOWER_DETAIL.format(serial_number)}"
        headers = {"User-Token": self._token}

        try:
            async with self._session.get(url, headers=headers) as response:
                response.raise_for_status()
                device_data = await response.json()
                _LOGGER.debug("Got device details for %s", serial_number)
                return device_data

        except aiohttp.ClientError as err:
            raise MoenApiError(f"Failed to get device details: {err}") from err

    async def get_pusher_auth(self, socket_id: str, channel_name: str) -> str:
        """Get Pusher authentication for private channel."""
        if not self._token:
            await self.authenticate()

        url = f"{API_BASE_URL}{API_PUSHER_AUTH}"
        headers = {
            "User-Token": self._token,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = f"socket_id={socket_id}&channel_name={channel_name}"

        try:
            async with self._session.post(url, headers=headers, data=data) as response:
                response.raise_for_status()
                auth_data = await response.json()
                return auth_data.get("auth")

        except aiohttp.ClientError as err:
            raise MoenApiError(f"Failed to get Pusher auth: {err}") from err

    def _pusher_auth_handler(self, socket_id: str, channel_name: str) -> Dict[str, str]:
        """Handle Pusher authentication requests (sync wrapper)."""
        # This needs to be synchronous for pysher, so we'll need to handle this differently
        # For now, we'll return a placeholder and handle auth via the async method
        _LOGGER.debug("Pusher auth requested for channel %s", channel_name)
        return {}

    def start_pusher(self, callback: callable = None):
        """Start Pusher connection in a separate thread."""
        if not self._pusher_key or not self._pusher_cluster:
            _LOGGER.error("Pusher credentials not available")
            return

        def pusher_thread():
            self._pusher = pysher.Pusher(
                key=self._pusher_key,
                cluster=self._pusher_cluster,
                auth_endpoint_callback=self._pusher_auth_handler,
            )

            self._pusher.connection.bind("pusher:connection_established", callback)
            self._pusher.connect()

            # Keep thread alive
            while True:
                import time
                time.sleep(1)

        self._pusher_thread = Thread(target=pusher_thread, daemon=True)
        self._pusher_thread.start()
        _LOGGER.debug("Started Pusher connection thread")

    def subscribe_to_device(
        self, channel_id: str, callback: callable
    ) -> None:
        """Subscribe to device updates via Pusher."""
        if not self._pusher:
            _LOGGER.error("Pusher not initialized")
            return

        channel_name = f"{PUSHER_CHANNEL_PREFIX}{channel_id}"
        channel = self._pusher.subscribe(channel_name)

        # Bind to all possible events
        channel.bind("shower-update", callback)
        channel.bind("status-update", callback)

        self._channels[channel_id] = channel
        self._device_callbacks[channel_id] = callback

        _LOGGER.debug("Subscribed to channel: %s", channel_name)

    async def set_shower_mode(self, serial_number: str, mode: str) -> None:
        """Set shower mode (on/off/pause) via Pusher client event."""
        channel_id = await self._get_channel_id(serial_number)
        if not channel_id:
            _LOGGER.error("No channel ID found for device %s", serial_number)
            return

        channel = self._channels.get(channel_id)
        if not channel:
            _LOGGER.error("Not subscribed to channel for device %s", serial_number)
            return

        # Send client event to control the shower
        event_data = {"mode": mode}
        channel.trigger("client-shower-control", event_data)
        _LOGGER.debug("Sent mode change to %s: %s", serial_number, mode)

    async def activate_preset(self, serial_number: str, preset_position: int) -> None:
        """Activate a preset via Pusher client event."""
        channel_id = await self._get_channel_id(serial_number)
        if not channel_id:
            _LOGGER.error("No channel ID found for device %s", serial_number)
            return

        channel = self._channels.get(channel_id)
        if not channel:
            _LOGGER.error("Not subscribed to channel for device %s", serial_number)
            return

        event_data = {"position": preset_position}
        channel.trigger("client-activate-preset", event_data)
        _LOGGER.debug("Activated preset %d on %s", preset_position, serial_number)

    async def set_target_temperature(self, serial_number: str, temperature: float) -> None:
        """Set target temperature via Pusher client event."""
        channel_id = await self._get_channel_id(serial_number)
        if not channel_id:
            _LOGGER.error("No channel ID found for device %s", serial_number)
            return

        channel = self._channels.get(channel_id)
        if not channel:
            _LOGGER.error("Not subscribed to channel for device %s", serial_number)
            return

        event_data = {"target_temperature": int(temperature)}
        channel.trigger("client-set-temperature", event_data)
        _LOGGER.debug("Set temperature to %d on %s", int(temperature), serial_number)

    async def set_outlet_state(
        self, serial_number: str, outlet_position: int, active: bool
    ) -> None:
        """Set outlet state via Pusher client event."""
        channel_id = await self._get_channel_id(serial_number)
        if not channel_id:
            _LOGGER.error("No channel ID found for device %s", serial_number)
            return

        channel = self._channels.get(channel_id)
        if not channel:
            _LOGGER.error("Not subscribed to channel for device %s", serial_number)
            return

        event_data = {"position": outlet_position, "active": active}
        channel.trigger("client-set-outlet", event_data)
        _LOGGER.debug(
            "Set outlet %d to %s on %s", outlet_position, active, serial_number
        )

    async def _get_channel_id(self, serial_number: str) -> Optional[str]:
        """Get channel ID for a device."""
        device_details = await self.get_device_details(serial_number)
        return device_details.get("channel")

    def stop_pusher(self):
        """Stop the Pusher connection."""
        if self._pusher:
            self._pusher.disconnect()
            _LOGGER.debug("Disconnected from Pusher")
