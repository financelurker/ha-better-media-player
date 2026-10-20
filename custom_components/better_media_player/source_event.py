"""Source changes of a room as an event (and device triggers).

The room's source is the source the room plays or is turned on with:
- video while the video player is on,
- music while the music player plays (on its own or in a group that counts
  for the room), while it is paused and the room still has power for it, or
  after the room was turned on with music,
- a source chosen through the room player (or a wake) stays the room's source
  until another source plays or the room goes off (e.g. during a snooze),
- none otherwise ("off").

A change is announced once it is stable for a moment (two players playing
at the same time for a moment fire nothing) and the room is ready for it:
after the power-on sequence, so e.g. the TV already has power and takes an
IR command. Not after a restart of Home Assistant with the room already on
(the first look waits until the players have reported), and not while the
current source's player is unavailable (a dropout is no change); another
unavailable player (e.g. behind its host plug) counts as off.

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
from homeassistant.helpers.event import async_call_later

from .const import EVENT_SOURCE_CHANGED, SOURCE_AUDIO, SOURCE_VIDEO

if TYPE_CHECKING:
    from .controller import RoomController

_LOGGER = logging.getLogger(__name__)

SETTLE_SECONDS = 2
REQUEST_SECONDS = 60  # a wish through the room player counts this long after its start
FIRST_LOOK_SECONDS = 60  # at most this long for the players to report after a start
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
_AUDIO_PLAYING = {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING}
_UNCERTAIN = {"unavailable", "unknown", None}
_NOT_LOADED = {"unknown", None}


class SourceWatch:
    def __init__(self, ctrl: RoomController) -> None:
        self.ctrl = ctrl
        self._announced: Any = _UNSET  # the last announced source (None: off)
        self._pending: tuple[str | None, float] | None = None
        self._requested: tuple[str, float] | None = None
        self._held: str | None = None  # turned on with this source (e.g. music, still idle)
        self._since = time.monotonic()
        self._unsub: CALLBACK_TYPE | None = None

    @callback
    def note_request(self, key: str) -> None:
        """The room was asked for this source (turn on, source, play, wake)."""
        self._requested = (key, time.monotonic())

    @property
    def ready(self) -> bool:
        """The first look after a start is done."""
        return self._announced is not _UNSET

    @property
    def source(self) -> str | None:
        """The announced source (None: off, or not looked yet)."""
        return None if self._announced is _UNSET else self._announced

    @property
    def changing(self) -> bool:
        """Another source is about to be announced (settling)."""
        return self._pending is not None

    @property
    def active(self) -> bool:
        """The room has a source: one plays, or the announced one (stable)."""
        return bool(self._playing()) or self._announced not in (None, _UNSET)

    def _playing(self) -> list[str]:
        ctrl = self.ctrl
        on = []
        for src in ctrl.sources.values():
            if src.suppressed:
                continue
            state = ctrl.player_state(src)
            if src.key == SOURCE_VIDEO and state in _VIDEO_ON:
                on.append(src.key)
            elif src.key == SOURCE_AUDIO and state in _AUDIO_PLAYING and ctrl._group_counts(src.player):
                on.append(src.key)
        return on

    def _usable(self, key: str) -> bool:
        """The room still holds this source (not off, not on its way off)."""
        ctrl = self.ctrl
        src = ctrl.sources.get(key)
        return (
            src is not None
            and not src.suppressed
            and not ctrl.turning_off
            and ctrl.player_state(src) not in (*_UNCERTAIN, "off", "standby")
            and (ctrl.room_on or not ctrl.roles)
        )

    def _current(self) -> str | None:
        ctrl = self.ctrl
        if ctrl.sequence_running:
            # nothing changes until the room is ready (e.g. the TV has power)
            if self._requested is not None:
                self._requested = (self._requested[0], time.monotonic())  # counts from the end of the start
            return self._announced
        if ctrl.error:
            self._requested = None  # the start failed: no wish left
        on = self._playing()
        if on:
            if self._held not in on:
                self._held = None  # another source took over
            if len(on) == 1:
                return on[0]
            active = ctrl.active_source
            return active.key if active is not None and active.key in on else on[0]
        if self._held is not None and not self._usable(self._held):
            self._held = None
        if self._held is not None:
            return self._held
        if self._fresh_request() is not None and self._usable(self._requested[0]):
            return self._requested[0]  # turned on with it (e.g. music, not playing yet)
        audio = ctrl.sources.get(SOURCE_AUDIO)
        if (
            audio is not None
            and ctrl.player_state(audio) == MediaPlayerState.PAUSED
            and self._announced in (SOURCE_AUDIO, _UNSET)  # (also at the first look after a start)
            and self._usable(SOURCE_AUDIO)
        ):
            return SOURCE_AUDIO  # paused: still the room's source while it has power for it
        return None

    def _fresh_request(self) -> str | None:
        req = self._requested
        if req is None or time.monotonic() - req[1] >= REQUEST_SECONDS:
            return None
        return req[0]

    @callback
    def async_check(self) -> None:
        """After each update of the room."""
        ctrl = self.ctrl
        if not ctrl._started or ctrl._stopped:
            return
        if self._announced is _UNSET:
            # Home Assistant started: the room is as it is, once its players have
            # reported (and no sequence is running)
            loading = any(ctrl.player_state(s) in _NOT_LOADED for s in ctrl.sources.values())
            if ctrl.sequence_running or (loading and time.monotonic() - self._since < FIRST_LOOK_SECONDS):
                self._recheck()
                return
            self._announced = self._current()
            ctrl._notify()  # controls wait for this first look
            return
        announced = ctrl.sources.get(self._announced) if self._announced else None
        if announced is not None and ctrl.player_state(announced) in _UNCERTAIN:
            return  # the current source drops out for a moment: no change
        current = self._current()
        if current == self._announced:
            self._pending = None
            if self._requested is not None and self._requested[0] == current and not ctrl.sequence_running:
                self._requested = None  # asked for what it is already
            return
        now = time.monotonic()
        if self._pending is None or self._pending[0] != current:
            self._pending = (current, now)
        if now - self._pending[1] < SETTLE_SECONDS:
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
        elif self._fresh_request() == source:
            reason = REASON_SELECTED
            self._held = source  # the room holds it until another source plays or it goes off
        else:
            reason = REASON_PLAYER
        self._requested = None
        player = ctrl.sources[source].player if source is not None else None
        _LOGGER.info("%s: source %s -> %s (%s)", ctrl.entry.title, previous, source, reason)
        ctrl._notify()  # controls that depend on it
        ctrl.async_fire(
            EVENT_SOURCE_CHANGED,
            {"source": source, "previous": previous, "player": player, "reason": reason},
        )
        ctrl.night.async_source_changed(source, previous)  # its devices are up: the night mode's event

    @callback
    def async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
