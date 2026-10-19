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

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.core import CALLBACK_TYPE, State, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .const import (
    CONF_NIGHT_FADE_IN,
    CONF_SLEEP_FADE,
    CONF_START_TIMEOUT,
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
STOP_SETTLE = 10  # s: a player between two items is idle for a moment, that is no stop
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
REASON_LEFT = "left"  # follow wake: the wake room was left
FAILED_START = "start_failed"
FAILED_NOT_PLAYING = "not_playing"

_PLAYING = {"playing", "buffering"}
_POWERED = {"on", "idle", "playing", "paused", "buffering"}


def _now() -> float:
    return dt_util.utcnow().timestamp()


class WakeAlarm:
    def __init__(self, ctrl: RoomController) -> None:
        self.ctrl = ctrl
        self.hass = ctrl.hass
        self._unsub: CALLBACK_TYPE | None = None
        self._ramp: VolumeRamp | None = None
        self._task: asyncio.Task | None = None
        self._stop_check: CALLBACK_TYPE | None = None
        self._before: tuple | None = None  # player before the playback request

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

    @property
    def _task_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _src(self) -> Source | None:
        w = self._w
        return self.ctrl.sources.get(w["source"]) if w else None

    def owns(self, output_id: str) -> bool:
        """The wake sets this output's volume (room volume and night fade back
        keep out): its player's, or the room's volume device."""
        src = self._src()
        return src is not None and self.ctrl.vol(src).id == output_id and self.phase != PHASE_IDLE

    def start_volume_for(self, output_id: str) -> float | None:
        w = self._w
        if w and self.owns(output_id) and w["phase"] == PHASE_STARTING:
            return self.ctrl.night.limit(w["start_volume"])
        return None

    # follow wake ---------------------------------------------------------

    @property
    def follows(self) -> bool:
        """A follow wake (the music follows out of the wake room)."""
        w = self._w
        return bool(w and w.get("follow") is not None)

    @property
    def follow_holding(self) -> bool:
        """A follow wake keeps this room in its group."""
        w = self._w
        return bool(
            w and w.get("follow") is not None and w["phase"] in (PHASE_STARTING, PHASE_FADING, PHASE_PLAYING)
        )

    @property
    def follow_snoozed(self) -> bool:
        w = self._w
        return bool(w and w.get("follow") is not None and w["phase"] == PHASE_SNOOZED)

    @property
    def seen(self) -> bool:
        w = self._w
        return bool(w and w.get("seen"))

    @callback
    def mark_seen(self) -> None:
        """Someone is in the wake room (after the wake started)."""
        w = self._w
        if w is None:
            return
        w["last_present"] = _now()
        if not w.get("seen"):
            w["seen"] = True
            self.ctrl._save()

    @property
    def last_present(self) -> float | None:
        w = self._w
        return w.get("last_present") if w else None

    def fading_player(self, output_id: str) -> bool:
        """Its volume is the wake's (not follow me's) right now."""
        return self.owns(output_id) and self.phase in (PHASE_STARTING, PHASE_FADING)

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
        follow: bool = False,
    ) -> None:
        ctrl = self.ctrl
        if key not in ctrl.sources:
            raise HomeAssistantError(f"Unknown source: {key}")
        if follow:
            hub = ctrl.hub
            if hub is None or key != "audio" or not ctrl.follow_capable:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="follow_wake",
                    translation_placeholders={"room": ctrl.entry.title},
                )
        if self.active:
            await self.async_finish(REASON_REPLACED)
        if ctrl.sleep.active:
            await ctrl.sleep.async_cancel()  # a sleep timer would end the wake
        player = ctrl.sources[key].player
        out = ctrl.vol(ctrl.sources[key])
        if not ctrl.room_on:
            ctrl.volume.async_power_on()  # the wake turns the room on: its power-on preset
        target = target_volume if target_volume is not None else ctrl.volume.value
        if target is None:
            target = out.level() or 0.25
        # the wake owns the player from here on: no room volume, no night fade back,
        # no sleep timer restore on it
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
            "requested_at": None,
            "playing_since": None,
            "fade_t0": None,
            "snooze_until": None,
            "user_volume": False,
            "follow": "" if follow else None,  # the follow-me player once joined
            "seen": False,
        }
        if ctrl.data.get("volume_restore", {}).pop(out.id, None) is not None:
            ctrl._save()
        if night_mode is not None:
            await ctrl.night.async_set(night_mode)
        ctrl.night.async_cancel_ramp(out.id)
        self._changed(now=True)
        _LOGGER.info("%s: wake up on %s", ctrl.entry.title, player)
        self._spawn_start()

    @callback
    def _spawn_start(self) -> None:
        w = self._w
        if w is None or self._task_running:
            return
        # background: it may wait for a slow player (like the power sequence)
        self._task = self.hass.async_create_background_task(
            self._start(w), f"{DOMAIN}_{self.ctrl.entry.entry_id}_wake", eager_start=False
        )

    @callback
    def _cancel_task(self) -> None:
        task = self._task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        self._task = None

    def _snapshot(self, src: Source) -> tuple:
        state = self.hass.states.get(src.player)
        if state is None:
            return (None, None, None)
        return (state.state, state.attributes.get("media_content_id"), state.attributes.get("media_title"))

    async def _start(self, w: dict[str, Any]) -> None:
        """(Re)start the playback at the start volume; the play check follows.

        Acts only on its own wake: a newer wake, a snooze or an end in the
        meantime makes it stop."""
        ctrl = self.ctrl
        src = ctrl.sources[w["source"]]
        ctrl.source_watch.note_request(w["source"])  # a wake is a wish for its source
        w["phase"] = PHASE_STARTING
        w["started_at"] = _now()
        w["seen_new"] = False
        self._changed()
        # safety net: also a start that hangs ends in a failed wake
        self._schedule(_now() + float(ctrl.opts.get(CONF_START_TIMEOUT, 90)) + PLAY_TIMEOUT)
        resume = w.pop("resume", False) and ctrl.player_state(src) == "paused"
        self._before = self._snapshot(src)
        try:
            if w.get("follow") is not None:
                await self._join_follow(w, src)
            elif resume:
                await self._set_volume(src, ctrl.night.limit(w["start_volume"]))
                await ctrl._player(src, "media_play")
            else:
                # the room volume step in async_play_on gives the start volume
                await ctrl.async_play_on(w["source"], dict(w["data"]), room_mode=w.get("room_mode"))
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - any failure of the start is a failed wake
            _LOGGER.warning("%s: wake start failed: %s", ctrl.entry.title, err)
            await self._fail(w, FAILED_START)
            return
        if self._w is not w or w["phase"] != PHASE_STARTING:
            return  # ended, replaced or snoozed meanwhile
        w["requested_at"] = _now()
        self._schedule(w["requested_at"] + PLAY_TIMEOUT)
        if w.get("seen_new") or (ctrl.source_playing(src) and self._before[0] not in _PLAYING):
            await self._playing(w)

    async def _join_follow(self, w: dict[str, Any], src: Source) -> None:
        """Follow wake: the room joins the follow-me player at its start volume."""
        hub = self.ctrl.hub
        if hub is None:
            raise HomeAssistantError("central settings are gone")
        start = self.ctrl.night.limit(w["start_volume"])
        join_volume: float | None = start
        if self.ctrl.volume_device is not None:
            # the device fades; the player joins at follow me's volume
            await self._set_volume(src, start)
            join_volume = None
        if w.get("follow") and w.get("started_once") and w.get("playing_since"):
            # snooze over (or a restart): back into the group
            w["follow"] = await hub.async_wake_resume(self.ctrl, join_volume)
        else:
            data = w.get("data") or {}
            media = {k: data[k] for k in ("media_content_id", "media_content_type") if data.get(k)}
            w["follow"] = await hub.async_wake_start(self.ctrl, media, join_volume)
            w["started_once"] = True
        self.ctrl.data.setdefault("last_source", "audio")

    async def _set_volume(self, src: Source, volume: float) -> None:
        out = self.ctrl.vol(src)
        if out.can_set:
            await out.async_set(volume)

    async def _playing(self, w: dict[str, Any]) -> None:
        """It plays: fade in, arm the end."""
        ctrl = self.ctrl
        if self._w is not w or w["phase"] != PHASE_STARTING:
            return
        src = ctrl.sources[w["source"]]
        out = ctrl.vol(src)
        ctrl.volume.async_cancel_apply(out.id)  # this start's volume is the wake's
        w["playing_since"] = _now()
        w["fade_t0"] = _now()
        w["user_volume"] = False
        w["phase"] = PHASE_FADING if w["fade_s"] > 0 else PHASE_PLAYING
        self._changed()
        self._fire(EVENT_WAKE_STARTED, {})
        target = ctrl.night.limit(w["target_volume"])
        if w["fade_s"] > 0:
            current = out.level()
            start = ctrl.night.limit(w["start_volume"])
            if current is not None and current > start + out.tolerance:
                await self._set_volume(src, start)
            self._fade(src, target, w["fade_s"])
        else:
            await self._set_volume(src, target)
        self._arm_end()

    @callback
    def _fade(self, src: Source, target: float, seconds: float) -> None:
        out = self.ctrl.vol(src)
        if self._ramp is None or self._ramp.entity_id != out.id:
            if self._ramp is not None:
                self._ramp.stop()
            self._ramp = VolumeRamp(self.hass, out, self.ctrl._spawn, self._fade_done)
        if not self._ramp.start(target, seconds):
            if out.is_device and out.level() is None:
                return  # the volume device is not up yet: the fade starts when it is
            self._fade_done()

    @callback
    def async_output_ready(self) -> None:
        """The room's volume device is up (its power came on during the wake):
        the fade goes on from where it would be now; after the fade the device
        gets the wake's volume (unless someone changed it)."""
        w, src = self._w, self._src()
        if w is None or src is None:
            return
        if w["phase"] == PHASE_PLAYING and not w.get("user_volume"):
            self.ctrl._spawn(self._set_volume(src, self.ctrl.night.limit(w["target_volume"])))
            return
        if w["phase"] != PHASE_FADING or not w.get("fade_t0"):
            return
        if self._ramp is not None and self._ramp.running:
            return
        self.ctrl._spawn(self._fade_from_now(w, src))

    async def _fade_from_now(self, w: dict[str, Any], src: Source) -> None:
        night = self.ctrl.night
        start, target = night.limit(w["start_volume"]), night.limit(w["target_volume"])
        left = max(0.0, w["fade_t0"] + w["fade_s"] - _now())
        progress = 1 - left / w["fade_s"] if w["fade_s"] else 1
        await self._set_volume(src, round(start + (target - start) * progress, 3))
        if self._w is w and w["phase"] == PHASE_FADING:
            self._fade(src, target, left)

    @callback
    def _fade_done(self) -> None:
        """Faded in, or someone changed the volume: from now on it just plays."""
        w = self._w
        if w is not None and w["phase"] == PHASE_FADING:
            w["phase"] = PHASE_PLAYING
            if self._ramp is not None and self._ramp.aborted:
                w["user_volume"] = True
            self._changed()

    @callback
    def _arm_end(self) -> None:
        w = self._w
        if w and w.get("max_s") and w.get("playing_since"):
            self._schedule(w["playing_since"] + w["max_s"])
        else:
            self._unschedule()

    async def _fail(self, w: dict[str, Any], reason: str) -> None:
        if self._w is not w:
            return  # an older wake's start: the current one is not affected
        self._clear()
        self._fire(EVENT_WAKE_FAILED, {"reason": reason})
        if w.get("room_was_off"):
            await self.ctrl.async_turn_off()

    @callback
    def async_night_changed(self) -> None:
        """The night mode's limit changed: the fade aims at the new target."""
        w = self._w
        src = self._src()
        if w is None or src is None:
            return
        target = self.ctrl.night.limit(w["target_volume"])
        if w["phase"] == PHASE_FADING and w.get("fade_t0"):
            left = w["fade_t0"] + w["fade_s"] - _now()
            self._fade(src, target, max(left, 0))
        elif w["phase"] == PHASE_PLAYING and not w.get("user_volume"):
            out = self.ctrl.vol(src)
            current = out.level()
            if current is not None and current < target - out.tolerance:
                w["phase"] = PHASE_FADING
                self._fade(src, target, float(self.ctrl.opts.get(CONF_NIGHT_FADE_IN, 0)) * 60)

    # ---------------------------------------------------------------- events

    @callback
    def async_on_state(self, src: Source, old: State | None, new: State | None) -> None:
        w = self._w
        if w is None or new is None or old is None or src.key != w["source"]:
            return
        phase = w["phase"]
        if phase == PHASE_STARTING:
            if new.state in _PLAYING and (
                old.state not in _PLAYING
                or old.attributes.get("media_content_id") != new.attributes.get("media_content_id")
                or old.attributes.get("media_title") != new.attributes.get("media_title")
            ):
                w["seen_new"] = True  # the new playback runs
                if w.get("requested_at") is not None:
                    self.ctrl._spawn(self._playing(w))
        elif phase in (PHASE_FADING, PHASE_PLAYING):
            if new.state == "paused" and old.state in _PLAYING:
                _LOGGER.info("%s: paused during the wake: snooze", self.ctrl.entry.title)
                self.ctrl._spawn(self.async_snooze(None))
            elif new.state not in _PLAYING and new.state != "paused" and old.state in _PLAYING:
                self._schedule_stop_check(w)
            elif new.state in _PLAYING:
                self._cancel_stop_check()

    @callback
    def _schedule_stop_check(self, w: dict[str, Any]) -> None:
        """Stopped? Only if it stays so for a moment (not between two items)."""
        if self._stop_check is not None:
            return

        @callback
        def _due(_now_dt) -> None:
            self._stop_check = None
            src = self._src()
            if self._w is not w or src is None or self.ctrl.player_state(src) in (*_PLAYING, "paused"):
                return
            hub = self.ctrl.hub
            if w.get("follow") and hub is not None:
                if src.player not in hub.group_members(hub.follow_player):
                    hub.async_schedule("wake_dropped")
                    return  # dropped from its group: follow me takes it back in
                if hub.group_state(hub.follow_player) in _PLAYING:
                    return
            self.ctrl._spawn(self.async_finish(REASON_STOPPED))

        self._stop_check = async_track_point_in_utc_time(
            self.hass, _due, dt_util.utc_from_timestamp(_now() + STOP_SETTLE)
        )

    @callback
    def _cancel_stop_check(self) -> None:
        if self._stop_check is not None:
            self._stop_check()
            self._stop_check = None

    # ---------------------------------------------------------------- snooze

    async def async_snooze(self, minutes: float | None) -> None:
        w = self._w
        if w is None or w["phase"] == PHASE_SNOOZED:
            _LOGGER.debug("%s: snooze without a running wake: nothing to do", self.ctrl.entry.title)
            return
        ctrl = self.ctrl
        src = ctrl.sources[w["source"]]
        minutes = float(ctrl.opts.get(CONF_SNOOZE_MINUTES, 9)) if minutes is None else float(minutes)
        starting = w["phase"] == PHASE_STARTING
        self._cancel_task()
        self._cancel_stop_check()
        if self._ramp is not None:
            self._ramp.stop()
        w["phase"] = PHASE_SNOOZED
        w["snooze_until"] = _now() + minutes * 60
        can_pause = ctrl.player_supports(src, MediaPlayerEntityFeature.PAUSE)
        # a start that was still under way is done again from the beginning
        w["resume"] = can_pause and not starting
        self._changed(now=True)
        self._schedule(w["snooze_until"])
        if w.get("follow") is not None:
            # only this room snoozes: it leaves the group, the others play on
            if (hub := ctrl.hub) is not None:
                await hub.async_wake_snooze(ctrl)
        elif ctrl.source_playing(src):
            if can_pause:
                await ctrl._player(src, "media_pause")
            elif ctrl.player_supports(src, MediaPlayerEntityFeature.STOP):
                await ctrl._player(src, "media_stop")
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
        self._cancel_stop_check()
        self._cancel_task()
        if self._ramp is not None:
            self._ramp.stop()
        self.ctrl.data.pop("wake", None)
        self._changed(now=True)

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
        ctrl = self.ctrl
        now = _now()
        phase = w["phase"]
        if phase == PHASE_STARTING:
            src = ctrl.sources[w["source"]]
            if self._task_running:
                # the start hangs (beyond start timeout + play check)
                self._cancel_task()
                await self._fail(w, FAILED_START)
            elif w.get("requested_at") is not None and now - w["requested_at"] >= PLAY_TIMEOUT - 0.5:
                if ctrl.source_playing(src):
                    await self._playing(w)  # plays (the same media as before, maybe)
                else:
                    await self._fail(w, FAILED_NOT_PLAYING)
            else:
                self._schedule((w.get("requested_at") or now) + PLAY_TIMEOUT)
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
                return
        now = _now()
        phase = w["phase"]
        src = self._src()
        if src is None:
            self._clear()
            return
        if phase == PHASE_STARTING:
            if self.ctrl.source_playing(src) and w.get("requested_at") is not None:
                await self._playing(w)  # it got going before the restart
            elif now - w["started_at"] < RESTART_RETRY:
                self._spawn_start()
            else:
                await self._fail(w, FAILED_START)
        elif phase == PHASE_SNOOZED:
            self._schedule(w["snooze_until"])
        elif (
            w.get("follow")
            and not self.ctrl.groups_of(src.player)
            and self.ctrl.hub is not None
            and self.ctrl.hub.group_state(self.ctrl.hub.follow_player) not in (None, "unavailable", "unknown")
        ):
            # a follow wake whose group stopped while HA was down: join again
            self._spawn_start()
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
        self._cancel_task()
        self._unschedule()
        self._cancel_stop_check()
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
