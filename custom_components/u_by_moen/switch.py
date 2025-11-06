"""Switch platform for U by Moen."""
import logging
from typing import Any, Optional

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    ATTR_OUTLETS,
    MODE_OFF,
    MODE_ON,
    ICON_SHOWER,
    ICON_OUTLET,
)
from .coordinator import MoenDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Outlet icon mappings based on icon_index from API
OUTLET_ICONS = {
    0: "mdi:shower-head",  # Shower head
    1: "mdi:shower",  # Rain shower
    2: "mdi:water",  # Hand shower
    3: "mdi:spray",  # Body spray
    4: "mdi:water-pump",  # Pump/valve
    5: "mdi:waves",  # Water feature
    6: "mdi:bathtub",  # Tub spout
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Moen switch entities."""
    coordinator: MoenDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    api = hass.data[DOMAIN][entry.entry_id]["api"]

    entities = []
    for serial_number, device_data in coordinator.data.items():
        # Add main shower on/off switch
        entities.append(MoenShowerSwitch(coordinator, api, serial_number))

        # Add outlet switches
        outlets = device_data.get(ATTR_OUTLETS, [])
        for outlet in outlets:
            position = outlet.get("position")
            if position:
                entities.append(
                    MoenOutletSwitch(coordinator, api, serial_number, position)
                )

    async_add_entities(entities)


class MoenShowerSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of a Moen shower on/off switch."""

    _attr_icon = ICON_SHOWER

    def __init__(
        self,
        coordinator: MoenDataUpdateCoordinator,
        api,
        serial_number: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._api = api
        self._serial_number = serial_number
        self._attr_unique_id = f"{serial_number}_power"

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
        """Return the name of the switch."""
        device_data = self.coordinator.data[self._serial_number]
        device_name = device_data.get("name", f"Shower {self._serial_number}")
        return f"{device_name} Power"

    @property
    def is_on(self) -> bool:
        """Return true if the shower is on."""
        device_data = self.coordinator.data[self._serial_number]
        mode = device_data.get("mode", MODE_OFF)
        return mode == MODE_ON

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the shower on."""
        await self._api.set_shower_mode(self._serial_number, MODE_ON)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the shower off."""
        await self._api.set_shower_mode(self._serial_number, MODE_OFF)
        await self.coordinator.async_request_refresh()


class MoenOutletSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of a Moen shower outlet switch."""

    def __init__(
        self,
        coordinator: MoenDataUpdateCoordinator,
        api,
        serial_number: str,
        outlet_position: int,
    ) -> None:
        """Initialize the outlet switch."""
        super().__init__(coordinator)
        self._api = api
        self._serial_number = serial_number
        self._outlet_position = outlet_position
        self._attr_unique_id = f"{serial_number}_outlet_{outlet_position}"

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
        """Return the name of the outlet switch."""
        device_data = self.coordinator.data[self._serial_number]
        device_name = device_data.get("name", f"Shower {self._serial_number}")

        # Get outlet name from icon index
        outlet = self._get_outlet_data()
        if outlet:
            icon_index = outlet.get("icon_index", 0)
            outlet_type = self._get_outlet_type(icon_index)
            return f"{device_name} {outlet_type}"

        return f"{device_name} Outlet {self._outlet_position}"

    @property
    def icon(self) -> str:
        """Return the icon for this outlet."""
        outlet = self._get_outlet_data()
        if outlet:
            icon_index = outlet.get("icon_index", 0)
            return OUTLET_ICONS.get(icon_index, ICON_OUTLET)
        return ICON_OUTLET

    @property
    def is_on(self) -> bool:
        """Return true if the outlet is active."""
        outlet = self._get_outlet_data()
        if outlet:
            return outlet.get("active", False)
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the outlet on."""
        await self._api.set_outlet_state(self._serial_number, self._outlet_position, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the outlet off."""
        await self._api.set_outlet_state(self._serial_number, self._outlet_position, False)
        await self.coordinator.async_request_refresh()

    def _get_outlet_data(self) -> Optional[dict]:
        """Get the outlet data for this position."""
        device_data = self.coordinator.data[self._serial_number]
        outlets = device_data.get(ATTR_OUTLETS, [])
        for outlet in outlets:
            if outlet.get("position") == self._outlet_position:
                return outlet
        return None

    def _get_outlet_type(self, icon_index: int) -> str:
        """Get a friendly name for the outlet type."""
        outlet_names = {
            0: "Shower Head",
            1: "Rain Shower",
            2: "Hand Shower",
            3: "Body Spray",
            4: "Valve",
            5: "Water Feature",
            6: "Tub Spout",
        }
        return outlet_names.get(icon_index, f"Outlet {self._outlet_position}")
