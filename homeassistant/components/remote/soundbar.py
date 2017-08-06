"""
Support for Samsung HW-H550 device.

"""
import logging

import homeassistant.components.remote as remote
from subprocess import call
from time import sleep

_LOGGER = logging.getLogger(__name__)

INPUT_STATES = ['DIGITAL', 'AUX', 'HDMI', 'BT', 'TV', 'USB']
SOUND_STATES = ['MUSIC', 'VOICE', 'SPORTS', 'CINEMA', 'STANDARD']
SUPPORTED_COMMANDS = ['VOL_UP', 'VOL_DOWN']

def setup_platform(hass, config, add_devices_callback, discovery_info=None):
    add_devices_callback([SoundbarRemote("remote1", None, None, False)])


class SoundbarRemote(remote.RemoteDevice):
    """Remote representation used to control a Harmony device."""

    def __init__(self, name, input_state, sound_state, mute_state):
        _LOGGER.debug("Soundbar device init started for: %s", name)
        self._name = name
        self._input_state = input_state or INPUT_STATES[0]
        self._sound_state = sound_state or SOUND_STATES[0]
        self._mute_state = mute_state or False

    @property
    def name(self):
        return self._name

    @property
    def device_state_attributes(self):
        return {'input_state': self._input_state,
                'sound_state': self._sound_state,
                'mute_state': self._mute_state}

    @property
    def is_on(self):
        return True

    @property
    def should_poll(self):
        return True

    def irsend(self, key):
        call(["irsend", "SEND_ONCE", "soundbar", key])

    def turn_on(self, **kwargs):
        _LOGGER.error("Turning on")
        self.irsend("POWER")

    def turn_off(self, **kwargs):
        _LOGGER.error("Turning off")
        self.irsend("POWER")

    def send_command(self, command, **kwargs):
        _LOGGER.debug("command '%s'", command)
        for com in command:
            if com.startswith("INPUT"):
                cur_index = INPUT_STATES.index(self._input_state)
                target_index = INPUT_STATES.index(com[6:])
                if cur_index == target_index:
                    _LOGGER.debug("No action needed")
                    return
                elif cur_index < target_index:
                    diff = target_index - cur_index + 1
                else:
                    diff = len(INPUT_STATES) - cur_index + target_index + 1
                for i in range(0, diff):
                    self.irsend("SOURCE")
                    self._input_state = INPUT_STATES[(cur_index + i) % len(INPUT_STATES)]
                    _LOGGER.debug("Current input state %s", self._input_state)
                    sleep(1.5)
            elif com.startswith("SOUND"):
                cur_index = SOUND_STATES.index(self._input_state)
                target_index = SOUND_STATES.index(com[6:])
                if cur_index == target_index:
                    _LOGGER.debug("No action needed")
                    return
                elif cur_index < target_index:
                    diff = target_index - cur_index + 1
                else:
                    diff = len(SOUND_STATES) - cur_index + target_index + 1
                for i in range(0, diff):
                    self.irsend("SOUND")
                    self._sound_state = SOUND_STATES[(cur_index + i) % len(SOUND_STATES)]
                    _LOGGER.debug("Current sound state %s", self._input_state)
                    sleep(1.5)
            elif com == "MUTE":
                self.irsend("MUTE")
                self._mute_state = not self._mute_state
            elif com == "FORCE_MUTE":
                if not self._mute_state:
                    self.irsend("MUTE")
                    self._mute_state = not self._mute_state
                else:
                    _LOGGER.debug("No action needed")
            elif com == "FORCE_UNMUTE":
                if self._mute_state:
                    self.irsend("MUTE")
                    self._mute_state = not self._mute_state
                else:
                    _LOGGER.debug("No action needed")
            elif com in SUPPORTED_COMMANDS:
                self.irsend(com)
            else:
                _LOGGER.error("Not supported command '%s'", com)
