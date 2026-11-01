"""Constants of the device layer."""

from __future__ import annotations

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

DATA_KEY = "better_media_player_devices"  # hass.data key of the device control

# Domains that can be switched as power devices
SWITCH_DOMAINS = ["switch", "light", "input_boolean"]
DEVICE_DOMAINS = ["media_player", "remote"]  # devices that turn themselves on/off

ACTION_ID = "better_media_player_action"  # virtual entity domain of a node's own on/off actions

COMMAND_RETRY_SECONDS = 30
DEVICE_OFF_SECONDS = 60  # a node's plugs wait at most this long for its devices to go off
OWN_CHANGE_SECONDS = 30
STANDBY_HOLD_SECONDS = 30  # below the standby power this long: in standby

POWERED_STATES = {
    MediaPlayerState.ON,
    MediaPlayerState.IDLE,
    MediaPlayerState.PLAYING,
    MediaPlayerState.PAUSED,
    MediaPlayerState.BUFFERING,
}
NOT_AVAILABLE = {STATE_UNAVAILABLE, STATE_UNKNOWN}
