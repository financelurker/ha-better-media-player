"""Websocket commands of the panel (admins only)."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from homeassistant.helpers import device_registry as dr

from ..const import CENTRAL_SETTINGS, CONF_ENTRY_TYPE, CONF_FOLLOW_PLAYER, DOMAIN, ENTRY_TYPE_HUB
from ..follow import get_hub
from ..model import KINDS, ROOM_SETTINGS, SOURCE_SETTINGS, SPECS, check_value, device_defaults, device_setting, source_setting
from ..players.music_assistant import MusicAssistant
from ..room import get_rooms
from ..room.controller import RoomController
from .edit import SETTINGS_SCHEMA, check_room, clean_setup, has_errors
from .view import central_view, player_suggestions, room_view, routes_of, setup_version

WS_CONFIG_GET = f"{DOMAIN}/config/get"
WS_ROUTE = f"{DOMAIN}/route"
WS_LAYOUT_SAVE = f"{DOMAIN}/layout/save"
WS_VALIDATE = f"{DOMAIN}/validate"
WS_ROOM_SAVE = f"{DOMAIN}/room/save"
WS_ROOM_CREATE = f"{DOMAIN}/room/create"
WS_ROOM_DELETE = f"{DOMAIN}/room/delete"
WS_CENTRAL_SAVE = f"{DOMAIN}/central/save"

LAYOUT_KEY = "layout"  # in the room's store: {device or source id: [x, y]}



@callback
def async_setup_ws(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_config_get)
    websocket_api.async_register_command(hass, ws_route)
    websocket_api.async_register_command(hass, ws_layout_save)
    websocket_api.async_register_command(hass, ws_validate)
    websocket_api.async_register_command(hass, ws_room_save)
    websocket_api.async_register_command(hass, ws_room_create)
    websocket_api.async_register_command(hass, ws_room_delete)
    websocket_api.async_register_command(hass, ws_central_save)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_CONFIG_GET})
@callback
def ws_config_get(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Everything the panel shows: the central entry and the rooms."""
    central = None
    rooms = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB:
            hub = get_hub(hass)
            central = central_view(hass, entry, hub.data if hub is not None else None)
        else:
            room = get_rooms(hass).get(entry.entry_id)
            data = room.data if room is not None else None
            rooms.append(room_view(hass, entry, data, (data or {}).get(LAYOUT_KEY)))
    rooms.sort(key=lambda r: r["name"].casefold())
    defaults = {"room": dict(ROOM_SETTINGS), "devices": {k: device_defaults(k) for k in KINDS}, "sources": dict(SOURCE_SETTINGS)}
    connection.send_result(
        msg["id"],
        {"central": central, "rooms": rooms, "specs": _specs(), "defaults": defaults, "suggested": player_suggestions(hass)},
    )


def _specs() -> dict[str, dict]:
    """What each setting takes (the panel's fields): a range, choices, or on/off."""
    out: dict[str, dict] = {}
    for key, spec in SPECS.items():
        if spec is bool:
            out[key] = {"type": "bool"}
        elif isinstance(spec, list):
            out[key] = {"type": "choice", "choices": list(spec)}
        else:
            out[key] = {"type": "number", "min": spec[0], "max": spec[1]}
    return out


_SOURCE = vol.Schema(
    {vol.Required("id"): str, vol.Required("needs"): [vol.In(["picture", "sound"])]}, extra=vol.ALLOW_EXTRA
)
_DEVICE = vol.Schema({vol.Required("id"): str, vol.Required("kind"): str}, extra=vol.ALLOW_EXTRA)
_CABLE = vol.Schema(
    {
        vol.Required("from"): str,
        vol.Required("to"): str,
        vol.Required("signals"): [vol.In(["picture", "sound"])],
        vol.Optional("input"): vol.Any(None, str),
        vol.Optional("mode"): vol.In(["bmp", "cec", "never"]),
    },
    extra=vol.ALLOW_EXTRA,
)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_ROUTE,
        vol.Required("sources"): [_SOURCE],
        vol.Required("devices"): [_DEVICE],
        vol.Required("cables"): [_CABLE],
    }
)
@callback
def ws_route(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The routes of a room's draft (sources, devices, cables as config/get gives them)."""
    connection.send_result(msg["id"], {"routes": routes_of(msg["sources"], msg["devices"], msg["cables"])})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_LAYOUT_SAVE,
        vol.Required("entry_id"): str,
        vol.Required("layout"): {
            str: vol.All([vol.All(vol.Coerce(float), vol.Range(min=-1e6, max=1e6))], vol.Length(min=2, max=2))
        },
    }
)
@callback
def ws_layout_save(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Where the devices sit in the room's drawing (only for the eye: nothing reloads)."""
    room = get_rooms(hass).get(msg["entry_id"])
    if room is None:
        connection.send_error(msg["id"], "not_loaded", "The room is not loaded")
        return
    if msg["layout"]:
        room.data[LAYOUT_KEY] = {k: [round(v[0]), round(v[1])] for k, v in msg["layout"].items()}
    else:
        room.data.pop(LAYOUT_KEY, None)  # arranged automatically again
    room._save_now()
    connection.send_result(msg["id"])


# ---------------------------------------------------------------- editing

_DRAFT = {
    vol.Required("setup"): dict,
    vol.Optional("settings", default=dict): dict,
}


def _draft(hass: HomeAssistant, entry_id: str | None, msg: dict) -> tuple[dict, dict, list[dict]]:
    """The draft's setup and settings as stored, and its problems (vol.Invalid if malformed)."""
    setup = clean_setup(msg["setup"])
    settings = SETTINGS_SCHEMA(msg["settings"])
    return setup, settings, check_room(hass, entry_id, setup, settings)


def _invalid(connection: websocket_api.ActiveConnection, msg: dict, err: vol.Invalid) -> None:
    connection.send_error(msg["id"], "invalid_format", str(err))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_VALIDATE, vol.Optional("entry_id"): str, **_DRAFT})
@callback
def ws_validate(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The problems of a room's draft (errors stop saving, hints do not)."""
    try:
        _setup, _settings, problems = _draft(hass, msg.get("entry_id"), msg)
    except vol.Invalid as err:
        _invalid(connection, msg, err)
        return
    connection.send_result(msg["id"], {"problems": problems})


async def _apply_settings(room: RoomController, setup: dict, settings: dict) -> None:
    """The draft's settings, each only when changed (with its side effects); what
    belongs to devices or players no longer in the room goes."""
    for key, value in settings["room"].items():
        if (value := check_value(key, value)) != room.setting(key):
            await room.async_set_setting(key, value)
    devices = {d["id"]: d for d in setup["devices"]}
    for dev_id, values in settings["devices"].items():
        if (device := devices.get(dev_id)) is None:
            continue
        for key, value in values.items():
            if (value := check_value(key, value)) != device_setting(room.data, device, key):
                await room.async_set_device_setting(dev_id, key, value)
    hosted = {s["id"] for s in setup["sources"] if s["host"]}
    for src, values in settings["sources"].items():
        if src not in hosted:
            continue
        for key, value in values.items():
            if (value := check_value(key, value)) != source_setting(room.data, src, key):
                await room.async_set_source_setting(src, key, value)
    for group, keep in (("devices", set(devices)), ("sources", hosted)):
        for gone in set(room.data.get(group) or {}) - keep:
            room.data[group].pop(gone)


def _stored(entry) -> dict:
    """The room's setup as a draft of it would be stored (to see whether it changed)."""
    try:
        return clean_setup(dict(entry.options))
    except vol.Invalid:
        return dict(entry.options)


async def _reloaded(hass: HomeAssistant, entry_id: str, before: RoomController | None) -> None:
    """Wait (a while) until the room is set up again, so the panel reads it back loaded."""
    for _ in range(100):
        now = get_rooms(hass).get(entry_id)
        if now is not None and now is not before:
            return
        await asyncio.sleep(0.1)


def _set_area(hass: HomeAssistant, entry_id: str, area_id: str | None) -> None:
    """The room's area is its device's (as Home Assistant shows it)."""
    reg = dr.async_get(hass)
    if (device := reg.async_get_device(identifiers={(DOMAIN, entry_id)})) is not None and device.area_id != area_id:
        reg.async_update_device(device.id, area_id=area_id)


_ROOM_FIELDS = {vol.Required("name"): vol.All(str, vol.Length(min=1, max=60)), vol.Optional("area_id"): vol.Any(None, str)}


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): WS_ROOM_SAVE, vol.Required("entry_id"): str, vol.Required("version"): str, **_ROOM_FIELDS, **_DRAFT}
)
@websocket_api.async_response
async def ws_room_save(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Save a room: its settings at once, its setup (options: the room reloads) only when changed."""
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN or entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB:
        connection.send_error(msg["id"], "not_found", "No such room")
        return
    if msg["version"] != setup_version(entry):
        connection.send_error(msg["id"], "changed", "The room was changed meanwhile")
        return
    try:
        setup, settings, problems = _draft(hass, entry.entry_id, msg)
    except vol.Invalid as err:
        _invalid(connection, msg, err)
        return
    if has_errors(problems):
        connection.send_result(msg["id"], {"saved": False, "problems": problems})
        return
    # the area is the room's device's (set below); the one chosen at setup stays as it was
    setup.pop("area", None)
    if "area" in entry.options:
        setup["area"] = entry.options["area"]
    room = get_rooms(hass).get(entry.entry_id)
    if room is not None:
        await _apply_settings(room, setup, settings)
    reload = setup != _stored(entry) or msg["name"] != entry.title
    if room is not None and reload:
        await room.async_save_now()  # the reloaded room reads them back
    if "area_id" in msg:
        _set_area(hass, entry.entry_id, msg["area_id"])
    if reload:
        hass.config_entries.async_update_entry(entry, title=msg["name"], options=setup)
        if room is not None:
            await _reloaded(hass, entry.entry_id, room)
        elif entry.disabled_by is None:
            await hass.config_entries.async_reload(entry.entry_id)  # not loaded (e.g. its setup failed): try again
    connection.send_result(msg["id"], {"saved": True, "problems": problems, "version": setup_version(entry)})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_ROOM_CREATE, **_ROOM_FIELDS, **_DRAFT})
@websocket_api.async_response
async def ws_room_create(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """A new room from the panel's draft."""
    try:
        setup, settings, problems = _draft(hass, None, msg)
    except vol.Invalid as err:
        _invalid(connection, msg, err)
        return
    if has_errors(problems):
        connection.send_result(msg["id"], {"saved": False, "problems": problems})
        return
    if msg.get("area_id"):
        setup["area"] = msg["area_id"]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "panel"}, data={"title": msg["name"], "options": setup}
    )
    entry = result["result"]
    await _reloaded(hass, entry.entry_id, None)  # (its setup may still be running)
    if (room := get_rooms(hass).get(entry.entry_id)) is not None:
        await _apply_settings(room, setup, settings)
    _set_area(hass, entry.entry_id, msg.get("area_id"))
    connection.send_result(msg["id"], {"saved": True, "problems": problems, "entry_id": entry.entry_id})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_ROOM_DELETE, vol.Required("entry_id"): str})
@websocket_api.async_response
async def ws_room_delete(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Remove a room (its entry, entities and device)."""
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN or entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB:
        connection.send_error(msg["id"], "not_found", "No such room")
        return
    await hass.config_entries.async_remove(entry.entry_id)
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_CENTRAL_SAVE,
        vol.Optional("follow_player"): vol.Any(None, str),
        vol.Optional("settings", default=dict): {str: vol.Any(bool, int, float)},
    }
)
@websocket_api.async_response
async def ws_central_save(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The central entry: the follow-me player (options) and the central settings."""
    entry = next((e for e in hass.config_entries.async_entries(DOMAIN) if e.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB), None)
    hub = get_hub(hass)
    if entry is None or hub is None:
        connection.send_error(msg["id"], "no_central", "The central entry is not set up")
        return
    problems = []
    player = msg.get("follow_player") or None
    if player and hass.states.get(player) is not None and not MusicAssistant(hass).is_group(player):
        problems.append({"error": True, "code": "follow_not_group", "entity": player})
    for key, value in msg["settings"].items():
        if key not in CENTRAL_SETTINGS:
            problems.append({"error": True, "code": "unknown_setting", "setting": key})
            continue
        try:
            check_value(key, value)
        except ValueError as err:
            problems.append({"error": True, "code": "bad_setting", "setting": key, "expected": str(err)})
    if problems:
        connection.send_result(msg["id"], {"saved": False, "problems": problems})
        return
    for key, value in msg["settings"].items():
        if (value := check_value(key, value)) != hub.setting(key):
            await hub.async_set_setting(key, value)
    if "follow_player" in msg and player != entry.options.get(CONF_FOLLOW_PLAYER):
        hass.config_entries.async_update_entry(entry, options={CONF_FOLLOW_PLAYER: player} if player else {})
    connection.send_result(msg["id"], {"saved": True, "problems": []})
