"""Websocket commands of the panel (admins only)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from ..const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_HUB
from ..follow import get_hub
from ..room import get_rooms
from .view import central_view, room_view, routes_of

WS_CONFIG_GET = f"{DOMAIN}/config/get"
WS_ROUTE = f"{DOMAIN}/route"
WS_LAYOUT_SAVE = f"{DOMAIN}/layout/save"

LAYOUT_KEY = "layout"  # in the room's store: {device or source id: [x, y]}




@callback
def async_setup_ws(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_config_get)
    websocket_api.async_register_command(hass, ws_route)
    websocket_api.async_register_command(hass, ws_layout_save)


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
    connection.send_result(msg["id"], {"central": central, "rooms": rooms})


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
