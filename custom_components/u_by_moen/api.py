"""API client for U by Moen."""
import logging
import asyncio
import json
import time
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

# Pusher protocol close/error codes.
# 4000-4099: fatal, do not reconnect. 4100-4199: reconnect after a backoff.
# 4200-4299: reconnect immediately.
PUSHER_ERROR_FATAL = range(4000, 4100)
PUSHER_ERROR_BACKOFF = range(4100, 4200)

DEFAULT_ACTIVITY_TIMEOUT = 120
# Pusher can advertise a very long activity_timeout. Cap it so a half-open
# socket is still detected within a couple of minutes.
MAX_ACTIVITY_TIMEOUT = 120
MIN_ACTIVITY_TIMEOUT = 30
# How long to wait for any inbound frame after our ping before declaring the
# connection dead.
PONG_TIMEOUT = 30
IDLE_CHECK_INTERVAL = 10
SOCKET_READY_TIMEOUT = 20
RECONNECT_DELAY_MIN = 2
RECONNECT_DELAY_MAX = 300


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

        # Desired state, as opposed to _running which is the actual state.
        self._should_run = False
        # channel_id -> callback, kept so channels can be re-subscribed after a
        # reconnect. _update_callbacks is keyed by full channel name and is
        # rebuilt from this on every reconnect.
        self._channel_callbacks: Dict[str, Callable] = {}
        self._connect_lock = asyncio.Lock()
        self._socket_ready = asyncio.Event()
        self._reconnect_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._activity_timeout = DEFAULT_ACTIVITY_TIMEOUT
        self._last_activity = time.monotonic()
        self._fatal_error = False

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

    async def get_devices(self, _retry: bool = True) -> List[Dict[str, Any]]:
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

        except aiohttp.ClientResponseError as err:
            # The account token expires server-side; re-auth once rather than
            # failing the coordinator refresh. _retry bounds this to one extra
            # attempt so a persistently rejecting API cannot recurse forever.
            if err.status == 401 and _retry:
                _LOGGER.warning("Token rejected during get_devices, re-authenticating")
                self._token = None
                await self.authenticate()
                return await self.get_devices(_retry=False)
            raise MoenApiError(f"Failed to get devices: {err}") from err
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
        """Connect to Pusher WebSocket and keep the connection alive."""
        self._should_run = True
        self._fatal_error = False
        return await self._ensure_connected()

    @property
    def connected(self) -> bool:
        """Return True when the Pusher WebSocket is usable."""
        return bool(self._running and self._ws and not self._ws.closed)

    async def _ensure_connected(self) -> bool:
        """Connect (or reconnect) to Pusher and restore subscriptions."""
        async with self._connect_lock:
            if self.connected:
                return True

            if self._fatal_error:
                _LOGGER.debug("Not reconnecting to Pusher after a fatal error")
                return False

            await self._teardown_ws()

            # Credentials are tied to the account session and can go stale, so
            # refresh them on every (re)connect rather than only at setup.
            try:
                await self.get_pusher_credentials()
            except MoenApiError as err:
                _LOGGER.error("Cannot refresh Pusher credentials: %s", err)
                return False

            if not self._pusher_key or not self._pusher_cluster:
                _LOGGER.error("Pusher credentials not available")
                return False

            ws_url = (
                f"wss://ws-{self._pusher_cluster}.pusher.com/app/{self._pusher_key}"
                "?protocol=7&client=python-client&version=1.0"
            )

            self._socket_ready.clear()

            try:
                self._ws = await self._session.ws_connect(ws_url)
            except Exception as err:
                _LOGGER.error("Failed to connect to Pusher: %s", err)
                return False

            self._running = True
            self._ws_task = asyncio.create_task(self._handle_messages())

            try:
                await asyncio.wait_for(
                    self._socket_ready.wait(), timeout=SOCKET_READY_TIMEOUT
                )
            except asyncio.TimeoutError:
                _LOGGER.error("Timed out waiting for Pusher connection_established")
                await self._teardown_ws()
                return False

            _LOGGER.info("Connected to Pusher WebSocket")

            self._last_activity = time.monotonic()
            self._keepalive_task = asyncio.create_task(self._keepalive())

            # Re-subscribe every known channel; a new socket_id invalidates the
            # previous subscriptions and their auth signatures.
            self._subscribed_channels.clear()
            self._update_callbacks.clear()
            for channel_id, callback in list(self._channel_callbacks.items()):
                await self._do_subscribe(channel_id, callback)

            return True

    async def _teardown_ws(self) -> None:
        """Tear down the current WebSocket and its helper tasks."""
        self._running = False
        self._socket_ready.clear()

        for task_attr in ("_keepalive_task", "_ws_task"):
            task = getattr(self, task_attr)
            if task and task is not asyncio.current_task():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            setattr(self, task_attr, None)

        if self._ws and not self._ws.closed:
            try:
                await self._ws.close()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Error closing Pusher WebSocket: %s", err)

        self._ws = None
        self._socket_id = None
        self._subscribed_channels.clear()

    def _schedule_reconnect(self, immediate: bool = False) -> None:
        """Start the background reconnect loop if it is not already running."""
        if not self._should_run or self._fatal_error:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_loop(immediate))

    async def _reconnect_loop(self, immediate: bool = False) -> None:
        """Reconnect to Pusher with exponential backoff until it succeeds."""
        delay = 0 if immediate else RECONNECT_DELAY_MIN
        try:
            while self._should_run and not self._fatal_error:
                if delay:
                    _LOGGER.debug("Reconnecting to Pusher in %s seconds", delay)
                    await asyncio.sleep(delay)

                if await self._ensure_connected():
                    _LOGGER.info("Pusher connection re-established")
                    return

                delay = min(max(delay * 2, RECONNECT_DELAY_MIN), RECONNECT_DELAY_MAX)
        except asyncio.CancelledError:
            _LOGGER.debug("Pusher reconnect loop cancelled")
            raise

    async def _keepalive(self) -> None:
        """Ping only when the connection has gone idle, per the Pusher spec.

        The device normally chats every ~60s, so this rarely needs to fire.
        Its real value is detecting a half-open socket: if nothing at all
        arrives within PONG_TIMEOUT of our ping, the connection is treated as
        dead and torn down so the reconnect path takes over.
        """
        try:
            while True:
                await asyncio.sleep(IDLE_CHECK_INTERVAL)
                if not self.connected:
                    return

                idle = time.monotonic() - self._last_activity
                if idle < self._activity_timeout:
                    continue

                ping_sent_at = time.monotonic()
                try:
                    await self._ws.send_json({"event": "pusher:ping", "data": {}})
                    _LOGGER.debug("Connection idle %.0fs, sent pusher:ping", idle)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Pusher keepalive ping failed: %s", err)
                    self._schedule_reconnect(immediate=True)
                    return

                await asyncio.sleep(PONG_TIMEOUT)

                if self._last_activity < ping_sent_at:
                    _LOGGER.warning(
                        "No Pusher traffic within %ss of ping; connection is dead, "
                        "forcing reconnect",
                        PONG_TIMEOUT,
                    )
                    if self._ws and not self._ws.closed:
                        await self._ws.close()
                    return
        except asyncio.CancelledError:
            raise

    async def _handle_messages(self):
        """Handle incoming WebSocket messages."""
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._process_message(msg.data)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    _LOGGER.warning("Pusher WebSocket closed by server")
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.error("WebSocket error: %s", self._ws.exception())
                    break
        except asyncio.CancelledError:
            _LOGGER.debug("WebSocket handler cancelled")
            raise
        except Exception as err:
            _LOGGER.error("Error handling WebSocket messages: %s", err)
        finally:
            self._running = False
            self._socket_ready.clear()
            if self._should_run and not self._fatal_error:
                _LOGGER.warning("Pusher connection lost, scheduling reconnect")
                self._schedule_reconnect()

    async def _process_message(self, message: str):
        """Process a Pusher message."""
        # Any inbound frame is proof the connection is alive.
        self._last_activity = time.monotonic()
        try:
            data = json.loads(message)
            event = data.get("event")

            if event == "pusher:connection_established":
                connection_data = json.loads(data.get("data", "{}"))
                self._socket_id = connection_data.get("socket_id")
                # Clamp: Pusher may advertise a timeout far longer than is
                # useful for detecting a dead socket.
                advertised = int(
                    connection_data.get("activity_timeout", DEFAULT_ACTIVITY_TIMEOUT)
                )
                self._activity_timeout = max(
                    MIN_ACTIVITY_TIMEOUT, min(advertised, MAX_ACTIVITY_TIMEOUT)
                )
                self._socket_ready.set()
                _LOGGER.info(
                    "Pusher connection established, socket_id: %s "
                    "(activity_timeout advertised=%ss, using=%ss)",
                    self._socket_id,
                    advertised,
                    self._activity_timeout,
                )

            elif event == "pusher:ping":
                # The server drops clients that do not answer its ping.
                try:
                    await self._ws.send_json({"event": "pusher:pong", "data": {}})
                    _LOGGER.debug("Replied to pusher:ping with pusher:pong")
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("Failed to send pusher:pong: %s", err)

            elif event == "pusher:pong":
                _LOGGER.debug("Received pusher:pong")

            elif event == "pusher:error":
                error_data = data.get("data")
                if isinstance(error_data, str):
                    try:
                        error_data = json.loads(error_data)
                    except json.JSONDecodeError:
                        error_data = {}
                code = (error_data or {}).get("code")
                _LOGGER.error("Pusher error: %s", data.get("data"))

                if isinstance(code, int) and code in PUSHER_ERROR_FATAL:
                    _LOGGER.error(
                        "Pusher returned fatal error %s; not reconnecting", code
                    )
                    self._fatal_error = True
                    self._should_run = False
                    if self._ws and not self._ws.closed:
                        await self._ws.close()
                    return

                # 4100-4199 back off, 4200-4299 reconnect immediately. Closing
                # the socket makes _handle_messages exit and trigger reconnect.
                immediate = not (isinstance(code, int) and code in PUSHER_ERROR_BACKOFF)
                _LOGGER.warning(
                    "Pusher asked us to reconnect (code=%s, immediate=%s)",
                    code,
                    immediate,
                )
                if self._ws and not self._ws.closed:
                    await self._ws.close()
                self._schedule_reconnect(immediate=immediate)

            elif event == "pusher_internal:subscription_succeeded":
                channel = data.get("channel")
                _LOGGER.info("Successfully subscribed to channel: %s", channel)
                self._subscribed_channels[channel] = True

            elif event == "pusher_internal:subscription_error":
                _LOGGER.error(
                    "Pusher subscription error on channel %s: %s",
                    data.get("channel"),
                    data.get("data"),
                )

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
        """Subscribe to a device channel and remember it for reconnects."""
        self._channel_callbacks[channel_id] = callback

        if not self.connected and not await self._ensure_connected():
            _LOGGER.error("Pusher not connected, cannot subscribe")
            return False

        return await self._do_subscribe(channel_id, callback)

    async def _do_subscribe(self, channel_id: str, callback: Callable) -> bool:
        """Send the subscribe frame for a channel on the current socket."""
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

    async def _wait_for_subscription(self, channel_name: str, timeout: float = 10) -> bool:
        """Wait until the subscription for a channel is confirmed."""
        waited = 0.0
        while waited < timeout:
            if self._subscribed_channels.get(channel_name):
                return True
            await asyncio.sleep(0.25)
            waited += 0.25
        return bool(self._subscribed_channels.get(channel_name))

    async def send_control_event(self, channel_id: str, action: str, params: Dict[str, Any]):
        """Send a control event to the device."""
        channel_name = f"{PUSHER_CHANNEL_PREFIX}{channel_id}"

        if not self.connected:
            _LOGGER.warning(
                "Pusher not connected; reconnecting before sending '%s'", action
            )
            if not await self._ensure_connected():
                _LOGGER.error("Pusher reconnect failed, cannot send event '%s'", action)
                return False

        if channel_name not in self._subscribed_channels:
            _LOGGER.debug(
                "Channel %s not subscribed yet, subscribing before send", channel_name
            )
            callback = self._channel_callbacks.get(channel_id)
            if callback is not None:
                await self._do_subscribe(channel_id, callback)
            if not await self._wait_for_subscription(channel_name):
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
            _LOGGER.info("Sending control event '%s' to channel '%s': %s", action, channel_name, params)
            _LOGGER.debug("Full message: %s", message)
            await self._ws.send_json(message)
            _LOGGER.info("Successfully sent control event '%s'", action)
            return True

        except Exception as err:
            _LOGGER.error("Failed to send control event: %s", err)
            self._schedule_reconnect(immediate=True)
            return False

    async def set_shower_mode(
        self, serial_number: str, mode: str, preset: Optional[str] = None
    ) -> None:
        """Set shower mode (on/off)."""
        device_details = await self.get_device_details(serial_number)
        channel_id = device_details.get("channel")

        if not channel_id:
            _LOGGER.error("No channel ID found for device %s", serial_number)
            return

        # Use the actual action names from the real app
        if mode == "on":
            preset_param = preset if preset is not None else "0"
            await self.send_control_event(channel_id, "shower_on", {"preset": preset_param})
        else:
            await self.send_control_event(channel_id, "shower_off", {})

    async def resume_shower(
        self, serial_number: str, preset: Optional[int] = None
    ) -> None:
        """Resume water after a preset paused the shower."""
        device_details = await self.get_device_details(serial_number)
        channel_id = device_details.get("channel")

        if not channel_id:
            _LOGGER.error("No channel ID found for device %s", serial_number)
            return

        active_preset = preset
        if active_preset is None:
            active_preset = device_details.get("active_preset")

        if not active_preset:
            _LOGGER.error(
                "Cannot resume shower %s because no active preset is reported",
                serial_number,
            )
            return

        preset_param = str(active_preset)

        _LOGGER.info(
            "Resuming shower %s from paused-by-preset state using preset %s",
            serial_number,
            preset_param,
        )
        await self.send_control_event(
            channel_id,
            "shower_on",
            {"preset": preset_param},
        )

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

        # Activate the preset by sending ONLY shower_set with all parameters.
        # The Moen app does NOT send shower_on after shower_set - shower_set alone
        # is sufficient to turn on the shower AND apply all preset settings.
        # This allows ready_pauses_water to work correctly (mode becomes 'paused-by-preset')
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

        # Send only shower_set - this activates the preset with all its settings
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
        self._should_run = False

        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._reconnect_task = None

        await self._teardown_ws()
        self._channel_callbacks.clear()
        self._update_callbacks.clear()
        _LOGGER.info("Disconnected from Pusher")

    def stop_pusher(self):
        """Stop Pusher connection (sync wrapper for unload)."""
        self._should_run = False
        if self._reconnect_task:
            self._reconnect_task.cancel()
        if self._keepalive_task:
            self._keepalive_task.cancel()
        if self._ws_task:
            self._ws_task.cancel()
