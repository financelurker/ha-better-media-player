"""The panel's live state (admins only).

room/live      subscribe: what one room does (room.live.room_live), sent on every
               change of the room (at most twice a second) and when it is set up
               anew (a reload); {"loaded": false} while it is not loaded
rooms/live     subscribe: one line per room (room.live.room_line), the same way
room/act       keep_on / release / all_off on a room (its switch and button)
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .. import clock
from ..const import DOMAIN
from ..room import get_rooms
from ..room.live import room_line, room_live

WS_ROOM_LIVE = f"{DOMAIN}/room/live"
WS_ROOMS_LIVE = f"{DOMAIN}/rooms/live"
WS_ROOM_ACT = f"{DOMAIN}/room/act"

MIN_GAP = 0.5  # s between two messages of a subscription
CHECK = timedelta(seconds=2)  # a reloaded room is picked up, waits that count down are sent


@callback
def async_setup_live(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_room_live)
    websocket_api.async_register_command(hass, ws_rooms_live)
    websocket_api.async_register_command(hass, ws_room_act)


class _Subscription:
    """Sends what build() returns whenever it changed: on the rooms' changes and
    every few seconds (a room set up anew has a new controller)."""

    def __init__(self, hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg_id: int,
                 build: Callable[[], dict], entry_ids: Callable[[], list[str]]) -> None:
        self.hass, self.connection, self.msg_id = hass, connection, msg_id
        self.build, self.entry_ids = build, entry_ids
        self._last: dict | None = None
        self._sent_at = 0.0
        self._later: CALLBACK_TYPE | None = None
        self._watched: dict[str, tuple[Any, Callable[[], None]]] = {}
        self._closed = False
        self._timer = async_track_time_interval(hass, self._check, CHECK)

    @callback
    def _check(self, _now: Any = None) -> None:
        rooms = get_rooms(self.hass)
        wanted = {eid: rooms[eid] for eid in self.entry_ids() if eid in rooms}
        for eid, (ctrl, unsub) in list(self._watched.items()):
            if wanted.get(eid) is not ctrl:
                unsub()
                del self._watched[eid]
        for eid, ctrl in wanted.items():
            if eid not in self._watched:
                self._watched[eid] = (ctrl, ctrl.async_add_listener(self._changed))
        self.send()

    @callback
    def _changed(self) -> None:
        if self._later is not None or self._closed:
            return
        wait = MIN_GAP - (clock.now() - self._sent_at)
        if wait <= 0:
            self.send()
        else:
            self._later = async_call_later(self.hass, wait, self._send_later)

    @callback
    def _send_later(self, _now: Any) -> None:
        self._later = None
        self.send()

    @callback
    def send(self) -> None:
        if self._closed:
            return
        data = self.build()
        if _without_now(data) == self._last:
            return
        self._last = _without_now(data)
        self._sent_at = clock.now()
        self.connection.send_message(websocket_api.event_message(self.msg_id, data))

    @callback
    def close(self) -> None:
        self._closed = True
        self._timer()
        if self._later is not None:
            self._later()
        for _ctrl, unsub in self._watched.values():
            unsub()
        self._watched.clear()


def _without_now(data: dict) -> dict:
    """(to tell a change: the time of the message is none)"""
    return {k: _without_now(v) if isinstance(v, dict) else v for k, v in data.items() if k != "now"}


def _start(hass, connection, msg, build, entry_ids) -> None:
    sub = _Subscription(hass, connection, msg["id"], build, entry_ids)
    connection.subscriptions[msg["id"]] = sub.close
    connection.send_result(msg["id"])
    sub._check()


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_ROOM_LIVE, vol.Required("entry_id"): str})
@callback
def ws_room_live(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    entry_id = msg["entry_id"]

    def build() -> dict:
        ctrl = get_rooms(hass).get(entry_id)
        return {"loaded": False} if ctrl is None else {"loaded": True, **room_live(ctrl)}

    _start(hass, connection, msg, build, lambda: [entry_id])


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): WS_ROOMS_LIVE})
@callback
def ws_rooms_live(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    def build() -> dict:
        return {"rooms": {eid: room_line(ctrl) for eid, ctrl in get_rooms(hass).items()}, "now": clock.now()}

    _start(hass, connection, msg, build, lambda: list(get_rooms(hass)))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_ROOM_ACT,
        vol.Required("entry_id"): str,
        vol.Required("action"): vol.In(["keep_on", "release", "all_off"]),
    }
)
@websocket_api.async_response
async def ws_room_act(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    ctrl = get_rooms(hass).get(msg["entry_id"])
    if ctrl is None:
        connection.send_error(msg["id"], "not_loaded", "The room is not loaded")
        return
    if msg["action"] == "all_off":
        await ctrl.async_turn_off(all_off=True)
    else:
        await ctrl.async_set_keep_on(msg["action"] == "keep_on")
    connection.send_result(msg["id"])
