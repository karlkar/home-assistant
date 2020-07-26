"""The Hisense AEHW4E1 integration."""
import logging

import asyncio

from aircon.app_mappings import SECRET_MAP
from aircon.notifier import Notifier

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_PORT,
    EVENT_HOMEASSISTANT_START,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, CONF_APPNAME

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_APPNAME): vol.In(SECRET_MAP.keys()),
                vol.Required(CONF_USERNAME): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
                vol.Optional(CONF_PORT, default=8888): cv.port,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

PLATFORMS = ["climate"]


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Hisense AEHW4E1 component."""
    hass.data[DOMAIN] = {}

    if DOMAIN not in config:
        return True

    conf = config.get(DOMAIN, [])
    hass.async_add_job(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_IMPORT},
            data={
                CONF_APPNAME: conf[CONF_APPNAME],
                CONF_USERNAME: conf[CONF_USERNAME],
                CONF_PASSWORD: conf[CONF_PASSWORD],
                CONF_PORT: conf[CONF_PORT],
            },
        )
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Hisense AEHW4E1 from a config entry."""
    # Store an API object for your platforms to access
    configured_port = entry.data[CONF_PORT]
    notifier = Notifier(configured_port)
    hass.data[DOMAIN][entry.entry_id] = notifier

    async def stop_notifier(event):
        await notifier.stop()

    session = async_get_clientsession(hass)
    hass.async_add_job(notifier.start(session))

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_notifier)

    for platform in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, platform)
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )
    if unload_ok:
        notifier = hass.data[DOMAIN][entry.entry_id]
        await notifier.stop()
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_remove_entry(hass, entry) -> None:
    """Handle removal of an entry."""
    notifier = hass.data[DOMAIN].get(entry.entry_id)
    if not notifier is None:
        await notifier.stop()
