"""Night mode of a room.

While on (switch, persisted, stays on until someone turns it off):
- the subwoofer stays off, also when follow me prewarms the speakers; when the
  night mode starts during playback it goes off right away,
- video still turns the display on, with shorter times (own idle timeout and
  display run-down),
- the room's volume is limited to a maximum, whoever sets it: the players'
  volume, or with a volume device the device's (the players keep their
  level). Volumes Better Media Player sets itself are limited before they are
  sent; others are set back as soon as they are reported (a short moment
  louder).

When it ends, the volume fades back up to the room volume (or is set at once
if nothing plays).

Turning it on and off is up to the user or to other integrations and
automations. So is anything else at night (e.g. the AV receiver's night sound
mode): the event below says when, once the room's devices are up.

    better_media_player_night_mode:
        entity_id, entry_id, room, night_mode (true / false),
        reason: switched (switched while the room is on), room_on (the room
        came on: a source started after none) or source (another source
        started, e.g. the TV for video after music) — at night, or the first
        time after the night mode ended while nothing could be told; after the
        power-on sequence, right after the source event
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.core import State, callback

from .const import (
    CONF_NIGHT_FADE_IN,
    EVENT_NIGHT_MODE,
    ROLE_BASS,
)
from .ramp import VolumeRamp

if TYPE_CHECKING:
    from .controller import RoomController, Source
    from .output import DeviceOutput, PlayerOutput

_LOGGER = logging.getLogger(__name__)

_PLAYING = {"playing", "buffering"}
_POWERED = {"on", "idle", "playing", "paused", "buffering"}


class NightMode:
    def __init__(self, ctrl: RoomController) -> None:
        self.ctrl = ctrl
        self.hass = ctrl.hass
        self._clamping: set[str] = set()
        self._ramps: dict[str, VolumeRamp] = {}

    # ------------------------------------------------------------------ state

    @property
    def active(self) -> bool:
        return bool(self.ctrl.data.get("night_mode", False))

    @property
    def cap(self) -> float | None:
        """Volume limit 0..1 while the night mode is on (None: no limit)."""
        if not self.active:
            return None
        value = float(self.ctrl.data.get("night_max_volume", 0) or 0)
        return value / 100 if value > 0 else None

    def limit(self, volume: float) -> float:
        cap = self.cap
        return min(volume, cap) if cap is not None else volume

    @property
    def player_cap(self) -> float | None:
        """The limit for the players' own volume (none with a volume device:
        there the device is limited and the players keep their level)."""
        return None if self.ctrl.volume_device is not None else self.cap

    def player_limit(self, volume: float) -> float:
        cap = self.player_cap
        return min(volume, cap) if cap is not None else volume

    def _outputs(self) -> list[tuple[PlayerOutput | DeviceOutput, Source | None]]:
        if self.ctrl.volume_device is not None:
            return [(self.ctrl.volume_device, None)]
        return [(self.ctrl.vol(src), src) for src in self.ctrl.sources.values()]

    async def async_started(self) -> None:
        """HA is up: the limit holds also for what changed while it was down
        (or a lower limit); a fade back that was cut by a
        restart goes on from where the volume is now."""
        if self.cap is not None:
            for out, src in self._outputs():
                await self._clamp(out, src)
        elif self.ctrl.data.get("night_fade_back"):
            await self._fade_back()

    def role_enabled(self, key: str) -> bool:
        return not (self.active and key == ROLE_BASS)

    def ramping(self, player: str) -> bool:
        ramp = self._ramps.get(player)
        return ramp is not None and ramp.running

    # ---------------------------------------------------------------- switch

    async def async_set(self, on: bool) -> None:
        if on == self.active:
            return
        ctrl = self.ctrl
        ctrl.data["night_mode"] = on
        ctrl._save_now()
        _LOGGER.info("%s: night mode %s", ctrl.entry.title, "on" if on else "off")
        if on:
            self._cancel_ramps()
            ctrl.data.pop("night_fade_back", None)
            bass = ctrl.roles.get(ROLE_BASS)
            if bass is not None and ctrl.auto_power and not ctrl.manual and ctrl.role_on(bass):
                bass.force_off = True  # no run-down
            for out, src in self._outputs():
                await self._clamp(out, src)
        else:
            await self._fade_back()
        ctrl.wake.async_night_changed()
        if (hub := ctrl.hub) is not None:
            hub.async_schedule("night_mode")
        await ctrl.async_update("night_mode")

    # ------------------------------------------------------------------ event

    @callback
    def async_check(self) -> None:
        """After each update: switched while the room is on (and no power-on
        sequence runs: then once it is done)."""
        ctrl = self.ctrl
        if not ctrl._started or ctrl._stopped or not ctrl.source_watch.ready:
            return
        if "night_announced" not in ctrl.data:
            ctrl.data["night_announced"] = self.active  # (first look after an update: as it is)
            ctrl._save()
            return
        watch = ctrl.source_watch
        if watch.source is None or watch.changing or ctrl.sequence_running:
            return  # told when the room (or its new source) is up
        if bool(ctrl.data["night_announced"]) != self.active:
            self._fire(self.active, "switched")

    @callback
    def async_source_changed(self, source: str | None, previous: str | None) -> None:
        """A source started (after the source event; its devices are up): at night,
        or the first time after the night mode ended while nothing told it."""
        if source is None:
            return
        active = self.active
        if active or bool(self.ctrl.data.get("night_announced", False)) != active:
            self._fire(active, "room_on" if previous is None else "source")

    @callback
    def _fire(self, active: bool, reason: str) -> None:
        ctrl = self.ctrl
        ctrl.data["night_announced"] = active
        ctrl._save()
        _LOGGER.info("%s: night mode %s announced (%s)", ctrl.entry.title, "on" if active else "off", reason)
        ctrl.async_fire(EVENT_NIGHT_MODE, {"night_mode": active, "reason": reason})

    # ------------------------------------------------------------ volume cap

    @callback
    def async_on_state(self, src: Source, new: State | None) -> None:
        """A player of the room reported: over the limit? Set it back."""
        if self.ctrl.volume_device is None and new is not None and new.state in _POWERED:
            self._check(self.ctrl.vol(src), src)

    @callback
    def async_on_device(self, new: State | None) -> None:
        """The volume device reported: over the limit? Set it back."""
        if (device := self.ctrl.volume_device) is not None and new is not None:
            self._check(device, None)

    @callback
    def _check(self, out: PlayerOutput | DeviceOutput, src: Source | None) -> None:
        cap = self.cap
        vol = out.level()
        if cap is None or vol is None or vol <= cap + out.tolerance or out.id in self._clamping:
            return
        self._clamping.add(out.id)
        self.ctrl._spawn(self._clamp_task(out, src))

    async def _clamp_task(self, out: PlayerOutput | DeviceOutput, src: Source | None) -> None:
        try:
            await self._clamp(out, src)
        finally:
            self._clamping.discard(out.id)

    async def _clamp(self, out: PlayerOutput | DeviceOutput, src: Source | None) -> None:
        cap = self.cap
        if cap is None or (src is not None and self.ctrl.player_state(src) not in _POWERED):
            return
        if not out.can_set:
            return
        vol = out.level()
        if vol is not None and vol > cap + out.tolerance:
            # a sleep timer fading this output goes on below the limit
            self.ctrl.sleep.limit_fade(out.id, cap)
            _LOGGER.debug("%s: %s limited to %.2f (night mode)", self.ctrl.entry.title, out.id, cap)
            await out.async_set(cap)

    async def async_limit_changed(self) -> None:
        """The limit was lowered: holds right away."""
        if self.cap is None:
            return
        for out, src in self._outputs():
            await self._clamp(out, src)
        if (hub := self.ctrl.hub) is not None:
            hub.async_schedule("night_limit")

    # --------------------------------------------------------------- fade back

    async def _fade_back(self) -> None:
        """Night mode over: back up to the room volume."""
        ctrl = self.ctrl
        target = ctrl.volume.value
        if target is None:
            return
        ctrl.data["night_fade_back"] = True  # a restart in between goes on afterwards
        ctrl._save_now()
        seconds = float(ctrl.setting(CONF_NIGHT_FADE_IN)) * 60
        for out, src in self._outputs():
            if (
                (src is not None and ctrl.player_state(src) not in _POWERED)
                or not out.can_set
                or (src is not None and self._hub_group(src))
                or ctrl.sleep.fading_player(out.id)  # the sleep timer is going down
                or ctrl.wake.owns(out.id)  # the wake fades in itself
            ):
                continue
            ramp = self._ramps.get(out.id)
            if ramp is None:
                ramp = self._ramps[out.id] = VolumeRamp(self.hass, out, ctrl._spawn, self._ramp_done)
            playing = ctrl.source_playing(src) if src is not None else any(
                ctrl.source_playing(s) for s in ctrl.sources.values()
            )
            ramp.start(target, seconds if playing else 0)
        self._ramp_done()

    @callback
    def _ramp_done(self) -> None:
        if not any(r.running for r in self._ramps.values()) and self.ctrl.data.pop("night_fade_back", None):
            self.ctrl._save_now()

    def _hub_group(self, src: Source) -> bool:
        """In a group the hub looks after (a Music Assistant group: it brings the
        member back); other groups: the room does it itself."""
        hub = self.ctrl.hub
        if hub is None:
            return False
        return any(hub.ma.is_group(g) for g in src.backend.groups_of(src.player, idle=True))

    @callback
    def async_cancel_ramp(self, player: str) -> None:
        if (ramp := self._ramps.get(player)) is not None:
            ramp.cancel()

    @callback
    def _cancel_ramps(self) -> None:
        for ramp in self._ramps.values():
            ramp.cancel()

    @callback
    def async_stop(self) -> None:
        for ramp in self._ramps.values():
            ramp.stop()
