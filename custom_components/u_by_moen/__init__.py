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

    # TODO: Set up Pusher connection for real-time updates
    # This will be implemented once we understand the Pusher event format

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Stop Pusher connection
        api = hass.data[DOMAIN][entry.entry_id]["api"]
        api.stop_pusher()

        # Remove the entry
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
