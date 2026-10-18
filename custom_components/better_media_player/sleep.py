"""Sleep timer of a room.

The room turns itself off at a set time. Over the last minutes the volume of
the active player is lowered step by step; at the end the normal "room off"
runs (exactly once) and the volume from before the fade is set again, so the
next playback does not start at zero.

State lives in controller.data (persisted), so the timer survives a restart:

    data["sleep"] = {
        "ends_at": <utc timestamp>,
        "fade": <seconds>,
        "fade_player": <entity id> | None,   # set when the fade begins
        "fade_from": <volume 0..1> | None,   # volume before the fade
        "last_set": <volume 0..1> | None,    # last volume we set
        "fade_aborted": bool,                # someone changed the volume
    }
    data["volume_restore"] = {<entity id>: <volume>}   # restores still to do

A volume change from outside during the fade ends the fade only (that volume
stays, nothing is restored); the timer still ends the room.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .const import CONF_SLEEP_FADE, DOMAIN, EVENT_SLEEP_TIMER_FINISHED

if TYPE_CHECKING:
    from .controller import RoomController, Source

_LOGGER = logging.getLogger(__name__)

FADE_STEP_SECONDS = 10
VOLUME_TOLERANCE = 0.02  # players round (Music Assistant: whole percent)
MAX_MINUTES = 720

REASON_EXPIRED = "expired"
REASON_CANCELLED = "cancelled"
REASON_ROOM_OFF = "room_off"

_PLAYING = {"playing", "buffering"}
_POWERED = {"on", "idle", "playing", "paused", "buffering"}


def _now() -> float:
    return dt_util.utcnow().timestamp()


class SleepTimer:
    """One per room; driven by a single scheduled callback."""

    def __init__(self, ctrl: RoomController) -> None:
        self.ctrl = ctrl
        self.hass = ctrl.hass
        self._unsub: CALLBACK_TYPE | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ state

    @property
    def _s(self) -> dict[str, Any] | None:
        return self.ctrl.data.get("sleep")

    @property
    def active(self) -> bool:
        return self._s is not None

    @property
    def ends_at(self) -> datetime | None:
        s = self._s
        return dt_util.utc_from_timestamp(s["ends_at"]) if s else None

    @property
    def remaining_minutes(self) -> int:
        s = self._s
        if not s:
            return 0
        return max(0, math.ceil((s["ends_at"] - _now()) / 60))

    @property
    def fading(self) -> bool:
        s = self._s
        return bool(s and s.get("fade_from") is not None and not s.get("fade_aborted"))

    @property
    def default_fade_minutes(self) -> float:
        return float(self.ctrl.opts.get(CONF_SLEEP_FADE, 0))

    @property
    def _restores(self) -> dict[str, float]:
        return self.ctrl.data.setdefault("volume_restore", {})

    # -------------------------------------------------------------- lifecycle

    @callback
    def async_start(self) -> None:
        """After loading the stored data; acting waits until HA has started."""
        if self.active:
            self._schedule()

    @callback
    def async_started(self) -> None:
        """HA is up: catch up (e.g. the end passed while HA was down)."""
        if self.active:
            self.ctrl._spawn(self._async_step())

    @callback
    def async_stop(self) -> None:
        self._unschedule()

    # ---------------------------------------------------------------- actions

    async def async_set(self, minutes: float, fade_minutes: float | None = None) -> None:
        """Start or replace the timer; minutes <= 0 cancels it."""
        if minutes <= 0:
            await self.async_cancel(REASON_CANCELLED)
            return
        minutes = min(float(minutes), MAX_MINUTES)
        fade = self.default_fade_minutes if fade_minutes is None else float(fade_minutes)
        fade = max(0.0, min(fade, minutes))
        async with self._lock:
            await self._end_fade(restore=True)
            self.ctrl.data["sleep"] = {
                "ends_at": _now() + minutes * 60,
                "fade": fade * 60,
                "fade_player": None,
                "fade_from": None,
                "last_set": None,
                "fade_aborted": False,
            }
            self._changed()
        self.ctrl._spawn(self._async_step())

    async def async_extend(self, minutes: float = 15) -> None:
        """+N minutes; starts a timer of N minutes if none runs."""
        s = self._s
        if s is None:
            await self.async_set(minutes)
            return
        async with self._lock:
            await self._end_fade(restore=True)
            s = self._s
            if s is None:  # cancelled meanwhile
                return
            ends = max(s["ends_at"], _now()) + minutes * 60
            s["ends_at"] = min(ends, _now() + MAX_MINUTES * 60)
            s["fade"] = min(s["fade"], s["ends_at"] - _now())
            self._changed()
        self.ctrl._spawn(self._async_step())

    async def async_cancel(self, reason: str = REASON_CANCELLED) -> None:
        if not self.active:
            return
        async with self._lock:
            if not self.active:
                return
            await self._end_fade(restore=True)
            self.ctrl.data.pop("sleep", None)
            self._unschedule()
            self._changed()
        self._fire(reason)

    # ------------------------------------------------------------ scheduling

    @callback
    def _unschedule(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _schedule(self) -> None:
        """One pending callback: fade start, next fade step, or the end."""
        self._unschedule()
        s = self._s
        if s is None or self.ctrl._stopped:
            return
        now = _now()
        fade_start = s["ends_at"] - s["fade"]
        if now < fade_start:
            at = fade_start
        elif s["fade"] > 0 and not s.get("fade_aborted"):
            at = min(now + FADE_STEP_SECONDS, s["ends_at"])
        else:
            at = s["ends_at"]
        self._unsub = async_track_point_in_utc_time(
            self.hass, self._on_due, dt_util.utc_from_timestamp(max(at, now))
        )

    @callback
    def _on_due(self, _now_dt: datetime) -> None:
        self._unsub = None
        self.ctrl._spawn(self._async_step())

    @callback
    def _changed(self) -> None:
        self.ctrl._save()
        self.ctrl._notify()

    # ----------------------------------------------------------------- steps

    async def _async_step(self) -> None:
        if not self.ctrl._started or self.ctrl._stopped:
            return  # async_started() catches up
        finish = False
        async with self._lock:
            s = self._s
            if s is None:
                return
            now = _now()
            if now >= s["ends_at"]:
                finish = True
            elif s["fade"] > 0 and now >= s["ends_at"] - s["fade"] and not s.get("fade_aborted"):
                await self._fade_step(s, now)
            if not finish:
                self._schedule()
        if finish:
            await self._finish()

    def _fade_source(self) -> Source | None:
        src = self.ctrl.active_source
        if (
            src is None
            or src.suppressed
            or self.ctrl.player_state(src) not in _POWERED
            or not self.ctrl.player_supports(src, MediaPlayerEntityFeature.VOLUME_SET)
        ):
            return None
        return src

    def _volume(self, player: str) -> float | None:
        state = self.hass.states.get(player)
        if state is None:
            return None
        value = state.attributes.get("volume_level")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    async def _fade_step(self, s: dict[str, Any], now: float) -> None:
        src = self._fade_source()
        if s.get("fade_player") is not None and (src is None or src.player != s["fade_player"]):
            if src is not None:
                # another player took over: the old one gets its volume back
                await self._end_fade(restore=True)
            return  # nothing to fade right now (e.g. paused into standby)
        if src is None:
            return
        current = self._volume(src.player)
        if current is None:
            return
        if s.get("fade_player") is None:
            start = self._restores.pop(src.player, None)
            s["fade_player"] = src.player
            s["fade_from"] = start if start is not None else current
            s["last_set"] = current
            self.ctrl._save()
        elif s.get("last_set") is not None and abs(current - s["last_set"]) > VOLUME_TOLERANCE:
            _LOGGER.info(
                "%s: volume changed during the sleep fade, keeping %.2f", self.ctrl.entry.title, current
            )
            s["fade_aborted"] = True
            s["fade_from"] = None
            s["last_set"] = None
            self._changed()
            return
        left = max(0.0, s["ends_at"] - now)
        target = round(s["fade_from"] * min(1.0, left / s["fade"]), 3)
        if target < current - 0.004:
            s["last_set"] = target
            self.ctrl._save()
            await self.ctrl._player(src, "volume_set", {"volume_level": target})
        self.ctrl._notify()

    async def _finish(self) -> None:
        s = self._s
        if s is None:
            return
        # clear first: nothing below may start a second shutdown
        self.ctrl.data.pop("sleep", None)
        self._unschedule()
        self._changed()
        if any(self.ctrl.source_playing(x) for x in self.ctrl.sources.values()) or (
            self.ctrl.room_on and not self._expired_while_down(s)
        ):
            _LOGGER.info("%s: sleep timer expired, turning the room off", self.ctrl.entry.title)
            await self.ctrl.async_turn_off()
        if s.get("fade_from") is not None and not s.get("fade_aborted") and s.get("fade_player"):
            self._restores[s["fade_player"]] = s["fade_from"]
            self.ctrl._save()
        await self.async_apply_restores()
        self._fire(REASON_EXPIRED)

    @staticmethod
    def _expired_while_down(s: dict[str, Any]) -> bool:
        """The end lies more than a step in the past: HA was not running then."""
        return _now() - s["ends_at"] > 2 * FADE_STEP_SECONDS

    async def _end_fade(self, restore: bool) -> None:
        """Stop a running fade; with restore, the volume from before comes back."""
        s = self._s
        if s is None or s.get("fade_player") is None:
            return
        player, start = s["fade_player"], s.get("fade_from")
        aborted = s.get("fade_aborted")
        s.update(fade_player=None, fade_from=None, last_set=None, fade_aborted=False)
        if restore and start is not None and not aborted:
            self._restores[player] = start
            await self.async_apply_restores()
        self.ctrl._save()

    # --------------------------------------------------------------- restore

    async def async_apply_restores(self) -> None:
        """Set volumes back once the player can take it.

        Not while it still plays after "room off" (its stop is on the way): it
        would be loud for a moment. A fresh playback, or a stopped or paused
        player, gets its volume right away; a player that is off gets it when
        it is up again.
        """
        pending = self.ctrl.data.get("volume_restore")
        if not pending:
            return
        for player, volume in list(pending.items()):
            src = next((x for x in self.ctrl.sources.values() if x.player == player), None)
            if src is None:
                pending.pop(player, None)
                continue
            state = self.ctrl.player_state(src)
            if state not in _POWERED:
                continue
            if state in _PLAYING and src.suppressed:
                continue
            if pending.pop(player, None) is None:
                continue  # done meanwhile by a concurrent call
            self.ctrl._save()
            if self.ctrl.player_supports(src, MediaPlayerEntityFeature.VOLUME_SET):
                await self.ctrl._player(src, "volume_set", {"volume_level": volume})

    # ----------------------------------------------------------------- event

    @callback
    def _fire(self, reason: str) -> None:
        entry = self.ctrl.entry
        entity_id = er.async_get(self.hass).async_get_entity_id(
            "media_player", DOMAIN, f"{entry.entry_id}_player"
        )
        self.hass.bus.async_fire(
            EVENT_SLEEP_TIMER_FINISHED,
            {"entity_id": entity_id, "entry_id": entry.entry_id, "room": entry.title, "reason": reason},
        )
