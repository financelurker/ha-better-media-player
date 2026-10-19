"""Wake up in one room: start playback quietly and fade it in.

Flow: the room powers up (normal start sequence), the chosen player starts
at the start volume, and once it really plays the volume fades to the target.
If nothing plays within PLAY_TIMEOUT, the wake fails (event) and the room goes
off again if it was off before, so the caller can pick another source.

- Snooze: pause (or stop) for some minutes, the room stays powered, then it
  starts again at the start volume and fades in again. A pause by hand during
  the wake counts as snooze.
- A volume change by hand ends only the fade.
- After max_duration the sleep timer takes over (its fade-out, then room off).
- Ends with stop_wake (playback goes on), a stop by hand, room off, a new
  sleep timer (playback goes on) or a new wake (replaced).

State is persisted in controller.data["wake"]; one scheduled callback at a
time (play check, snooze end or max duration) plus the fade's own steps.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.core import CALLBACK_TYPE, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .const import (
    CONF_SLEEP_FADE,
    CONF_SNOOZE_MINUTES,
    DOMAIN,
    EVENT_WAKE_FAILED,
    EVENT_WAKE_FINISHED,
    EVENT_WAKE_STARTED,
)
from .ramp import VolumeRamp

if TYPE_CHECKING:
    from .controller import RoomController, Source

_LOGGER = logging.getLogger(__name__)

PLAY_TIMEOUT = 60  # s after the playback was requested
RESTART_RETRY = 300  # s: a start cut by a restart is tried again within this time
DEFAULT_START_VOLUME = 0.05
DEFAULT_FADE_MINUTES = 10

PHASE_IDLE = "idle"
PHASE_STARTING = "starting"
PHASE_FADING = "fading"
PHASE_PLAYING = "playing"
PHASE_SNOOZED = "snoozed"
PHASES = [PHASE_IDLE, PHASE_STARTING, PHASE_FADING, PHASE_PLAYING, PHASE_SNOOZED]

REASON_STOPPED = "stopped"
REASON_MAX_DURATION = "max_duration"
REASON_ROOM_OFF = "room_off"
REASON_SLEEP_TIMER = "sleep_timer"
REASON_REPLACED = "replaced"
FAILED_START = "start_failed"
FAILED_NOT_PLAYING = "not_playing"

# entities shown only while a wake runs
CONTROL_KEYS = ("wake_snooze", "wake_stop")

_PLAYING = {"playing", "buffering"}
_POWERED = {"on", "idle", "playing", "paused", "buffering"}


def _now() -> float:
    return dt_util.utcnow().timestamp()


def _volume(state: State | None) -> float | None:
    value = state.attributes.get("volume_level") if state else None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class WakeAlarm:
    def __init__(self, ctrl: RoomController) -> None:
        self.ctrl = ctrl
        self.hass = ctrl.hass
        self._unsub: CALLBACK_TYPE | None = None
        self._ramp: VolumeRamp | None = None
        self._task_running = False
        self._snoozing = False  # our own pause is under way
        self._task = None

    # ------------------------------------------------------------------ state

    @property
    def _w(self) -> dict[str, Any] | None:
        return self.ctrl.data.get("wake")

    @property
    def active(self) -> bool:
        return self._w is not None

    @property
    def phase(self) -> str:
        w = self._w
        return w["phase"] if w else PHASE_IDLE

    @property
    def attributes(self) -> dict[str, Any]:
        w = self._w
        if not w:
            return {}

        def ts(value):
            return dt_util.utc_from_timestamp(value).isoformat() if value else None

        ends = w["playing_since"] + w["max_s"] if w.get("max_s") and w.get("playing_since") else None
        return {
            "source": w["source"],
            "start_volume": w["start_volume"],
            "target_volume": w["target_volume"],
            "snooze_until": ts(w.get("snooze_until")) if w["phase"] == PHASE_SNOOZED else None,
            "ends_at": ts(ends) if w["phase"] in (PHASE_FADING, PHASE_PLAYING) else None,
        }

    def _src(self) -> Source | None:
        w = self._w
        return self.ctrl.sources.get(w["source"]) if w else None

    def owns(self, player: str) -> bool:
        """The wake sets this player's volume (room volume and night fade back keep out)."""
        src = self._src()
        return src is not None and src.player == player and self.phase != PHASE_IDLE

    def start_volume_for(self, player: str) -> float | None:
        w = self._w
        if w and self.owns(player) and w["phase"] == PHASE_STARTING:
            return self.ctrl.night.limit(w["start_volume"])
        return None

    def holds(self, role) -> bool:
        """While snoozed the room keeps the power the wake's player needs."""
        w = self._w
        return bool(w and w["phase"] == PHASE_SNOOZED and w["source"] in role.sources)

    # ---------------------------------------------------------------- start

    async def async_wake(
        self,
        key: str,
        data: dict[str, Any],
        room_mode: str | None,
        start_volume: float | None,
        target_volume: float | None,
        fade_minutes: float | None,
        max_minutes: float | None,
        night_mode: bool | None,
    ) -> None:
        ctrl = self.ctrl
        if key not in ctrl.sources:
            raise HomeAssistantError(f"Unknown source: {key}")
        if self.active:
            await self.async_finish(REASON_REPLACED)
        if ctrl.sleep.active:
            await ctrl.sleep.async_cancel()  # a sleep timer would end the wake
        if night_mode is not None:
            await ctrl.night.async_set(night_mode)
        target = target_volume if target_volume is not None else ctrl.volume.value
        if target is None:
            target = _volume(self.hass.states.get(ctrl.sources[key].player)) or 0.25
        ctrl.data["wake"] = {
            "phase": PHASE_STARTING,
            "source": key,
            "data": data,
            "room_mode": room_mode,
            "start_volume": float(DEFAULT_START_VOLUME if start_volume is None else start_volume),
            "target_volume": float(target),
            "fade_s": float(DEFAULT_FADE_MINUTES if fade_minutes is None else fade_minutes) * 60,
            "max_s": float(max_minutes) * 60 if max_minutes else None,
            "room_was_off": not ctrl.room_on,
            "started_at": _now(),
            "playing_since": None,
            "fade_t0": None,
            "snooze_until": None,
            "replay": False,
        }
        self._changed()
        self._show_controls(True)
        _LOGGER.info("%s: wake up on %s", ctrl.entry.title, ctrl.sources[key].player)
        ctrl.night.async_cancel_ramp(ctrl.sources[key].player)
        self._spawn_start()

    @callback
    def _spawn_start(self) -> None:
        if self._task_running:
            return
        self._task_running = True
        # background: it may wait for a slow player (like the power sequence)
        self._task = self.hass.async_create_background_task(
            self._start(), f"{DOMAIN}_{self.ctrl.entry.entry_id}_wake", eager_start=False
        )

    async def _start(self) -> None:
        """(Re)start the playback at the start volume; the play check follows."""
        ctrl = self.ctrl
        try:
            w = self._w
            if w is None:
                return
            src = ctrl.sources[w["source"]]
            w["phase"] = PHASE_STARTING
            w["started_at"] = _now()
            self._changed()
            try:
                if w.get("resume"):
                    await self._set_volume(src, w["start_volume"])
                    await ctrl._player(src, "media_play")
                else:
                    # the room volume step in async_play_on gives the start volume
                    await ctrl.async_play_on(w["source"], dict(w["data"]), room_mode=w.get("room_mode"))
            except HomeAssistantError as err:
                _LOGGER.warning("%s: wake start failed: %s", ctrl.entry.title, err)
                await self._fail(FAILED_START)
                return
            if self._w is not w:
                return  # ended meanwhile
            w.pop("resume", None)
            if ctrl.source_playing(src):
                await self._playing()
            else:
                self._schedule(_now() + PLAY_TIMEOUT)
        finally:
            self._task_running = False

    async def _set_volume(self, src: Source, volume: float) -> None:
        if self.ctrl.player_supports(src, MediaPlayerEntityFeature.VOLUME_SET):
            await self.ctrl._player(src, "volume_set", {"volume_level": volume})

    async def _playing(self) -> None:
        """It plays: fade in, arm the end."""
        ctrl = self.ctrl
        w = self._w
        if w is None or w["phase"] != PHASE_STARTING:
            return
        src = ctrl.sources[w["source"]]
        ctrl.volume.async_cancel_apply(src.player)  # this start's volume is the wake's
        w["playing_since"] = _now()
        w["fade_t0"] = _now()
        w["phase"] = PHASE_FADING if w["fade_s"] > 0 else PHASE_PLAYING
        self._changed()
        self._fire(EVENT_WAKE_STARTED, {})
        target = ctrl.night.limit(w["target_volume"])
        if w["fade_s"] > 0:
            current = _volume(self.hass.states.get(src.player))
            if current is not None and current > w["start_volume"] + 0.011:
                await self._set_volume(src, ctrl.night.limit(w["start_volume"]))
            self._fade(src, target, w["fade_s"])
        else:
            await self._set_volume(src, target)
        self._arm_end()

    @callback
    def _fade(self, src: Source, target: float, seconds: float) -> None:
        if self._ramp is None or self._ramp.entity_id != src.player:
            if self._ramp is not None:
                self._ramp.stop()
            self._ramp = VolumeRamp(self.hass, src.player, self._setter(src), self.ctrl._spawn, self._fade_done)
        if not self._ramp.start(target, seconds):
            self._fade_done()

    def _setter(self, src: Source):
        async def _set(volume: float) -> None:
            await self.ctrl._player(src, "volume_set", {"volume_level": volume})

        return _set

    @callback
    def _fade_done(self) -> None:
        """Faded in, or someone changed the volume: from now on it just plays."""
        w = self._w
        if w is not None and w["phase"] == PHASE_FADING:
            w["phase"] = PHASE_PLAYING
            self._changed()

    @callback
    def _arm_end(self) -> None:
        w = self._w
        if w and w.get("max_s") and w.get("playing_since"):
            self._schedule(w["playing_since"] + w["max_s"])
        else:
            self._unschedule()

    async def _fail(self, reason: str) -> None:
        w = self._w
        if w is None:
            return
        self._clear()
        self._fire(EVENT_WAKE_FAILED, {"reason": reason})
        if w.get("room_was_off"):
            await self.ctrl.async_turn_off()

    # ---------------------------------------------------------------- events

    @callback
    def async_on_state(self, src: Source, old: State | None, new: State | None) -> None:
        w = self._w
        if w is None or new is None or old is None or src.key != w["source"]:
            return
        phase = w["phase"]
        if phase == PHASE_STARTING and new.state in _PLAYING and not self._task_running:
            self.ctrl._spawn(self._playing())
        elif phase == PHASE_STARTING and new.state in _PLAYING:
            pass  # the start task sees it right after play_media
        elif phase in (PHASE_FADING, PHASE_PLAYING) and old.state in _PLAYING:
            if new.state == "paused" and not self._snoozing:
                _LOGGER.info("%s: paused during the wake: snooze", self.ctrl.entry.title)
                self.ctrl._spawn(self.async_snooze(None))
            elif new.state in ("idle", "off", "standby", "on"):
                self.ctrl._spawn(self.async_finish(REASON_STOPPED))

    # ---------------------------------------------------------------- snooze

    async def async_snooze(self, minutes: float | None) -> None:
        w = self._w
        if w is None or w["phase"] not in (PHASE_FADING, PHASE_PLAYING):
            _LOGGER.debug("%s: snooze without a running wake: nothing to do", self.ctrl.entry.title)
            return
        ctrl = self.ctrl
        src = ctrl.sources[w["source"]]
        minutes = float(ctrl.opts.get(CONF_SNOOZE_MINUTES, 9)) if minutes is None else float(minutes)
        if self._ramp is not None:
            self._ramp.stop()
        w["phase"] = PHASE_SNOOZED
        w["snooze_until"] = _now() + minutes * 60
        can_pause = ctrl.player_supports(src, MediaPlayerEntityFeature.PAUSE)
        w["resume"] = can_pause
        self._changed(now=True)
        self._schedule(w["snooze_until"])
        self._snoozing = True
        try:
            if ctrl.source_playing(src):
                if can_pause:
                    await ctrl._player(src, "media_pause")
                elif ctrl.player_supports(src, MediaPlayerEntityFeature.STOP):
                    await ctrl._player(src, "media_stop")
        finally:
            self._snoozing = False
        _LOGGER.info("%s: snooze for %.0f min", ctrl.entry.title, minutes)

    # ------------------------------------------------------------------- end

    async def async_finish(self, reason: str) -> None:
        """End the wake; the playback is left as it is."""
        if not self.active:
            if reason == REASON_STOPPED:
                _LOGGER.debug("%s: stop without a running wake: nothing to do", self.ctrl.entry.title)
            return
        self._clear()
        self._fire(EVENT_WAKE_FINISHED, {"reason": reason})

    @callback
    def _clear(self) -> None:
        self._unschedule()
        if self._ramp is not None:
            self._ramp.stop()
        self.ctrl.data.pop("wake", None)
        self._changed(now=True)
        self._show_controls(False)

    # ------------------------------------------------------------ scheduling

    @callback
    def _unschedule(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _schedule(self, at: float) -> None:
        self._unschedule()
        self._unsub = async_track_point_in_utc_time(
            self.hass, self._due, dt_util.utc_from_timestamp(max(at, _now()))
        )

    @callback
    def _due(self, _now_dt: datetime) -> None:
        self._unsub = None
        self.ctrl._spawn(self._on_due())

    async def _on_due(self) -> None:
        w = self._w
        if w is None or self.ctrl._stopped:
            return
        now = _now()
        phase = w["phase"]
        if phase == PHASE_STARTING:
            if not self._task_running and now - w["started_at"] >= PLAY_TIMEOUT:
                await self._fail(FAILED_NOT_PLAYING)
        elif phase == PHASE_SNOOZED:
            if now >= w["snooze_until"]:
                self._spawn_start()
            else:
                self._schedule(w["snooze_until"])
        elif w.get("max_s") and w.get("playing_since") and now >= w["playing_since"] + w["max_s"]:
            await self._max_duration()
        else:
            self._arm_end()

    async def _max_duration(self) -> None:
        """Played long enough: the sleep timer fades out and turns the room off."""
        ctrl = self.ctrl
        await self.async_finish(REASON_MAX_DURATION)
        fade = float(ctrl.opts.get(CONF_SLEEP_FADE, 0))
        if fade > 0 and ctrl.room_on:
            await ctrl.sleep.async_set(fade, fade)
        else:
            await ctrl.async_turn_off()

    # -------------------------------------------------------------- lifecycle

    async def async_started(self) -> None:
        """HA is up: go on where the wake was."""
        w = self._w
        if w is None:
            self._show_controls(False)
            return
        self._show_controls(True)
        now = _now()
        phase = w["phase"]
        src = self._src()
        if src is None:
            self._clear()
            return
        if phase == PHASE_STARTING:
            if now - w["started_at"] < RESTART_RETRY:
                self._spawn_start()
            else:
                await self._fail(FAILED_START)
        elif phase == PHASE_SNOOZED:
            self._schedule(w["snooze_until"])
        elif phase == PHASE_FADING:
            left = w["fade_t0"] + w["fade_s"] - now if w.get("fade_t0") else 0
            if left > 0:
                self._fade(src, self.ctrl.night.limit(w["target_volume"]), left)
            else:
                self._fade_done()
            self._arm_end()
        else:
            self._arm_end()

    @callback
    def async_stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task_running = False
        self._unschedule()
        if self._ramp is not None:
            self._ramp.stop()

    # ---------------------------------------------------------------- helpers

    @callback
    def _changed(self, now: bool = False) -> None:
        if now:
            self.ctrl._save_now()
        else:
            self.ctrl._save()
        self.ctrl._notify()

    @callback
    def _fire(self, event: str, data: dict[str, Any]) -> None:
        entry = self.ctrl.entry
        entity_id = er.async_get(self.hass).async_get_entity_id("media_player", DOMAIN, f"{entry.entry_id}_player")
        self.hass.bus.async_fire(event, {"entity_id": entity_id, "entry_id": entry.entry_id, "room": entry.title, **data})

    @callback
    def refresh_controls(self) -> None:
        self._show_controls(self.active)

    @callback
    def _show_controls(self, show: bool) -> None:
        """Snooze / stop buttons: hidden while no wake runs (only if nobody else hid them)."""
        reg = er.async_get(self.hass)
        for key in CONTROL_KEYS:
            entity_id = reg.async_get_entity_id("button", DOMAIN, f"{self.ctrl.entry.entry_id}_{key}")
            if entity_id is None:
                continue
            entry = reg.async_get(entity_id)
            if entry is None:
                continue
            if show and entry.hidden_by == er.RegistryEntryHider.INTEGRATION:
                reg.async_update_entity(entity_id, hidden_by=None)
            elif not show and entry.hidden_by is None:
                reg.async_update_entity(entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION)
