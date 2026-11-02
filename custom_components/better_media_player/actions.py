"""The actions to set and read the settings of a room, of its devices, or the central ones.

better_media_player.set_setting   one setting; takes effect at once and is kept
better_media_player.get_settings  all of them, as the action's response

A room is named by its room player (entity_id). A device of the room by its id or
name (device); so is a player with a plug for its hardware (video / audio, or the
player's name: the settings of that plug). Without a room player the central
settings are meant.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.const import ATTR_FRIENDLY_NAME
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import CENTRAL_SETTINGS, DOMAIN
from .follow import get_hub
from .model import (
    DEVICE_SETTINGS,
    ROOM_SETTINGS,
    SOURCE_SETTINGS,
    check_value,
    device_setting,
    devices_of,
    source_setting,
    sources_of,
)
from .room import room_of
from .room.controller import RoomController

SERVICE_SET_SETTING = "set_setting"
SERVICE_GET_SETTINGS = "get_settings"

# the room player, also as a target (a list of one)
ROOM = vol.All(cv.ensure_list, vol.Length(min=1, max=1), [cv.entity_id], lambda ids: ids[0])
SET_SCHEMA = vol.Schema(
    {
        vol.Optional("entity_id"): ROOM,
        vol.Optional("device"): cv.string,
        vol.Required("setting"): cv.string,
        vol.Required("value"): vol.Any(None, bool, vol.Coerce(float), cv.string),
    }
)
GET_SCHEMA = vol.Schema({vol.Optional("entity_id"): ROOM, vol.Optional("device"): cv.string})


def _error(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders)


def _room(hass: HomeAssistant, entity_id: str) -> RoomController:
    if (room := room_of(hass, entity_id)) is None:
        raise _error("settings_not_a_room", entity=entity_id)
    return room


def _hosted(hass: HomeAssistant, room: RoomController) -> dict[str, str | None]:
    """The room's players with a plug for their hardware: source -> the player's name."""
    out = {}
    for key, src in sources_of(room.opts).items():
        if src.get("host"):
            state = hass.states.get(src["player"])
            out[key] = state.attributes.get(ATTR_FRIENDLY_NAME) if state else None
    return out


def _target(hass: HomeAssistant, room: RoomController, name: str) -> tuple[str, str]:
    """("device" | "source", its id): a device of the room, or a player with a hardware plug."""
    for d in devices_of(room.opts):
        if name in (d["id"], d.get("name")):
            return "device", d["id"]
    for key, player in _hosted(hass, room).items():
        if name in (key, player):
            return "source", key
    known = [d["id"] for d in devices_of(room.opts)] + list(_hosted(hass, room))
    raise _error("settings_no_device", device=name, room=room.entry.title, devices=", ".join(known))


def _value(key: str, value: Any) -> Any:
    try:
        return check_value(key, value)
    except ValueError as err:
        raise _error("settings_bad_value", setting=key, expected=str(err)) from err


def async_setup_actions(hass: HomeAssistant) -> None:
    async def _set(call: ServiceCall) -> None:
        key, value = call.data["setting"], call.data["value"]
        if (entity_id := call.data.get("entity_id")) is None:
            if "device" in call.data:
                raise _error("settings_needs_room", device=call.data["device"])
            if key not in CENTRAL_SETTINGS:
                raise _error("settings_unknown", setting=key, known=", ".join(CENTRAL_SETTINGS))
            if (hub := get_hub(hass)) is None:
                raise _error("settings_no_central")
            await hub.async_set_setting(key, _value(key, value))
            return
        room = _room(hass, entity_id)
        if (name := call.data.get("device")) is not None:
            what, target = _target(hass, room, name)
            keys = DEVICE_SETTINGS if what == "device" else SOURCE_SETTINGS
            if key not in keys:
                raise _error("settings_unknown", setting=key, known=", ".join(keys))
            if what == "device":
                await room.async_set_device_setting(target, key, _value(key, value))
            else:
                await room.async_set_source_setting(target, key, _value(key, value))
            return
        if key not in ROOM_SETTINGS:
            if key in DEVICE_SETTINGS:
                raise _error("settings_needs_device", setting=key)
            raise _error("settings_unknown", setting=key, known=", ".join(ROOM_SETTINGS))
        await room.async_set_setting(key, _value(key, value))

    async def _get(call: ServiceCall) -> ServiceResponse:
        if (entity_id := call.data.get("entity_id")) is None:
            if "device" in call.data:
                raise _error("settings_needs_room", device=call.data["device"])
            if (hub := get_hub(hass)) is None:
                raise _error("settings_no_central")
            return {"settings": {k: hub.setting(k) for k in CENTRAL_SETTINGS}}
        room = _room(hass, entity_id)
        devices, sources = devices_of(room.opts), list(_hosted(hass, room))
        if name := call.data.get("device"):
            what, target = _target(hass, room, name)
            devices = [d for d in devices if what == "device" and d["id"] == target]
            sources = [target] if what == "source" else []
        return {
            "settings": {k: room.setting(k) for k in ROOM_SETTINGS},
            "devices": {d["id"]: {k: device_setting(room.data, d, k) for k in DEVICE_SETTINGS} for d in devices},
            "sources": {s: {k: source_setting(room.data, s, k) for k in SOURCE_SETTINGS} for s in sources},
        }

    hass.services.async_register(DOMAIN, SERVICE_SET_SETTING, _set, SET_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_GET_SETTINGS, _get, GET_SCHEMA, supports_response=SupportsResponse.ONLY)
