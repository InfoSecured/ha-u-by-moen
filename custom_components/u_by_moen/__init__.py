"""The U by Moen integration."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MoenApi
from .const import DOMAIN
from .coordinator import MoenDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE, Platform.SWITCH, Platform.SENSOR, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up U by Moen from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Create API client
    session = async_get_clientsession(hass)
    api = MoenApi(
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        session,
    )

    # Authenticate and get credentials
    try:
        await api.authenticate()
        await api.get_pusher_credentials()
    except Exception as err:
        _LOGGER.error("Failed to authenticate with Moen API: %s", err)
        return False

    # Create coordinator
    coordinator = MoenDataUpdateCoordinator(hass, api)

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator and API
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "api": api,
    }

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Set up Pusher WebSocket connection
    await api.connect_pusher()

    # Subscribe to device channels for real-time updates
    # Create a mapping from channel_id to serial_number
    channel_to_serial = {}
    for serial_number, device_data in coordinator.data.items():
        channel_id = device_data.get("channel")
        if channel_id:
            channel_to_serial[channel_id] = serial_number

    async def create_device_update_callback(serial_number: str):
        """Create a callback for a specific device."""
        async def device_update_callback(event: str, data: dict):
            """Handle device updates from Pusher."""
            _LOGGER.info("Pusher event received for %s: event='%s', data=%s", serial_number, event, data)

            # Parse the event data and update coordinator
            if event == "client-state-reported" and isinstance(data, dict):
                # This is a state update from the device
                # Extract the relevant fields and update coordinator
                update_data = {}

                # Map Pusher fields to our device data fields
                if "mode" in data:
                    update_data["mode"] = data["mode"]
                if "target_temperature" in data:
                    update_data["target_temperature"] = data["target_temperature"]
                if "current_temperature" in data:
                    update_data["current_temperature"] = data["current_temperature"]
                if "outlets" in data:
                    update_data["outlets"] = data["outlets"]
                if "active_preset" in data:
                    update_data["active_preset"] = data["active_preset"]
                if "timer_enabled" in data:
                    update_data["timer_enabled"] = data["timer_enabled"]
                if "timer_remaining" in data:
                    update_data["timer_remaining"] = data["timer_remaining"]

                if update_data:
                    coordinator.update_device_from_pusher(serial_number, update_data)
                    _LOGGER.debug("Updated device %s from Pusher with: %s", serial_number, update_data)
                else:
                    _LOGGER.debug("No recognized fields in Pusher event, requesting full refresh")
                    await coordinator.async_request_refresh()
            else:
                # Unknown event type, do a full refresh to be safe
                _LOGGER.debug("Unknown event type '%s', requesting full refresh", event)
                await coordinator.async_request_refresh()

        return device_update_callback

    for serial_number, device_data in coordinator.data.items():
        channel_id = device_data.get("channel")
        if channel_id:
            callback = await create_device_update_callback(serial_number)
            await api.subscribe_to_channel(channel_id, callback)
            _LOGGER.info("Subscribed to updates for device %s", serial_number)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Disconnect from Pusher
        api = hass.data[DOMAIN][entry.entry_id]["api"]
        await api.disconnect_pusher()

        # Remove the entry
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
