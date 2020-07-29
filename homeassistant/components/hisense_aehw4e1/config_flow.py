"""Config flow for Hisense AEHW4E1 integration."""
import logging

from aircon.discovery import perform_discovery
from aircon.error import AuthFailed, Error, NoDevicesConfigured

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

# pylint: disable=unused-import
from .const import (
    APP_NAME_TO_CODE,
    CONF_APPNAME,
    CONF_LOCAL_DEVICES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_APPNAME): vol.In(APP_NAME_TO_CODE.keys()),
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_LOCAL_DEVICES): bool,
        vol.Optional(CONF_PORT, default=8889): int,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hisense AEHW4E1."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    def _handle_successful_sign_in(self, user_input: dict):
        title = user_input[CONF_APPNAME] + " " + user_input[CONF_USERNAME]
        return self.async_create_entry(title=title, data=user_input)

    async def async_step_import(self, import_config):
        """Import a config entry from configuration.yaml."""
        entries = self._async_current_entries()
        if entries:
            for entry in entries:
                same_app_name = self._check_same_app_name(entry, import_config)
                if same_app_name:
                    return self.async_abort(reason="already_configured")

                if entry.data[CONF_PORT] == import_config[CONF_PORT]:
                    _LOGGER.warning(
                        "Port %d is already used by another entry",
                        import_config[CONF_PORT],
                    )
                    return self.async_abort(reason="port_is_used")

        return await self.async_step_user(import_config)

    @staticmethod
    def _check_same_app_name(entry, import_config) -> bool:
        """Checks if the app name is already configured"""
        entry_appname = entry.data[CONF_APPNAME]
        config_appname = import_config[CONF_APPNAME]
        if entry_appname == config_appname:
            if entry.data[CONF_USERNAME] == import_config[CONF_USERNAME]:
                _LOGGER.warning(
                    "Account %s for application %s is already configured.",
                    import_config[CONF_USERNAME],
                    entry_appname,
                )
                return True
        return False

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass, verify_ssl=False)

            app_code = APP_NAME_TO_CODE[user_input[CONF_APPNAME]]

            try:
                await perform_discovery(
                    session,
                    app_code,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
                return self._handle_successful_sign_in(user_input)
            except NoDevicesConfigured:
                errors["base"] = "no_devices_added"
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
