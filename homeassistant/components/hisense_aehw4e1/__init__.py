"""The Hisense AEHW4E1 integration."""
from aiohttp import ClientSession
import asyncio
import logging

from aircon.app_mappings import SECRET_MAP
from aircon.ayla_api import get_ayla_api
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

from .const import (
    APP_NAME_TO_CODE,
    DOMAIN,
    CONF_APPCODE,
    CONF_APPNAME,
)

_LOGGER = logging.getLogger(__name__)

BASE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_PORT, default=8889): cv.port,
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Any(
            BASE_SCHEMA.extend(
                {vol.Required(CONF_APPNAME): vol.In(APP_NAME_TO_CODE.keys())}
            ),
            BASE_SCHEMA.extend(
                {vol.Required(CONF_APPCODE): vol.In(APP_NAME_TO_CODE.values())}
            ),
        ),
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
    # normalize config
    app_name = None
    if CONF_APPCODE in conf.keys():
        app_code = conf[CONF_APPCODE]
        for key, value in APP_NAME_TO_CODE.items():
            if value == app_code:
                app_name = key
                break
    else:
        app_name = conf[CONF_APPNAME]
    if app_name is None:
        raise ValueError("Incorrect config")

    hass.async_add_job(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_IMPORT},
            data={
                CONF_APPNAME: app_name,
                CONF_USERNAME: conf[CONF_USERNAME],
                CONF_PASSWORD: conf[CONF_PASSWORD],
                CONF_PORT: conf[CONF_PORT],
            },
        )
    )
    return True


def _setup_notifier(
    hass: HomeAssistant, session: ClientSession, entry: ConfigEntry
) -> Notifier:
    configured_port = entry.data[CONF_PORT]
    notifier = Notifier(configured_port)

    async def stop_notifier(event):
        await notifier.stop()

    hass.loop.create_task(notifier.start(session))

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_notifier)

    return notifier


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Hisense AEHW4E1 from a config entry."""
    session = async_get_clientsession(hass)

    ayla_api = get_ayla_api(
        entry.data[CONF_APPNAME],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        session,
    )

    notifier = _setup_notifier(hass, session, entry)

    hass.data[DOMAIN][entry.entry_id] = {"api": ayla_api, "notifier": notifier}

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
        ayla_api = hass.data[DOMAIN][entry.entry_id]["api"]
        await ayla_api.async_sign_out()

        notifier = hass.data[DOMAIN][entry.entry_id]["notifier"]
        await notifier.stop()
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_remove_entry(hass, entry) -> None:
    """Handle removal of an entry."""
    entry_data = hass.data[DOMAIN].get(entry.entry_id)
    if entry_data:
        ayla_api = entry_data.get("api")
        if ayla_api:
            await ayla_api.async_sign_out()

        notifier = entry_data.get("notifier")
        if notifier:
            await notifier.stop()
