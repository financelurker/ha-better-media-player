"""Room volume: one volume per room, not per media player.

Presets (quiet, normal, loud, party; a volume per room) set the room volume
by name. A preset stays chosen until the volume is changed otherwise; above
the night mode's limit it is limited, and comes back after the night. The
power-on preset (option) is the room volume whenever the room comes on.

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

With a volume device (see output.py) the room's volume is the device's: it is
learned from the device (also during group playback: the device is no group
member) and set on it once it is ready again (e.g. after its power came
back). The players keep their own level: learned while they play on their
own, and given back when they play on their own again after group playback
(where the group set their volume).

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
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_call_later

from .const import CONF_POWER_ON_PRESET, CONF_PRESET, DOMAIN, ENTITY_DEFAULTS, PRESET_NONE, PRESETS

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

    # ---------------------------------------------------------------- presets

    @property
    def presets(self) -> dict[str, float]:
        """The room's presets (0..1), in their order; 0 is not offered. The
        values are the number entities' (set from the options)."""
        out: dict[str, float] = {}
        for name in PRESETS:
            key = CONF_PRESET.format(name)
            value = self.ctrl.data.get(key, ENTITY_DEFAULTS[key])
            if value not in (None, "") and float(value) > 0:
                out[name] = max(0.0, min(1.0, float(value) / 100))
        return out

    @property
    def preset(self) -> str | None:
        """The chosen preset (None: the volume was set otherwise)."""
        name = self.ctrl.data.get("volume_preset")
        return name if name in self.presets else None

    def preset_value(self, name: str) -> float:
        presets = self.presets
        if name not in presets:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="preset_not_offered",
                translation_placeholders={"preset": name, "room": self.ctrl.entry.title},
            )
        return presets[name]

    @callback
    def _choose(self, name: str | None, volume: float | None = None) -> None:
        if name is None:
            if self.ctrl.data.pop("volume_preset", None) is None:
                return
        else:
            self.ctrl.data["volume_preset"] = name
            # not limited by the night mode: the preset comes back after the night
            self.ctrl.data["room_volume"] = round(volume, 3)
        self.ctrl._save()
        self.ctrl._notify()

    async def async_set_preset(self, name: str | None) -> None:
        """Choose a preset (None: none, the volume stays as it is)."""
        if name is None:
            self._choose(None)
            return
        self._choose(name, self.preset_value(name))
        ctrl = self.ctrl
        _LOGGER.debug("%s: volume preset %s", ctrl.entry.title, name)
        if ctrl.volume_device is not None:
            await self.async_apply_device()
        else:
            src = ctrl.active_source
            if src is not None:
                await self.async_apply(src)  # in a group: only remembered

    @callback
    def async_power_on(self) -> None:
        """The room came on: the power-on preset is its volume (players get it
        when they start)."""
        name = self.ctrl.setting(CONF_POWER_ON_PRESET)
        if not name or name == PRESET_NONE:
            return
        if name not in self.presets:
            _LOGGER.info("%s: power-on preset %s is not offered (0): skipped", self.ctrl.entry.title, name)
            return
        self._choose(name, self.presets[name])

    async def async_preset_changed(self, name: str) -> None:
        """A preset's value was changed (number entity): the chosen one goes along."""
        if self.ctrl.data.get("volume_preset") != name:
            return  # (the select is updated with the room)
        if name in self.presets:
            await self.async_set_preset(name)
        else:
            self._choose(None)  # no longer offered: the volume stays

    async def async_started(self) -> None:
        """HA is up: a chosen preset whose value was changed in the options."""
        name = self.preset
        if name is not None and self.value is not None and abs(self.presets[name] - self.value) >= 0.0005:
            await self.async_set_preset(name)

    # ------------------------------------------------------------ own changes

    @callback
    def note_own(self, player: str, volume: float) -> None:
        """A volume Better Media Player sets on player (not a room wish)."""
        self._own.setdefault(player, deque(maxlen=8)).append((float(volume), time.monotonic()))

    def _is_own(self, player: str, volume: float, tolerance: float = TOLERANCE) -> bool:
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
            elif abs(volume - v) <= tolerance:
                sets.remove(entry)
                return True
        return False

    # ---------------------------------------------------------------- learning

    @callback
    def learn(self, volume: float) -> None:
        # at night, a wish above the limit counts as the limit (what is heard)
        volume = round(max(0.0, min(1.0, self.ctrl.night.limit(float(volume)))), 3)
        if self.value is not None and abs(self.value - volume) < 0.0005:
            return
        self.ctrl.data.pop("volume_preset", None)  # changed otherwise: no preset any more
        self.ctrl.data["room_volume"] = volume
        self.ctrl._save()
        self.ctrl._notify()

    @callback
    def learn_wish(self, src: Source, volume: float) -> None:
        """Volume set through the room player."""
        if self.ctrl.volume_device is not None:
            self.learn(volume)
        elif self._takes_part(src) and not self._in_group(src):
            self.learn(volume)

    def in_group(self, src: Source) -> bool:
        return self._in_group(src)

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
    def async_on_device(self, old: State | None, new: State | None) -> None:
        """State change of the room's volume device."""
        device = self.ctrl.volume_device
        if device is None or new is None or old is None or self.ctrl._stopped:
            return
        level = device.level()
        if level is None:
            return  # off / unavailable: the room keeps its volume
        before = device.level(old)
        if before is None:
            # ready again (e.g. its power came back): it gets the room's volume
            self._schedule_apply_device()
        elif abs(level - before) >= 0.0005 and not self._is_own(device.id, level, device.tolerance):
            self._schedule_learn_device(level)

    @callback
    def async_on_state(self, src: Source, old: State | None, new: State | None) -> None:
        """State change of one of the room's players."""
        if new is None or old is None or self.ctrl._stopped:
            return
        vol_new, vol_old = _volume(new), _volume(old)
        if self.ctrl.volume_device is not None:
            if (
                vol_new is not None
                and vol_old is not None
                and abs(vol_new - vol_old) >= 0.0005
                and new.state in _POWERED
                and not self._is_own(src.player, vol_new)
            ):
                self._schedule_learn_level(src, vol_new)
        elif (
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
            cap = self.ctrl.night.cap
            if current is not None and (
                abs(current - volume) < 0.0005
                # set back by the night mode meanwhile: the wish was "loud"
                or (cap is not None and volume > cap and abs(current - cap) <= TOLERANCE)
            ):
                self.learn(volume)

        self._learning[src.player] = async_call_later(self.hass, SETTLE_SECONDS, _due)

    @property
    def _levels(self) -> dict[str, float]:
        """With a volume device: each player's own level."""
        return self.ctrl.data.setdefault("player_levels", {})

    @callback
    def _schedule_learn_level(self, src: Source, volume: float) -> None:
        """A player's own level changed (on its own, not in a group)."""
        if (unsub := self._learning.pop(src.player, None)) is not None:
            unsub()

        @callback
        def _due(_now) -> None:
            self._learning.pop(src.player, None)
            if self.ctrl._stopped or self._in_group(src):
                return
            current = _volume(self.hass.states.get(src.player))
            if current is not None and abs(current - volume) < 0.0005:
                self._levels[src.player] = round(volume, 3)
                self.ctrl._save()

        self._learning[src.player] = async_call_later(self.hass, SETTLE_SECONDS, _due)

    async def _restore_level(self, src: Source, left_group: bool) -> None:
        """The player plays on its own: its own level back (a group set it)."""
        if (not left_group and self._in_group(src)) or self.ctrl.player_state(src) not in _POWERED:
            return
        if not self._takes_part(src):
            return
        current = _volume(self.hass.states.get(src.player))
        level = self._levels.get(src.player)
        if level is None:
            if current is not None:
                self._levels[src.player] = current  # first time: what it has now
                self.ctrl._save()
            return
        if current is not None and abs(current - level) > TOLERANCE:
            await self.ctrl._player(src, "volume_set", {"volume_level": level})

    @callback
    def _schedule_learn_device(self, volume: float) -> None:
        key = self.ctrl.volume_device.id
        if (unsub := self._learning.pop(key, None)) is not None:
            unsub()

        @callback
        def _due(_now) -> None:
            self._learning.pop(key, None)
            device = self.ctrl.volume_device
            if self.ctrl._stopped or device is None:
                return
            current = device.level()
            cap = self.ctrl.night.cap
            if current is not None and (
                abs(current - volume) < 0.0005
                or (cap is not None and volume > cap and abs(current - cap) <= device.tolerance)
            ):
                self.learn(volume)

        self._learning[key] = async_call_later(self.hass, SETTLE_SECONDS, _due)

    @callback
    def _schedule_apply_device(self) -> None:
        key = self.ctrl.volume_device.id
        if key in self._pending:
            return

        @callback
        def _due(_now) -> None:
            self._pending.pop(key, None)
            self.ctrl._spawn(self.async_apply_device())

        self._pending[key] = async_call_later(self.hass, SETTLE_SECONDS, _due)

    async def async_apply_device(self) -> None:
        """The volume device gets the room's volume (or the wake's start volume)."""
        device = self.ctrl.volume_device
        if device is None or self.ctrl._stopped or not device.can_set:
            return
        if self.ctrl.sleep.fading_player(device.id):
            return
        override = self.ctrl.wake.start_volume_for(device.id)
        if override is None and self.ctrl.wake.owns(device.id):
            self.ctrl.wake.async_output_ready()  # a wake fading in goes on there
            return
        target = self.value if override is None else override
        current = device.level()
        if target is None:
            if current is not None:
                self.learn(current)
            return
        target = self.ctrl.night.limit(target)
        if self.ctrl.data.get("volume_restore", {}).pop(device.id, None) is not None:
            self.ctrl._save()
        if current is not None and abs(current - target) <= device.tolerance:
            return
        _LOGGER.debug("%s: %s to room volume %.2f", self.ctrl.entry.title, device.id, target)
        await device.async_set(target)

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
        if self.ctrl.volume_device is not None:
            await self.async_apply_device()
            if not self.ctrl._stopped:
                await self._restore_level(src, left_group)
            return
        if self.ctrl._stopped or (not left_group and self._in_group(src)):
            return
        if self.ctrl.player_state(src) not in _POWERED:
            return
        if not self._takes_part(src):
            return
        if self.ctrl.sleep.fading_player(src.player):
            return  # the sleep timer is lowering it right now
        current = _volume(self.hass.states.get(src.player))
        override = self.ctrl.wake.start_volume_for(src.player)
        if override is None and self.ctrl.wake.owns(src.player):
            return  # the wake sets this player's volume
        target = self.value if override is None else override
        if target is not None:
            target = self.ctrl.night.limit(target)
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
    def async_cancel_apply(self, player: str) -> None:
        """Someone else (the wake) decides this start's volume."""
        if (unsub := self._pending.pop(player, None)) is not None:
            unsub()

    @callback
    def async_stop(self) -> None:
        for unsub in [*self._pending.values(), *self._learning.values()]:
            unsub()
        self._pending.clear()
        self._learning.clear()
