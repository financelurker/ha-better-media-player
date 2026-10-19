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
the last OWN_SECONDS counts as ours (once per set).
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
_NOT_GROUPED = {"off", "standby", "unavailable", "unknown"}


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
        self._learning: dict[str, CALLBACK_TYPE] = {}

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
        """A report of a volume we set; each set is recognised once only, so
        a person going back to that value later is a wish again."""
        now = time.monotonic()
        sets = self._own.get(player)
        if not sets:
            return False
        for entry in list(sets):
            v, ts = entry
            if now - ts >= OWN_SECONDS:
                sets.remove(entry)
            elif abs(volume - v) <= TOLERANCE:
                sets.remove(entry)
                return True
        return False

    # ---------------------------------------------------------------- learning

    @callback
    def learn(self, volume: float) -> None:
        volume = round(max(0.0, min(1.0, float(volume))), 3)
        if self.value is not None and abs(self.value - volume) < 0.0005:
            return
        self.ctrl.data["room_volume"] = volume
        self.ctrl._save()
        self.ctrl._notify()

    @callback
    def learn_wish(self, src: Source, volume: float) -> None:
        """Volume set through the room player."""
        if self._takes_part(src) and not self._in_group(src):
            self.learn(volume)

    def _in_group(self, src: Source) -> bool:
        """Member of a group, also of one that does not play right now (e.g.
        joined by follow me before the group starts). Groups that are off are
        left out: static groups list their members even then."""
        for state in self.hass.states.async_all("media_player"):
            if state.entity_id == src.player or state.state in _NOT_GROUPED:
                continue
            if src.player in (state.attributes.get("group_members") or []):
                return True
        return False

    def _takes_part(self, src: Source) -> bool:
        return self.ctrl.player_supports(src, MediaPlayerEntityFeature.VOLUME_SET)

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
        ):
            self._schedule_learn(src, vol_new)

        started = new.state in _PLAYING and old.state not in _PLAYING
        if src.key == "video":
            # the video player gets the volume as soon as it is up, before it plays
            started = started or (new.state in _POWERED and old.state not in _POWERED)
        if started:
            self._schedule_apply(src)

    @callback
    def _schedule_learn(self, src: Source, volume: float) -> None:
        """Learn after a short moment: by then a group lists its member."""
        if (unsub := self._learning.pop(src.player, None)) is not None:
            unsub()  # a newer change replaces the older one

        @callback
        def _due(_now) -> None:
            self._learning.pop(src.player, None)
            if self.ctrl._stopped or self._in_group(src) or not self._takes_part(src):
                return
            current = _volume(self.hass.states.get(src.player))
            if current is not None and abs(current - volume) < 0.0005:
                self.learn(volume)

        self._learning[src.player] = async_call_later(self.hass, SETTLE_SECONDS, _due)

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

    async def async_apply(self, src: Source, left_group: bool = False) -> None:
        """Give the player the room's volume if it plays on its own.

        left_group: we have just taken it out of its group (its report may
        still list it as a member).
        """
        if self.ctrl._stopped or (not left_group and self._in_group(src)):
            return
        if self.ctrl.player_state(src) not in _POWERED:
            return
        if not self._takes_part(src):
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
        for unsub in [*self._pending.values(), *self._learning.values()]:
            unsub()
        self._pending.clear()
        self._learning.clear()
