"""Room volume: one volume per room, not per media player.

Presets (quiet, normal, loud, party; a volume per room) set the room volume
by name. A preset stays chosen until the volume is changed otherwise; above
the night mode's limit it is limited, and comes back after the night. The
power-on preset (option) is the room volume whenever the room comes on.
The volume fades to a preset (preset fade, eased in and out; 0: it jumps);
the power-on preset fades up from the volume the player starts with (down it
is set at once: no fade from last night's volume). A change from outside
during the fade stops it (and is the room's volume then). A fade cut by a
restart of Home Assistant goes on once its player plays again, unless the
volume was changed meanwhile to outside the fade's range.

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

from .const import (
    CONF_POWER_ON_PRESET,
    CONF_PRESET,
    CONF_PRESET_FADE,
    DOMAIN,
    ENTITY_DEFAULTS,
    PRESET_NONE,
    PRESETS,
)
from .ramp import VolumeRamp, ease_in_out

if TYPE_CHECKING:
    from .controller import RoomController, Source
    from .output import DeviceOutput, PlayerOutput

_LOGGER = logging.getLogger(__name__)

TOLERANCE = 0.011  # players round to whole percent
OWN_SECONDS = 30
# Music Assistant may report a player as playing before the group lists it as a
# member: decide only after a short moment
SETTLE_SECONDS = 2
FADE_STEP_SECONDS = 0.5  # the preset fade's steps
RESUME_SECONDS = 120  # after a restart: how long a cut fade waits for its player

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
        self._learning: dict[str, CALLBACK_TYPE] = {}
        self._fades: dict[str, VolumeRamp] = {}
        # fades cut by a restart, waiting for their player to play again
        self._cut: dict[str, dict[str, float]] = {}
        self._resuming: dict[str, CALLBACK_TYPE] = {}
        self._resume_expiry: CALLBACK_TYPE | None = None
        # the power-on preset was chosen: the next volume the room sets fades
        self._fade_next = False

    @property
    def value(self) -> float | None:
        value = self.ctrl.data.get("room_volume")
        return float(value) if value is not None else None

    # ---------------------------------------------------------------- presets

    @property
    def presets(self) -> dict[str, float]:
        """The room's presets (0..1), in their order; 0 is not offered. The
        values are the number entities'."""
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

    def fade_seconds(self) -> float:
        """The preset fade (s) set on the device."""
        try:
            return max(0.0, float(self.ctrl.setting(CONF_PRESET_FADE) or 0))
        except (TypeError, ValueError):
            return 0.0

    async def async_set_preset(self, name: str | None, fade: float | None = None) -> None:
        """Choose a preset (None: none, the volume stays as it is); the volume
        fades there in fade s (None: the preset fade set on the device)."""
        if name is None:
            self._choose(None)
            return
        self._choose(name, self.preset_value(name))
        for output_id in list(self._cut):
            self._drop_cut(output_id)  # chosen anew: no fade from before the restart
        ctrl = self.ctrl
        seconds = self.fade_seconds() if fade is None else max(0.0, float(fade))
        _LOGGER.debug("%s: volume preset %s (fade %.1f s)", ctrl.entry.title, name, seconds)
        if ctrl.volume_device is not None:
            await self.async_apply_device(fade=seconds)
        else:
            src = ctrl.active_source
            if src is not None:
                await self.async_apply(src, fade=seconds)  # in a group: only remembered

    @callback
    def async_power_on(self, fade: bool = True) -> None:
        """The room came on: the power-on preset is its volume (players get it
        when they start, fading from their volume)."""
        name = self.ctrl.setting(CONF_POWER_ON_PRESET)
        if not name or name == PRESET_NONE:
            return
        if name not in self.presets:
            _LOGGER.info("%s: power-on preset %s is not offered (0): skipped", self.ctrl.entry.title, name)
            return
        self._choose(name, self.presets[name])
        self._fade_next = fade and not self.ctrl.wake.active  # a wake sets its own volume

    async def async_preset_changed(self, name: str) -> None:
        """A preset's value was changed (number entity): the chosen one goes along."""
        if self.ctrl.data.get("volume_preset") != name:
            return  # (the select is updated with the room)
        if name in self.presets:
            await self.async_set_preset(name)
        else:
            self._choose(None)  # no longer offered: the volume stays

    async def async_started(self) -> None:
        """HA is up: a fade cut by the restart goes on; a chosen preset whose
        value no longer matches the room volume."""
        self._load_cut_fades()
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

    def _in_group(self, src: Source) -> bool:
        """Member of a group, also of one that does not play right now (e.g.
        joined by follow me before the group starts)."""
        return bool(src.backend.groups_of(src.player, idle=True))

    def _takes_part(self, src: Source) -> bool:
        return self.ctrl.player_supports(src, MediaPlayerEntityFeature.VOLUME_SET)

    @callback
    def async_on_device(self, old: State | None, new: State | None) -> None:
        """State change of the room's volume device."""
        device = self.ctrl.volume_device
        if device is None or new is None or old is None or self.ctrl._stopped:
            return
        self._check_cut(device.id)
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
        self._check_cut(src.player, *([self.ctrl.volume_device.id] if self.ctrl.volume_device else []))
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

    async def async_apply_device(self, fade: float | None = None) -> None:
        """The volume device gets the room's volume (or the wake's start volume).

        fade: s to fade there (None: no fade, unless the power-on preset's)."""
        device = self.ctrl.volume_device
        if device is None or self.ctrl._stopped or not device.can_set:
            return
        if self.ctrl.sleep.fading_player(device.id):
            return
        if fade is None and (self.fading(device.id) or device.id in self._cut):
            self._check_cut(device.id)
            return  # a preset fade is on its way there (or goes on after the restart)
        override = self.ctrl.wake.start_volume_for(device.id)
        if override is None and self.ctrl.wake.owns(device.id):
            self._fade_next = False
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
        fade = self._fade_for(fade, override, current, target)
        if current is not None and abs(current - target) <= device.tolerance:
            self.async_cancel_fade(device.id)
            return
        _LOGGER.debug("%s: %s to room volume %.2f", self.ctrl.entry.title, device.id, target)
        await self._set(device, target, current, fade)

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

    async def async_apply(self, src: Source, left_group: bool = False, fade: float | None = None) -> None:
        """Give the player the room's volume if it plays on its own.

        left_group: we have just taken it out of its group (its report may
        still list it as a member). fade: s to fade there (None: no fade,
        unless the power-on preset's).
        """
        if self.ctrl.volume_device is not None:
            await self.async_apply_device(fade=fade)
            if not self.ctrl._stopped:
                await self._restore_level(src, left_group)
            return
        if self.ctrl._stopped:
            return
        if not left_group and self._in_group(src):
            self._fade_next = False  # the group decides this start's volume
            return
        if self.ctrl.player_state(src) not in _POWERED:
            return
        if not self._takes_part(src):
            return
        if self.ctrl.sleep.fading_player(src.player):
            return  # the sleep timer is lowering it right now
        if fade is None and (self.fading(src.player) or src.player in self._cut):
            self._check_cut(src.player)
            return  # a preset fade is on its way there (or goes on after the restart)
        current = _volume(self.hass.states.get(src.player))
        override = self.ctrl.wake.start_volume_for(src.player)
        if override is None and self.ctrl.wake.owns(src.player):
            self._fade_next = False
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
        fade = self._fade_for(fade, override, current, target)
        if current is not None and abs(current - target) <= TOLERANCE:
            self.async_cancel_fade(src.player)
            return
        _LOGGER.debug("%s: %s to room volume %.2f", self.ctrl.entry.title, src.player, target)
        await self._set(self.ctrl.vol(src), target, current, fade)

    # ------------------------------------------------------------------ fading

    def _fade_for(self, fade: float | None, override: float | None, current: float | None, target: float) -> float:
        """The fade of this set: asked for, or the power-on preset's (once;
        only up, down it is set at once)."""
        power_on = fade is None and self._fade_next
        self._fade_next = False
        if override is not None:
            return 0.0  # the wake's start volume: at once
        if power_on:
            return self.fade_seconds() if current is not None and current < target else 0.0
        return fade or 0.0

    def fading(self, output_id: str) -> bool:
        """A preset fade runs on this output."""
        ramp = self._fades.get(output_id)
        return ramp is not None and ramp.running

    async def _set(self, out: PlayerOutput | DeviceOutput, target: float, current: float | None, fade: float) -> None:
        """Set out to target: at once, or fading from current."""
        if fade > 0 and current is not None:
            self.ctrl.night.async_cancel_ramp(out.id)  # this fade replaces the night's fade back
            ramp = self._fades.get(out.id)
            if ramp is None or ramp.output is not out:
                if ramp is not None:
                    ramp.stop()
                ramp = self._fades[out.id] = VolumeRamp(
                    self.hass,
                    out,
                    self.ctrl._spawn,
                    on_done=lambda out_id=out.id: self._forget_fade(out_id),
                    step_seconds=FADE_STEP_SECONDS,
                    curve=ease_in_out,
                )
            if ramp.start(target, fade):
                # a restart of Home Assistant in between: it goes on afterwards
                self._cut_fades[out.id] = {"from": current, "to": target, "seconds": fade}
                self.ctrl._save()
                return
        self.async_cancel_fade(out.id)
        await out.async_set(target)

    @property
    def _cut_fades(self) -> dict[str, dict[str, float]]:
        """The running preset fades (output: from, to, seconds), stored."""
        return self.ctrl.data.setdefault("preset_fades", {})

    @callback
    def _forget_fade(self, output_id: str) -> None:
        if self.hass.is_stopping:
            return  # Home Assistant stops: it goes on after the restart
        if self._cut_fades.pop(output_id, None) is not None:
            self.ctrl._save()

    @callback
    def _load_cut_fades(self) -> None:
        """HA is up: the fades cut by the restart wait for their player to play
        (it may report late), at most RESUME_SECONDS."""
        if not self._cut_fades:
            return
        self._cut = dict(self._cut_fades)
        for output_id in self._cut:
            self._schedule_resume(output_id)

        @callback
        def _expired(_now) -> None:
            self._resume_expiry = None
            for output_id in list(self._cut):
                self._drop_cut(output_id)  # its player did not come back playing

        self._resume_expiry = async_call_later(self.hass, RESUME_SECONDS, _expired)

    @callback
    def _drop_cut(self, output_id: str) -> None:
        self._cut.pop(output_id, None)
        if (unsub := self._resuming.pop(output_id, None)) is not None:
            unsub()
        self._forget_fade(output_id)

    @callback
    def _schedule_resume(self, output_id: str) -> None:
        """Check after a short moment (by then a group lists its member)."""
        if output_id in self._resuming:
            return

        @callback
        def _due(_now) -> None:
            self._resuming.pop(output_id, None)
            self.ctrl._spawn(self._resume(output_id))

        self._resuming[output_id] = async_call_later(self.hass, SETTLE_SECONDS, _due)

    async def _resume(self, output_id: str) -> None:
        """A fade cut by the restart goes on from where the volume is now;
        changed meanwhile (outside from..to): that is the room volume."""
        fade = self._cut.get(output_id)
        if fade is None or self.ctrl._stopped:
            return
        out, src = self.ctrl.output(output_id)
        if out is None:
            self._drop_cut(output_id)
            return
        if src is not None:
            if self._in_group(src):
                self._drop_cut(output_id)  # in a group meanwhile: the group decides
                return
            if not self.ctrl.source_playing(src):
                return  # not (yet) playing: wait for it
        elif not any(self.ctrl.source_active(s) for s in self.ctrl.sources.values()):
            return  # the volume device, with nothing playing (yet)
        current = out.level()
        if current is None:
            return
        self._drop_cut(output_id)
        start, end = float(fade["from"]), float(fade["to"])
        low, high = sorted((start, end))
        if not low - out.tolerance <= current <= high + out.tolerance:
            _LOGGER.info("%s: %s changed during the restart, keeping %.2f", self.ctrl.entry.title, out.id, current)
            self.learn(current)
            return
        # the rest of the way in its share of the time
        share = abs(end - current) / abs(end - start) if abs(end - start) > 0.0005 else 0.0
        seconds = max(FADE_STEP_SECONDS, float(fade["seconds"]) * share)
        if src is None:
            await self.async_apply_device(fade=seconds)
        else:
            await self.async_apply(src, fade=seconds)

    @callback
    def _check_cut(self, *output_ids: str) -> None:
        """A state change: a cut fade may go on now."""
        for output_id in output_ids:
            if output_id in self._cut:
                self._schedule_resume(output_id)

    @callback
    def async_cancel_fade(self, output_id: str) -> None:
        """Someone else sets this output's volume now (night, sleep, wake)."""
        if (ramp := self._fades.get(output_id)) is not None:
            ramp.stop()
        self._drop_cut(output_id)

    @callback
    def async_off(self) -> None:
        """The room is turned off: no fade goes on."""
        self._fade_next = False
        self.async_cancel_fades()

    def recent_sets(self, output_id: str) -> list[float]:
        """The volumes a preset fade set lately (their reports may still come)."""
        ramp = self._fades.get(output_id)
        return ramp.recent() if ramp is not None else []

    @callback
    def async_pause_fades(self) -> float:
        """Stop the running fades; returns the longest time they had left."""
        left = max((r.remaining for r in self._fades.values()), default=0.0)
        self.async_cancel_fades()
        return left

    async def async_resume_fades(self, seconds: float) -> None:
        """Fade on to the room volume (as it is limited now) in seconds."""
        if seconds <= 0:
            return
        if self.ctrl.volume_device is not None:
            await self.async_apply_device(fade=seconds)
        elif (src := self.ctrl.active_source) is not None:
            await self.async_apply(src, fade=seconds)

    @callback
    def async_cancel_fades(self) -> None:
        for output_id in list(self._fades):
            self.async_cancel_fade(output_id)

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
        for ramp in self._fades.values():
            ramp.stop()  # (unloaded: stored, it goes on when set up again)
        for unsub in self._resuming.values():
            unsub()
        self._resuming.clear()
        self._cut.clear()
        if self._resume_expiry is not None:
            self._resume_expiry()
            self._resume_expiry = None
