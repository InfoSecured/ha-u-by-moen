"""Button platform for U by Moen."""
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ATTR_PRESETS
from .coordinator import MoenDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Moen button entities."""
    coordinator: MoenDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    api = hass.data[DOMAIN][entry.entry_id]["api"]

    entities = []
    for serial_number, device_data in coordinator.data.items():
        # Add preset buttons
        presets = device_data.get(ATTR_PRESETS, [])
        for preset in presets:
            position = preset.get("position")
            if position:
                entities.append(
                    MoenPresetButton(coordinator, api, serial_number, position)
                )

    async_add_entities(entities)


class MoenPresetButton(CoordinatorEntity, ButtonEntity):
    """Representation of a Moen shower preset button."""

    _attr_icon = "mdi:playlist-star"

    def __init__(
        self,
        coordinator: MoenDataUpdateCoordinator,
        api,
        serial_number: str,
        preset_position: int,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._api = api
        self._serial_number = serial_number
        self._preset_position = preset_position
        self._attr_unique_id = f"{serial_number}_preset_{preset_position}"

    @property
    def device_info(self):
        """Return device information."""
        device_data = self.coordinator.data[self._serial_number]
        return {
            "identifiers": {(DOMAIN, self._serial_number)},
            "name": device_data.get("name", f"Moen Shower {self._serial_number}"),
            "manufacturer": "Moen",
            "model": "U by Moen Shower",
            "sw_version": device_data.get("current_firmware_version"),
        }

    @property
    def name(self) -> str:
        """Return the name of the preset button."""
        device_data = self.coordinator.data[self._serial_number]
        device_name = device_data.get("name", f"Shower {self._serial_number}")

        preset = self._get_preset_data()
        if preset:
            preset_title = preset.get("title", f"Preset {self._preset_position}")
            return f"{device_name} {preset_title}"

        return f"{device_name} Preset {self._preset_position}"

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.debug(
            "Activating preset %d on device %s",
            self._preset_position,
            self._serial_number,
        )
        await self._api.activate_preset(self._serial_number, self._preset_position)
        # State will update via Pusher client-state-reported event

    def _get_preset_data(self) -> dict:
        """Get the preset data for this position."""
        device_data = self.coordinator.data[self._serial_number]
        presets = device_data.get(ATTR_PRESETS, [])
        for preset in presets:
            if preset.get("position") == self._preset_position:
                return preset
        return {}

    @property
    def extra_state_attributes(self):
        """Return additional state attributes."""
        preset = self._get_preset_data()
        if not preset:
            return {}

        return {
            "target_temperature": preset.get("target_temperature"),
            "greeting": preset.get("greeting"),
            "timer_enabled": preset.get("timer_enabled"),
            "timer_length": preset.get("timer_length"),
            "ready_sounds_alert": preset.get("ready_sounds_alert"),
            "ready_pushes_notification": preset.get("ready_pushes_notification"),
        }
