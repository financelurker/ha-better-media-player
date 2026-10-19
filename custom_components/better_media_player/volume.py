"""Room volume: one volume per room, not per media player.

The room remembers the volume last chosen for it, wherever it was changed:
through the room player, or directly on one of its players (Music Assistant
app, Kodi remote, ...). When a player of the room starts to play on its own
(not as a member of a group), it gets that volume. So switching from music to
video, or starting a film by casting to Kodi, keeps the room's volume.

Not learned: what Better Media Player sets itself (sleep timer fade and
restore, this sync, follow me's join volume) and anything that happens while
the player is in a group: there the group (or follow me) decides.

Assumption: the room's players have about the same loudness at the same
volume level.

Own changes are recognised by value: the players report volumes over their
own connections with a new context, so a reported value we set ourselves in
the last OWN_SECONDS counts as ours.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import TYPE_CHECKING

from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.core import CALLBACK_TYPE, State, callback
from homeassistant.helpers.event import async_call_later

if TYPE_CHECKING:
    from .controller import RoomController, Source

_LOGGER = logging.getLogger(__name__)

TOLERANCE = 0.011  # players round to whole percent
OWN_SECONDS = 30
# Music Assistant may report a player as playing before the group lists it as a
# member: decide only after a short moment
SETTLE_SECONDS = 2

_PLAYING = {"playing", "buffering"}
_POWERED = {"on", "idle", "playing", "paused", "buffering"}


def _volume(state: State | None) -> float | None:
    if state is None:
        return None
    value = state.attributes.get("volume_level")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class RoomVolume:
    """Learns and applies the room's volume."""

    def __init__(self, ctrl: RoomController) -> None:
        self.ctrl = ctrl
        self.hass = ctrl.hass
        self._own: dict[str, deque[tuple[float, float]]] = {}
        self._pending: dict[str, CALLBACK_TYPE] = {}

    @property
    def value(self) -> float | None:
        value = self.ctrl.data.get("room_volume")
        return float(value) if value is not None else None

    # ------------------------------------------------------------ own changes

    @callback
    def note_own(self, player: str, volume: float) -> None:
        """A volume Better Media Player sets on player (not a room wish)."""
        self._own.setdefault(player, deque(maxlen=8)).append((float(volume), time.monotonic()))

    def _is_own(self, player: str, volume: float) -> bool:
        now = time.monotonic()
        return any(
            abs(volume - v) <= TOLERANCE and now - ts < OWN_SECONDS for v, ts in self._own.get(player, ())
        )

    # ---------------------------------------------------------------- learning

    @callback
    def learn(self, volume: float) -> None:
        volume = round(max(0.0, min(1.0, float(volume))), 3)
        if self.value is not None and abs(self.value - volume) < 0.0005:
            return
        self.ctrl.data["room_volume"] = volume
        self.ctrl._save()
        self.ctrl._notify()

    def _in_group(self, src: Source) -> bool:
        return bool(self.ctrl.groups_of(src.player))

    @callback
    def async_on_state(self, src: Source, old: State | None, new: State | None) -> None:
        """State change of one of the room's players."""
        if new is None or old is None or self.ctrl._stopped:
            return
        vol_new, vol_old = _volume(new), _volume(old)
        if (
            vol_new is not None
            and vol_old is not None
            and abs(vol_new - vol_old) >= 0.0005
            and new.state in _POWERED
            and not self._is_own(src.player, vol_new)
            and not self._in_group(src)
        ):
            self.learn(vol_new)

        started = new.state in _PLAYING and old.state not in _PLAYING
        if src.key == "video":
            # the video player gets the volume as soon as it is up, before it plays
            started = started or (new.state in _POWERED and old.state not in _POWERED)
        if started:
            self._schedule_apply(src)

    # ---------------------------------------------------------------- applying

    @callback
    def _schedule_apply(self, src: Source) -> None:
        if src.player in self._pending:
            return  # one check per player at a time

        @callback
        def _due(_now) -> None:
            self._pending.pop(src.player, None)
            self.ctrl._spawn(self.async_apply(src))

        self._pending[src.player] = async_call_later(self.hass, SETTLE_SECONDS, _due)

    async def async_apply(self, src: Source) -> None:
        """Give the player the room's volume if it plays on its own."""
        if self.ctrl._stopped or self._in_group(src):
            return
        if self.ctrl.player_state(src) not in _POWERED:
            return
        if not self.ctrl.player_supports(src, MediaPlayerEntityFeature.VOLUME_SET):
            return
        if self.ctrl.sleep.fading_player(src.player):
            return  # the sleep timer is lowering it right now
        current = _volume(self.hass.states.get(src.player))
        target = self.value
        if target is None:
            if current is not None:
                self.learn(current)  # first time: the room starts from what plays now
            return
        # the room's volume supersedes a restore the sleep timer still has to do
        if self.ctrl.data.get("volume_restore", {}).pop(src.player, None) is not None:
            self.ctrl._save()
        if current is not None and abs(current - target) <= TOLERANCE:
            return
        _LOGGER.debug("%s: %s to room volume %.2f", self.ctrl.entry.title, src.player, target)
        await self.ctrl._player(src, "volume_set", {"volume_level": target})

    @callback
    def async_stop(self) -> None:
        for unsub in self._pending.values():
            unsub()
        self._pending.clear()
