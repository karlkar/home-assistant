"""Config flow for Hisense AEHW4E1 integration."""
import logging

from aircon.app_mappings import SECRET_MAP
from aircon.discovery import perform_discovery
from aircon.error import AuthFailed, Error, NoDevicesConfigured

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from .const import CONF_APPNAME, DOMAIN  # pylint:disable=unused-import

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_APPNAME): vol.In(SECRET_MAP.keys()),
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_PORT, default=8889): int,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hisense AEHW4E1."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    def _handle_successful_sign_in(self, user_input: dict):
        return self.async_create_entry(title="Ayla Cloud Credentials", data=user_input)

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass, verify_ssl=False)
            try:
                await perform_discovery(
                    session,
                    user_input[CONF_APPNAME],
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
                return self._handle_successful_sign_in(user_input)
            except NoDevicesConfigured:
                # Ignore this exception, as it doesn't matter now
                # TODO: Setup another step with explanation to user that he should setup devices first
                return self._handle_successful_sign_in(user_input)
            except AuthFailed:
                errors["base"] = "invalid_auth"
            except Error:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )
