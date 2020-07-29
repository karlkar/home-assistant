"""Support for AC devices controlled by AEH-W4B1 and AEH-W4E1."""
from logging import getLogger

from aiohttp import web
from asyncio import get_event_loop
from functools import partial
import json
from typing import Callable, List, Optional
import os

from aircon.aircon import AcDevice, BaseDevice
from aircon.discovery import perform_discovery
from aircon.query_handlers import QueryHandlers
from aircon.properties import (
    AcWorkMode,
    AirFlow,
    AirFlowState,
    Economy,
    FanSpeed,
    FastColdHeat,
    Power,
    Quiet,
    TemperatureUnit,
)

from homeassistant import util
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    HVAC_MODE_AUTO,
    HVAC_MODE_COOL,
    HVAC_MODE_DRY,
    HVAC_MODE_FAN_ONLY,
    HVAC_MODE_HEAT,
    HVAC_MODE_OFF,
    PRESET_BOOST,
    PRESET_ECO,
    PRESET_SLEEP,
    PRESET_NONE,
    SUPPORT_PRESET_MODE,
    SUPPORT_TARGET_TEMPERATURE,
    SUPPORT_FAN_MODE,
    SUPPORT_SWING_MODE,
    FAN_LOW,
    FAN_MEDIUM,
    FAN_HIGH,
    FAN_AUTO,
    SWING_OFF,
    SWING_VERTICAL,
    SWING_HORIZONTAL,
    SWING_BOTH,
)
from homeassistant.components.http import HomeAssistantView, real_ip
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_PORT,
    EVENT_HOMEASSISTANT_STOP,
    PRECISION_WHOLE,
    TEMP_CELSIUS,
    TEMP_FAHRENHEIT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import Entity

from .const import CONF_APPNAME, CONF_LOCAL_DEVICES, DOMAIN

_LOGGER = getLogger(__name__)

SUPPORT_FLAGS = (
    SUPPORT_TARGET_TEMPERATURE
    | SUPPORT_FAN_MODE
    | SUPPORT_SWING_MODE
    | SUPPORT_PRESET_MODE
)

MIN_TEMP_C = 16
MAX_TEMP_C = 32

MIN_TEMP_F = 61
MAX_TEMP_F = 90

HVAC_MODES = [
    HVAC_MODE_OFF,
    HVAC_MODE_HEAT,
    HVAC_MODE_COOL,
    HVAC_MODE_DRY,
    HVAC_MODE_FAN_ONLY,
]

FAN_MODES = [
    FAN_LOW,
    FAN_MEDIUM,
    FAN_HIGH,
    FAN_AUTO,
]

SWING_MODES = [
    SWING_OFF,
    SWING_VERTICAL,
    SWING_HORIZONTAL,
    SWING_BOTH,
]

PRESET_MODES = [
    PRESET_NONE,
    PRESET_ECO,
    PRESET_BOOST,
    PRESET_SLEEP,
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: Callable[[List[Entity], bool], None],
):
    """Set up ACs based on a config entry."""
    session = async_get_clientsession(hass)
    conf = config_entry.data
    notifier = hass.data[DOMAIN][config_entry.entry_id]

    # TODO: Remove file on removal
    filepath = hass.config.path(".aehw4e1.json")
    if conf[CONF_LOCAL_DEVICES] and os.path.isfile(filepath):
        _LOGGER.debug("Reading devices from LOCAL source")
        with open(filepath, "rt", encoding="utf-8") as file_handle:
            discovery_result = json.load(file_handle)
    else:
        _LOGGER.debug("Reading devices from REMOTE source")
        discovery_result = await perform_discovery(
            session, conf[CONF_APPNAME], conf[CONF_USERNAME], conf[CONF_PASSWORD],
        )
        if conf[CONF_LOCAL_DEVICES]:
            with open(filepath, "w", encoding="utf-8") as file_handle:
                json.dump(
                    discovery_result,
                    file_handle,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )

    devices = []
    entities = []
    for attributes in discovery_result:
        name = attributes["product_name"]
        lan_ip = attributes["lan_ip"]
        lan_ip_key = attributes["lanip_key"]
        lan_ip_key_id = attributes["lanip_key_id"]
        device = AcDevice(
            name,
            lan_ip,
            lan_ip_key,
            lan_ip_key_id,
            partial(notifier.notify, get_event_loop()),
        )
        _LOGGER.debug("Adding device %s, ip = %s", name, lan_ip)

        notifier.register_device(device)
        devices.append(device)
        entity = ClimateAehW4e1(device, attributes["mac"])

        def property_changed(entity: Entity, dev_name: str, name: str, value):
            entity.schedule_update_ha_state()

        device.add_property_change_listener(partial(property_changed, entity))
        entities.append(entity)

    await _setup_hisense_server(hass, conf, devices)

    async_add_entities(entities)


# TODO Should be able to start server without any devices and add them in runtime
async def _setup_hisense_server(hass: HomeAssistant, conf: dict, devices: [BaseDevice]):
    query_handlers = QueryHandlers(devices)
    app = web.Application()
    app["hass"] = hass

    real_ip.setup_real_ip(app, False, [])

    # pylint: disable=protected-access
    app._on_startup.freeze()
    await app.startup()

    runner = None
    site = None

    KeyExchangeView(query_handlers).register(app, app.router)
    CommandsView(query_handlers).register(app, app.router)
    PropertyUpdateView(
        query_handlers,
        "/local_lan/property/datapoint.json",
        "local_lan:property:datapoint",
    ).register(app, app.router)
    PropertyUpdateView(
        query_handlers,
        "/local_lan/property/datapoint/ack.json",
        "local_lan:property:datapoint:ack",
    ).register(app, app.router)
    PropertyUpdateView(
        query_handlers,
        "/local_lan/node/property/datapoint.json'",
        "local_lan:node:property:datapoint",
    ).register(app, app.router)
    PropertyUpdateView(
        query_handlers,
        "/local_lan/node/property/datapoint/ack.json",
        "local_lan:node:property:datapoint:ack",
    ).register(app, app.router)

    async def stop_hisense_server(event):
        if site:
            await site.stop()
        if runner:
            await runner.cleanup()

    async def start_hisense_server():
        nonlocal site
        nonlocal runner

        runner = web.AppRunner(app)
        await runner.setup()

        host_ip_addr = util.get_local_ip()
        _LOGGER.debug(
            "Starting hisense web server on %s:%d", host_ip_addr, conf[CONF_PORT]
        )
        site = web.TCPSite(runner, host_ip_addr, conf[CONF_PORT])

        try:
            await site.start()
            _LOGGER.debug("Started")
        except OSError as error:
            _LOGGER.error(
                "Failed to create HTTP server at port %d: %s", conf[CONF_PORT], error
            )
        else:
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_hisense_server)

    _LOGGER.debug("event listener for server added")
    hass.loop.create_task(start_hisense_server())


class KeyExchangeView(HomeAssistantView):
    """View handling key exchange"""

    url = "/local_lan/key_exchange.json"
    name = "local_lan:key_exchange"
    requires_auth = False

    def __init__(self, query_handlers: QueryHandlers):
        self._query_handlers = query_handlers

    async def post(self, request: web.Request):
        """Method responsible for exchanging the encryption keys"""
        return await self._query_handlers.key_exchange_handler(request)


class CommandsView(HomeAssistantView):
    """View for handling incoming command requests"""

    url = "/local_lan/commands.json"
    name = "local_lan:commands"
    requires_auth = False

    def __init__(self, query_handlers: QueryHandlers):
        self._query_handlers = query_handlers

    async def get(self, request):
        """Method responsible for exchanging the encryption keys"""
        return await self._query_handlers.command_handler(request)


class PropertyUpdateView(HomeAssistantView):
    """View for handling incoming property updates"""

    requires_auth = False

    def __init__(self, query_handlers: QueryHandlers, url: str, name: str):
        self._query_handlers = query_handlers
        self.url = url
        self.name = name

    async def post(self, request: web.Request):
        """Method responsible for exchanging the encryption keys"""
        return await self._query_handlers.property_update_handler(request)


class ClimateAehW4e1(ClimateEntity):
    """Represents device to be controlled"""

    def __init__(self, device: AcDevice, mac: str):
        self._device = device
        self._mac = mac
        self._device.queue_status()
        super().__init__()

    @property
    def precision(self) -> float:
        """Return the precision of the system."""
        return PRECISION_WHOLE

    @property
    def temperature_unit(self) -> str:
        """Return the unit of measurement used by the platform."""
        temptype = self._device.get_temptype()
        if temptype == TemperatureUnit.CELSIUS:
            return TEMP_CELSIUS
        return TEMP_FAHRENHEIT

    @property
    def hvac_mode(self) -> str:
        """Return hvac operation ie. heat, cool mode.

        Need to be one of HVAC_MODE_*.
        """
        power = self._device.get_power()
        if power == Power.OFF:
            return HVAC_MODE_OFF

        work_mode = self._device.get_work_mode()
        if work_mode == AcWorkMode.FAN:
            return HVAC_MODE_FAN_ONLY
        if work_mode == AcWorkMode.HEAT:
            return HVAC_MODE_HEAT
        if work_mode == AcWorkMode.COOL:
            return HVAC_MODE_COOL
        if work_mode == AcWorkMode.DRY:
            return HVAC_MODE_DRY
        if work_mode == AcWorkMode.AUTO:
            return HVAC_MODE_AUTO

    @property
    def hvac_modes(self) -> List[str]:
        """Return the list of available hvac operation modes.

        Need to be a subset of HVAC_MODES.
        """
        return HVAC_MODES

    @property
    def current_temperature(self) -> Optional[float]:
        """Return the current temperature."""
        return float(self._device.get_env_temp())

    @property
    def target_temperature(self) -> Optional[float]:
        """Return the temperature we try to reach."""
        return float(self._device.get_temperature())

    @property
    def target_temperature_step(self) -> Optional[float]:
        """Return the supported step of target temperature."""
        return 1.0

    @property
    def preset_mode(self) -> Optional[str]:
        """Return the current preset mode, e.g., home, away, temp.

        Requires SUPPORT_PRESET_MODE.
        """
        eco = self._device.get_eco()
        if eco == Economy.ON:
            return PRESET_ECO

        fast_cold_heat = self._device.get_fast_heat_cold()
        if fast_cold_heat == FastColdHeat.ON:
            return PRESET_BOOST

        sleep = self._device.get_fan_mute()
        if sleep == Quiet.ON:
            return PRESET_SLEEP

        return PRESET_NONE

    @property
    def preset_modes(self) -> Optional[List[str]]:
        """Return a list of available preset modes.

        Requires SUPPORT_PRESET_MODE.
        """
        return PRESET_MODES

    @property
    def fan_mode(self) -> Optional[str]:
        """Return the fan setting.

        Requires SUPPORT_FAN_MODE.
        """
        fan_mode = self._device.get_fan_speed()
        if fan_mode == FanSpeed.AUTO:
            return FAN_AUTO
        if fan_mode == FanSpeed.LOWER:
            return FAN_LOW
        if fan_mode == FanSpeed.MEDIUM:
            return FAN_MEDIUM
        if fan_mode == FanSpeed.HIGHER:
            return FAN_HIGH

    @property
    def fan_modes(self) -> Optional[List[str]]:
        """Return the list of available fan modes.

        Requires SUPPORT_FAN_MODE.
        """
        return FAN_MODES

    @property
    def swing_mode(self) -> Optional[str]:
        """Return the swing setting.

        Requires SUPPORT_SWING_MODE.
        """
        fan_horizontal = self._device.get_fan_horizontal()
        fan_vertical = self._device.get_fan_speed()
        if fan_horizontal == AirFlow.ON and fan_vertical == AirFlow.ON:
            return SWING_BOTH
        if fan_horizontal == AirFlow.ON:
            return SWING_HORIZONTAL
        if fan_vertical == AirFlow.ON:
            return SWING_VERTICAL
        return SWING_OFF

    @property
    def swing_modes(self) -> Optional[List[str]]:
        """Return the list of available swing modes.

        Requires SUPPORT_SWING_MODE.
        """
        return SWING_MODES

    def set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""
        power = self._device.get_power()
        if power == Power.OFF:
            _LOGGER.warning(
                "Device %s is off, could not set the temperature", self._device.name
            )
            return
        temp = kwargs.get(ATTR_TEMPERATURE)
        self._device.set_temperature(temp)

    def set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""
        if fan_mode == FAN_AUTO:
            self._device.set_fan_speed(FanSpeed.AUTO)
        elif fan_mode == FAN_LOW:
            self._device.set_fan_speed(FanSpeed.LOWER)
        elif fan_mode == FAN_MEDIUM:
            self._device.set_fan_speed(FanSpeed.MEDIUM)
        elif fan_mode == FAN_HIGH:
            self._device.set_fan_speed(FanSpeed.HIGHER)

    def set_hvac_mode(self, hvac_mode: str) -> None:
        """Set new target hvac mode."""
        if hvac_mode == HVAC_MODE_OFF:
            self._device.set_power(Power.OFF)
        elif hvac_mode == HVAC_MODE_AUTO:
            self._device.set_work_mode(AcWorkMode.AUTO)
        elif hvac_mode == HVAC_MODE_COOL:
            self._device.set_work_mode(AcWorkMode.COOL)
        elif hvac_mode == HVAC_MODE_DRY:
            self._device.set_work_mode(AcWorkMode.DRY)
        elif hvac_mode == HVAC_MODE_FAN_ONLY:
            self._device.set_work_mode(AcWorkMode.FAN)
        elif hvac_mode == HVAC_MODE_HEAT:
            self._device.set_work_mode(AcWorkMode.HEAT)

    def set_swing_mode(self, swing_mode: str) -> None:
        """Set new target swing operation."""
        if swing_mode == SWING_OFF:
            self._device.set_swing(AirFlowState.OFF)
        elif swing_mode == SWING_HORIZONTAL:
            self._device.set_swing(AirFlowState.HORIZONTAL_ONLY)
        elif swing_mode == SWING_VERTICAL:
            self._device.set_swing(AirFlowState.VERTICAL_ONLY)
        elif swing_mode == SWING_BOTH:
            self._device.set_swing(AirFlowState.VERTICAL_AND_HORIZONTAL)

    def set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        if preset_mode == PRESET_ECO:
            self._device.set_eco(Economy.ON)
        elif preset_mode == PRESET_BOOST:
            self._device.set_fast_heat_cold(FastColdHeat.ON)
        elif preset_mode == PRESET_SLEEP:
            self._device.set_fan_mute(Quiet.ON)
        else:
            # TODO need to check if it doesn't explode
            self._device.set_eco(Economy.OFF)
            self._device.set_fast_heat_cold(FastColdHeat.OFF)
            self._device.set_fan_mute(Quiet.OFF)

    def turn_on(self):
        """Turn the device on"""
        self._device.set_power(Power.ON)

    async def async_turn_on(self) -> None:
        """Turn the entity on."""
        await self.hass.async_add_executor_job(self.turn_on)

    def turn_off(self):
        """Turn the device off"""
        self._device.set_power(Power.OFF)

    async def async_turn_off(self) -> None:
        """Turn the entity off."""
        await self.hass.async_add_executor_job(self.turn_off)

    @property
    def supported_features(self) -> int:
        """Return the list of supported features."""
        return SUPPORT_FLAGS

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        if self.temperature_unit == TEMP_CELSIUS:
            return MIN_TEMP_C
        return MIN_TEMP_F

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        if self.temperature_unit == TEMP_CELSIUS:
            return MAX_TEMP_C
        return MAX_TEMP_F

    @property
    def should_poll(self) -> bool:
        """Return True if entity has to be polled for state.

        False if entity pushes its state to HA.
        """
        return False

    @property
    def unique_id(self) -> Optional[str]:
        """Return a unique ID."""
        return self._mac

    @property
    def name(self) -> Optional[str]:
        """Return the name of the entity."""
        return self._device.name

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._device.available
