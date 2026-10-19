"""Source changes of a room as an event (and device triggers).

The room's source is the source the room plays or is turned on with: video
while the video player is on, music while the music player plays or is
paused (on its own or in a group that counts for the room), or none.

A change is announced once it is stable for a moment (two players playing
at the same time for a moment fire nothing) and the room is ready for it:
after the power-on sequence, so e.g. the TV already has power and takes an
IR command. Not after a restart of Home Assistant with the room already on,
and not while a player is unavailable (a dropout is no change).

    better_media_player_source_changed:
        entity_id, entry_id, room, source (video / audio / None),
        previous, player, reason (selected / player / off)
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, EVENT_SOURCE_CHANGED, SOURCE_VIDEO

if TYPE_CHECKING:
    from .controller import RoomController

_LOGGER = logging.getLogger(__name__)

SETTLE_SECONDS = 2
REQUEST_SECONDS = 180  # a wish through the room player counts this long for the reason
_UNSET = object()

REASON_SELECTED = "selected"
REASON_PLAYER = "player"
REASON_OFF = "off"

_VIDEO_ON = {
    MediaPlayerState.ON,
    MediaPlayerState.IDLE,
    MediaPlayerState.PLAYING,
    MediaPlayerState.PAUSED,
    MediaPlayerState.BUFFERING,
}
_AUDIO_ON = {MediaPlayerState.PLAYING, MediaPlayerState.PAUSED, MediaPlayerState.BUFFERING}
_UNCERTAIN = {"unavailable", "unknown", None}


class SourceWatch:
    def __init__(self, ctrl: RoomController) -> None:
        self.ctrl = ctrl
        self._announced: Any = _UNSET  # the last announced source (None: off)
        self._pending: tuple[str | None, float] | None = None
        self._requested: tuple[str, float] | None = None
        self._unsub: CALLBACK_TYPE | None = None

    @callback
    def note_request(self, key: str) -> None:
        """The room was asked for this source (turn on, source, play, wake)."""
        self._requested = (key, time.monotonic())

    def _current(self) -> str | None:
        ctrl = self.ctrl
        if ctrl.sequence_running:
            return ctrl.last_source  # the source the room is turned on with
        on = []
        for src in ctrl.sources.values():
            state = ctrl.player_state(src)
            if src.suppressed:
                continue
            if src.key == SOURCE_VIDEO and state in _VIDEO_ON:
                on.append(src.key)
            elif src.key != SOURCE_VIDEO and state in _AUDIO_ON and ctrl._group_counts(src.player):
                on.append(src.key)
        if not on:
            return None
        if len(on) == 1:
            return on[0]
        active = ctrl.active_source
        return active.key if active is not None and active.key in on else on[0]

    @callback
    def async_check(self) -> None:
        """After each update of the room."""
        ctrl = self.ctrl
        if not ctrl._started or ctrl._stopped:
            return
        if any(ctrl.player_state(s) in _UNCERTAIN for s in ctrl.sources.values()):
            return  # a dropout (or not loaded yet) is no change
        current = self._current()
        if self._announced is _UNSET:
            self._announced = current  # Home Assistant started: the room is as it is
            return
        if current == self._announced:
            self._pending = None
            return
        now = time.monotonic()
        if self._pending is None or self._pending[0] != current:
            self._pending = (current, now)
        if now - self._pending[1] < SETTLE_SECONDS or ctrl.sequence_running:
            self._recheck()
            return
        self._fire(current)

    @callback
    def _recheck(self) -> None:
        if self._unsub is not None:
            return

        @callback
        def _due(_now) -> None:
            self._unsub = None
            self.async_check()

        self._unsub = async_call_later(self.ctrl.hass, SETTLE_SECONDS, _due)

    @callback
    def _fire(self, source: str | None) -> None:
        ctrl = self.ctrl
        previous = self._announced
        self._announced = source
        self._pending = None
        if source is None:
            reason = REASON_OFF
        elif (
            self._requested is not None
            and self._requested[0] == source
            and time.monotonic() - self._requested[1] < REQUEST_SECONDS
        ):
            reason = REASON_SELECTED
        else:
            reason = REASON_PLAYER
        self._requested = None
        entry = ctrl.entry
        entity_id = er.async_get(ctrl.hass).async_get_entity_id("media_player", DOMAIN, f"{entry.entry_id}_player")
        player = ctrl.sources[source].player if source is not None else None
        _LOGGER.info("%s: source %s -> %s (%s)", entry.title, previous, source, reason)
        ctrl.hass.bus.async_fire(
            EVENT_SOURCE_CHANGED,
            {
                "entity_id": entity_id,
                "entry_id": entry.entry_id,
                "room": entry.title,
                "source": source,
                "previous": previous,
                "player": player,
                "reason": reason,
            },
        )

    @callback
    def async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
