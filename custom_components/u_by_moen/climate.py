"""Climate platform for U by Moen."""
import logging
from typing import Any, List, Optional

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    ATTR_SERIAL_NUMBER,
    ATTR_MODE,
    ATTR_CURRENT_TEMP,
    ATTR_TARGET_TEMP,
    ATTR_MAX_TEMP,
    MODE_OFF,
    ICON_SHOWER,
)
from .coordinator import MoenDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Moen climate entities."""
    coordinator: MoenDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    api = hass.data[DOMAIN][entry.entry_id]["api"]

    entities = []
    for serial_number, device_data in coordinator.data.items():
        entities.append(MoenClimate(coordinator, api, serial_number))

    async_add_entities(entities)


class MoenClimate(CoordinatorEntity, ClimateEntity):
    """Representation of a Moen shower as a climate entity."""

    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
    )
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_icon = ICON_SHOWER

    def __init__(
        self,
        coordinator: MoenDataUpdateCoordinator,
        api,
        serial_number: str,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator)
        self._api = api
        self._serial_number = serial_number
        self._attr_unique_id = f"{serial_number}_climate"

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
        """Return the name of the entity."""
        device_data = self.coordinator.data[self._serial_number]
        return device_data.get("name", f"Moen Shower {self._serial_number}")

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        device_data = self.coordinator.data[self._serial_number]
        mode = device_data.get(ATTR_MODE, MODE_OFF)
        # Any mode other than "off" means the shower is on (adjusting, ready, pause)
        return HVACMode.HEAT if mode != MODE_OFF else HVACMode.OFF

    @property
    def current_temperature(self) -> Optional[float]:
        """Return the current temperature."""
        device_data = self.coordinator.data[self._serial_number]
        return device_data.get(ATTR_CURRENT_TEMP)

    @property
    def target_temperature(self) -> Optional[float]:
        """Return the target temperature."""
        device_data = self.coordinator.data[self._serial_number]
        return device_data.get(ATTR_TARGET_TEMP)

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        device_data = self.coordinator.data[self._serial_number]
        return device_data.get(ATTR_MAX_TEMP, 115)

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        return 60  # Reasonable minimum for shower

    @property
    def target_temperature_step(self) -> float:
        """Return the supported step of target temperature."""
        return 1.0

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new HVAC mode."""
        if hvac_mode == HVACMode.HEAT:
            await self._api.set_shower_mode(self._serial_number, "on")
        elif hvac_mode == HVACMode.OFF:
            await self._api.set_shower_mode(self._serial_number, MODE_OFF)
        # State will update via Pusher client-state-reported event

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return

        await self._api.set_target_temperature(self._serial_number, temperature)
        # State will update via Pusher client-state-reported event

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        device_data = self.coordinator.data[self._serial_number]
        return {
            "serial_number": self._serial_number,
            "mode": device_data.get(ATTR_MODE),
            "active_preset": device_data.get("active_preset"),
            "firmware_version": device_data.get("current_firmware_version"),
        }
