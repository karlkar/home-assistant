""" Support for ONVIF Cameras with FFmpeg as decoder.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/camera.onvif/
"""
import asyncio
import logging
import os

import voluptuous as vol

from homeassistant.const import (
    CONF_NAME, CONF_HOST, CONF_USERNAME, CONF_PASSWORD, CONF_PORT,
    CONF_STREAM_AUTH, ATTR_ENTITY_ID)
from homeassistant.components.camera import Camera, PLATFORM_SCHEMA, DOMAIN
from homeassistant.components.ffmpeg import (
    DATA_FFMPEG, CONF_EXTRA_ARGUMENTS)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.aiohttp_client import (
    async_aiohttp_proxy_stream)
from homeassistant.helpers.service import extract_entity_ids

_LOGGER = logging.getLogger(__name__)

REQUIREMENTS = ['onvif-py3==0.1.3',
                'suds-py3==1.3.3.0',
                'http://github.com/tgaugry/suds-passworddigest-py3'
                '/archive/86fc50e39b4d2b8997481967d6a7fe1c57118999.zip'
                '#suds-passworddigest-py3==0.1.2a']
DEPENDENCIES = ['ffmpeg']
DEFAULT_NAME = 'ONVIF Camera'
DEFAULT_PORT = 5000
DEFAULT_USERNAME = 'admin'
DEFAULT_PASSWORD = '888888'
DEFAULT_ARGUMENTS = '-q:v 2'
DEFAULT_PROFILE = 0

CONF_PROFILE = "profile"

ATTR_PAN = "pan"
ATTR_TILT = "tilt"
ATTR_ZOOM = "zoom"

DIR_UP = "UP"
DIR_DOWN = "DOWN"
DIR_LEFT = "LEFT"
DIR_RIGHT = "RIGHT"
ZOOM_OUT = "ZOOM_OUT"
ZOOM_IN = "ZOOM_IN"

SERVICE_PTZ = "ptz"

ONVIF_DATA = "onvif"
ENTITIES = "entities"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): cv.string,
    vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): cv.string,
    vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
    vol.Optional(CONF_EXTRA_ARGUMENTS, default=DEFAULT_ARGUMENTS): cv.string,
    vol.Optional(CONF_STREAM_AUTH, default=False): cv.boolean,
    vol.Optional(CONF_PROFILE, default=DEFAULT_PROFILE):
        vol.All(vol.Coerce(int), vol.Range(min=0)),
})

SERVICE_PTZ_SCHEMA = vol.Schema({
    ATTR_ENTITY_ID: cv.entity_ids,
    ATTR_PAN: vol.In([DIR_LEFT, DIR_RIGHT]),
    ATTR_TILT: vol.In([DIR_UP, DIR_DOWN]),
    ATTR_ZOOM: vol.In([ZOOM_OUT, ZOOM_IN])
})


@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up a ONVIF camera."""
    if not hass.data[DATA_FFMPEG].async_run_test(config.get(CONF_HOST)):
        return

    def handle_ptz(service):
        """Handle PTZ service call."""
        tilt = service.data.get(ATTR_TILT, None)
        pan = service.data.get(ATTR_PAN, None)
        zoom = service.data.get(ATTR_ZOOM, None)
        all_cameras = hass.data[ONVIF_DATA][ENTITIES]
        entity_ids = extract_entity_ids(hass, service)
        target_cameras = []
        if not entity_ids:
            target_cameras = all_cameras
        else:
            target_cameras = [camera for camera in all_cameras
                              if camera.entity_id in entity_ids]
        req = None
        for camera in target_cameras:
            if not camera._ptz:
                continue
            if not req:
                req = camera._ptz.create_type('ContinuousMove')
                if tilt == DIR_UP:
                    req.Velocity.PanTilt._y = 1
                elif tilt == DIR_DOWN:
                    req.Velocity.PanTilt._y = -1
                if pan == DIR_LEFT:
                    req.Velocity.PanTilt._x = -1
                elif pan == DIR_RIGHT:
                    req.Velocity.PanTilt._x = 1
                if zoom == ZOOM_IN:
                    req.Velocity.Zoom._x = 1
                elif zoom == ZOOM_OUT:
                    req.Velocity.Zoom._x = -1
            camera._ptz.ContinuousMove(req)

    async_add_devices([ONVIFCamera(hass, config)])
    hass.services.async_register(DOMAIN, SERVICE_PTZ, handle_ptz,
                                 schema=SERVICE_PTZ_SCHEMA)


class ONVIFCamera(Camera):
    """An implementation of an ONVIF camera."""

    def __init__(self, hass, config):
        """Initialize a ONVIF camera."""
        from onvif import ONVIFService
        import onvif
        super().__init__()

        self._name = config.get(CONF_NAME)
        self._stream_auth = config.get(CONF_STREAM_AUTH)
        self._username = config.get(CONF_USERNAME)
        self._password = config.get(CONF_PASSWORD)
        self._ffmpeg_arguments = config.get(CONF_EXTRA_ARGUMENTS)

        self._ptz = ONVIFService(
            'http://{}:{}/onvif/device_service'.format(
                config.get(CONF_HOST), config.get(CONF_PORT)),
            self._username,
            self._password,
            '{}/wsdl/ptz.wsdl'.format(os.path.dirname(onvif.__file__))
        )

        self._media = ONVIFService(
            'http://{}:{}/onvif/device_service'.format(
                config.get(CONF_HOST), config.get(CONF_PORT)),
            self._username,
            self._password,
            '{}/wsdl/media.wsdl'.format(os.path.dirname(onvif.__file__))
        )
        self._config_profile_index = config.get(CONF_PROFILE)

        self._profile_index = None
        self._input = None
        self._profiles = None
        self.configure_input()

    def get_profiles(self):
        """Function gets the profiles available"""
        return self._media.GetProfiles()

    def calculate_profile_index(self):
        """Function gets the configured profile index"""
        if self._config_profile_index >= len(self._profiles):
            _LOGGER.warning("ONVIF Camera '%s' doesn't provide profile %d."
                            " Using the last available profile.",
                            self._name, self._config_profile_index)
            return -1
        return self._config_profile_index

    def get_stream_uri(self):
        """Function gets the stream uri from media service"""
        req = self._media.create_type('GetStreamUri')
        req.ProfileToken = self._profiles[
            self._profile_index].__dict__['_token']
        return self._media.GetStreamUri(req).Uri

    @asyncio.coroutine
    def async_added_to_hass(self):
        """Callback when entity is added to hass."""
        if ONVIF_DATA not in self.hass.data:
            self.hass.data[ONVIF_DATA] = {}
            self.hass.data[ONVIF_DATA][ENTITIES] = []
        self.hass.data[ONVIF_DATA][ENTITIES].append(self)

    @asyncio.coroutine
    def async_camera_image(self):
        """Return a still image response from the camera."""
        from haffmpeg import ImageFrame, IMAGE_JPEG

        if not self.configure_input():
            return None

        ffmpeg = ImageFrame(
            self.hass.data[DATA_FFMPEG].binary, loop=self.hass.loop)

        image = yield from asyncio.shield(ffmpeg.get_image(
            self._input, output_format=IMAGE_JPEG,
            extra_cmd=self._ffmpeg_arguments), loop=self.hass.loop)
        return image

    @asyncio.coroutine
    def handle_async_mjpeg_stream(self, request):
        """Generate an HTTP MJPEG stream from the camera."""
        from haffmpeg import CameraMjpeg

        if not self.configure_input():
            return

        stream = CameraMjpeg(self.hass.data[DATA_FFMPEG].binary,
                             loop=self.hass.loop)
        yield from stream.open_camera(
            self._input, extra_cmd=self._ffmpeg_arguments)

        yield from async_aiohttp_proxy_stream(
            self.hass, request, stream,
            'multipart/x-mixed-replace;boundary=ffserver')
        yield from stream.close()

    @property
    def name(self):
        """Return the name of this camera."""
        return self._name

    @property
    def device_state_attributes(self):
        """Return the state attributes."""
        attrs = super().device_state_attributes
        _LOGGER.debug("I am called now - device_state_attributes")
        if self._profiles is None:
            return attrs
        custom_attrs = {}
        i = 0
        _LOGGER.debug("Wooo device_state_attributes profiles available!")
        for profile in self._profiles:
            bounds = profile.VideoSourceConfiguration.Bounds.__dict__
            custom_attrs.update({"Profile #" + str(i):
                                 str(bounds['_width'])
                                 + " x "
                                 + str(bounds['_height'])})
            i = i + 1
        custom_attrs.update({"Active profile": str(self._profile_index)})
        if attrs:
            attrs.update(custom_attrs)
        else:
            attrs = custom_attrs
        return attrs

    def configure_input(self):
        """Configures input for displaying image/video"""
        import onvif
        if self._profiles is None:
            try:
                self._profiles = self.get_profiles()
            except onvif.exceptions.ONVIFError:
                self._profiles = None
                _LOGGER.warning("Couldn't obtain profiles of camera '%s'",
                                self._name)
                return False

        if self._profile_index is None:
            self._profile_index = self.calculate_profile_index()

        if self._input is None:
            try:
                self._input = self.get_stream_uri()
                if self._stream_auth:
                    self._input = self._input.replace(
                        'rtsp://', 'rtsp://{}:{}@'.format(self._username,
                                                          self._password), 1)

                _LOGGER.debug(
                    "ONVIF Camera Using the following URL for %s: %s",
                    self._name, self._input)
            except onvif.exceptions.ONVIFError:
                self._input = None
                _LOGGER.warning("Camera '%s' not currently available.",
                                self._name)
                return False

        return True
