"""Config flow for U by Moen integration."""
import logging
from typing import Any, Dict, Optional

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MoenApi, MoenAuthError, MoenApiError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class MoenConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for U by Moen."""

    VERSION = 1

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            # Validate the credentials
            session = async_get_clientsession(self.hass)
            api = MoenApi(
                user_input[CONF_EMAIL],
                user_input[CONF_PASSWORD],
                session,
            )

            try:
                # Try to authenticate
                await api.authenticate()

                # Get devices to verify connection works
                devices = await api.get_devices()

                # Create a unique ID based on the email
                await self.async_set_unique_id(user_input[CONF_EMAIL].lower())
                self._abort_if_unique_id_configured()

                # Create the entry
                return self.async_create_entry(
                    title=f"U by Moen ({user_input[CONF_EMAIL]})",
                    data=user_input,
                )

            except MoenAuthError:
                errors["base"] = "invalid_auth"
            except MoenApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during setup")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
