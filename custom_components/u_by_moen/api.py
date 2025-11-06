"""API client for U by Moen."""
import logging
import asyncio
import json
from typing import Any, Dict, List, Optional, Callable
import aiohttp

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
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._socket_id: Optional[str] = None
        self._subscribed_channels: Dict[str, bool] = {}
        self._ws_task: Optional[asyncio.Task] = None
        self._update_callbacks: Dict[str, Callable] = {}
        self._running = False

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

    async def get_pusher_auth(self, channel_name: str) -> str:
        """Get Pusher authentication for private channel."""
        if not self._token:
            await self.authenticate()

        if not self._socket_id:
            _LOGGER.error("No socket_id available for Pusher auth")
            return ""

        url = f"{API_BASE_URL}{API_PUSHER_AUTH}"
        headers = {
            "User-Token": self._token,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = f"socket_id={self._socket_id}&channel_name={channel_name}"

        try:
            async with self._session.post(url, headers=headers, data=data) as response:
                response.raise_for_status()
                auth_data = await response.json()
                return auth_data.get("auth", "")

        except aiohttp.ClientError as err:
            _LOGGER.error("Failed to get Pusher auth: %s", err)
            return ""

    async def connect_pusher(self) -> bool:
        """Connect to Pusher WebSocket."""
        if not self._pusher_key or not self._pusher_cluster:
            _LOGGER.error("Pusher credentials not available")
            return False

        if self._running:
            _LOGGER.debug("Pusher already connected")
            return True

        ws_url = f"wss://ws-{self._pusher_cluster}.pusher.com/app/{self._pusher_key}?protocol=7&client=python-client&version=1.0"

        try:
            self._ws = await self._session.ws_connect(ws_url)
            self._running = True
            _LOGGER.info("Connected to Pusher WebSocket")

            # Start message handler task
            self._ws_task = asyncio.create_task(self._handle_messages())
            return True

        except Exception as err:
            _LOGGER.error("Failed to connect to Pusher: %s", err)
            return False

    async def _handle_messages(self):
        """Handle incoming WebSocket messages."""
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._process_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.error("WebSocket error: %s", self._ws.exception())
                    break
        except asyncio.CancelledError:
            _LOGGER.debug("WebSocket handler cancelled")
        except Exception as err:
            _LOGGER.error("Error handling WebSocket messages: %s", err)
        finally:
            self._running = False

    async def _process_message(self, message: str):
        """Process a Pusher message."""
        try:
            data = json.loads(message)
            event = data.get("event")

            if event == "pusher:connection_established":
                connection_data = json.loads(data.get("data", "{}"))
                self._socket_id = connection_data.get("socket_id")
                _LOGGER.info("Pusher connection established, socket_id: %s", self._socket_id)

            elif event == "pusher:error":
                _LOGGER.error("Pusher error: %s", data.get("data"))

            elif event == "pusher_internal:subscription_succeeded":
                channel = data.get("channel")
                _LOGGER.info("Successfully subscribed to channel: %s", channel)
                self._subscribed_channels[channel] = True

            else:
                # Handle custom events (device updates)
                channel = data.get("channel", "")
                event_data = data.get("data")
                if event_data:
                    try:
                        event_data = json.loads(event_data) if isinstance(event_data, str) else event_data
                    except json.JSONDecodeError:
                        pass

                _LOGGER.debug("Received event '%s' on channel '%s': %s", event, channel, event_data)

                # Call update callbacks
                if channel in self._update_callbacks:
                    await self._update_callbacks[channel](event, event_data)

        except json.JSONDecodeError as err:
            _LOGGER.error("Failed to parse Pusher message: %s", err)
        except Exception as err:
            _LOGGER.error("Error processing Pusher message: %s", err)

    async def subscribe_to_channel(self, channel_id: str, callback: Callable):
        """Subscribe to a device channel."""
        if not self._running:
            _LOGGER.error("Pusher not connected, cannot subscribe")
            return False

        channel_name = f"{PUSHER_CHANNEL_PREFIX}{channel_id}"

        # Wait for socket_id if not yet available
        retries = 0
        while not self._socket_id and retries < 10:
            await asyncio.sleep(0.5)
            retries += 1

        if not self._socket_id:
            _LOGGER.error("No socket_id available, cannot subscribe")
            return False

        # Get auth for private channel
        auth = await self.get_pusher_auth(channel_name)
        if not auth:
            _LOGGER.error("Failed to get auth for channel %s", channel_name)
            return False

        # Send subscription message
        subscribe_msg = {
            "event": "pusher:subscribe",
            "data": {
                "channel": channel_name,
                "auth": auth,
            }
        }

        try:
            await self._ws.send_json(subscribe_msg)
            self._update_callbacks[channel_name] = callback
            _LOGGER.debug("Sent subscription request for channel: %s", channel_name)
            return True

        except Exception as err:
            _LOGGER.error("Failed to subscribe to channel %s: %s", channel_name, err)
            return False

    async def send_control_event(self, channel_id: str, action: str, params: Dict[str, Any]):
        """Send a control event to the device."""
        if not self._running or not self._ws:
            _LOGGER.error("Pusher not connected, cannot send event")
            return False

        channel_name = f"{PUSHER_CHANNEL_PREFIX}{channel_id}"

        if channel_name not in self._subscribed_channels:
            _LOGGER.error("Not subscribed to channel %s", channel_name)
            return False

        # Format matches the real Moen app: client-state-desired with type=control
        message = {
            "event": "client-state-desired",
            "channel": channel_name,
            "data": {
                "type": "control",
                "data": {
                    "action": action,
                    "params": params
                }
            }
        }

        try:
            await self._ws.send_json(message)
            _LOGGER.debug("Sent control event '%s' to channel '%s': %s", action, channel_name, params)
            return True

        except Exception as err:
            _LOGGER.error("Failed to send control event: %s", err)
            return False

    async def set_shower_mode(self, serial_number: str, mode: str) -> None:
        """Set shower mode (on/off)."""
        device_details = await self.get_device_details(serial_number)
        channel_id = device_details.get("channel")

        if not channel_id:
            _LOGGER.error("No channel ID found for device %s", serial_number)
            return

        # Use the actual action names from the real app
        if mode == "on":
            await self.send_control_event(channel_id, "shower_on", {"preset": "0"})
        else:
            await self.send_control_event(channel_id, "shower_off", {})

    async def activate_preset(self, serial_number: str, preset_position: int) -> None:
        """Activate a preset."""
        device_details = await self.get_device_details(serial_number)
        channel_id = device_details.get("channel")

        if not channel_id:
            _LOGGER.error("No channel ID found for device %s", serial_number)
            return

        # Get preset details
        presets = device_details.get("presets", [])
        preset = None
        for p in presets:
            if p.get("position") == preset_position:
                preset = p
                break

        if not preset:
            _LOGGER.error("Preset %d not found", preset_position)
            return

        # Send shower_set with all preset parameters (matches real app)
        params = {
            "active_preset": preset_position,
            "title": preset.get("title", ""),
            "greeting": preset.get("greeting", ""),
            "target_temperature": preset.get("target_temperature", 100),
            "outlets": preset.get("outlets", []),
            "timer_enabled": preset.get("timer_enabled", False),
            "timer_length": preset.get("timer_length", 600),
            "timer_ends_shower": preset.get("timer_ends_shower", False),
            "timer_sounds_alert": preset.get("timer_sounds_alert", True),
            "ready_pauses_water": preset.get("ready_pauses_water", False),
            "ready_pushes_notification": preset.get("ready_pushes_notification", False),
            "ready_sounds_alert": preset.get("ready_sounds_alert", True),
        }

        await self.send_control_event(channel_id, "shower_set", params)

    async def set_target_temperature(self, serial_number: str, temperature: float) -> None:
        """Set target temperature."""
        device_details = await self.get_device_details(serial_number)
        channel_id = device_details.get("channel")

        if not channel_id:
            _LOGGER.error("No channel ID found for device %s", serial_number)
            return

        await self.send_control_event(channel_id, "temperature_set", {"target_temperature": int(temperature)})

    async def set_outlet_state(self, serial_number: str, outlet_position: int, active: bool) -> None:
        """Set outlet state - sets all outlets at once."""
        device_details = await self.get_device_details(serial_number)
        channel_id = device_details.get("channel")

        if not channel_id:
            _LOGGER.error("No channel ID found for device %s", serial_number)
            return

        # Get current outlet states and update the specific outlet
        outlets = device_details.get("outlets", [])
        outlet_states = []
        for outlet in outlets:
            pos = outlet.get("position")
            is_active = outlet.get("active", False)
            if pos == outlet_position:
                is_active = active
            outlet_states.append({"position": pos, "active": is_active})

        await self.send_control_event(channel_id, "outlets_set", {"outlets": outlet_states})

    async def disconnect_pusher(self):
        """Disconnect from Pusher WebSocket."""
        self._running = False

        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        if self._ws and not self._ws.closed:
            await self._ws.close()

        self._ws = None
        self._socket_id = None
        self._subscribed_channels.clear()
        _LOGGER.info("Disconnected from Pusher")

    def stop_pusher(self):
        """Stop Pusher connection (sync wrapper for unload)."""
        if self._ws_task:
            self._ws_task.cancel()
