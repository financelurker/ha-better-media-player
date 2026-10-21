"""Sleep timer of a room.

The room turns itself off at a set time. Over the last minutes the volume of
the active player (or of the room's volume device) is lowered step by step; at the end the normal "room off"
runs (exactly once) and the volume from before the fade is set again, so the
next playback does not start at zero.

State lives in controller.data (persisted), so the timer survives a restart:

    data["sleep"] = {
        "ends_at": <utc timestamp>,
        "fade": <seconds>,
        "fade_player": <entity id> | None,   # the faded output, set when the fade begins
        "fade_from": <volume 0..1> | None,   # volume before the fade
        "recent": [<volume 0..1>, ...],      # last volumes we set (late reports)
        "fade_aborted": bool,                # someone changed the volume
    }
    data["volume_restore"] = {<entity id>: {"volume": v, "left": x}}
        restores still to do; "left" is the volume the fade left behind; if
        someone set another volume meanwhile, the restore is dropped

A volume change from outside during the fade ends the fade only (that volume
stays, nothing is restored); the timer still ends the room.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .const import CONF_SLEEP_FADE, DOMAIN, EVENT_SLEEP_TIMER_FINISHED

if TYPE_CHECKING:
    from .controller import RoomController

_LOGGER = logging.getLogger(__name__)

FADE_STEP_SECONDS = 10
VOLUME_TOLERANCE = 0.011  # players round to whole percent
RECENT_SETS = 4  # a report may come a few steps late (polled players)
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

    def fading_player(self, player: str | None) -> bool:
        """This player's volume is being lowered right now (not its real volume)."""
        s = self._s
        return bool(player and self.fading and s.get("fade_player") == player)

    @callback
    def limit_fade(self, player: str, cap: float) -> None:
        """The night mode limits this player: the fade goes on below the limit
        (and the limit's own set is not taken for a change from outside)."""
        s = self._s
        if not self.fading_player(player):
            return
        if s["fade_from"] > cap:
            s["fade_from"] = cap
        s["recent"] = [*s.get("recent", []), cap][-RECENT_SETS:]
        self._save()

    @property
    def fading(self) -> bool:
        s = self._s
        return bool(s and s.get("fade_from") is not None and not s.get("fade_aborted"))

    @property
    def default_fade_minutes(self) -> float:
        return float(self.ctrl.setting(CONF_SLEEP_FADE))

    @property
    def _restores(self) -> dict[str, dict[str, float | None]]:
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
        await self.ctrl.wake.async_finish("sleep_timer")  # playback goes on, the timer ends it
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
                "recent": [],
                "fade_aborted": False,
            }
            self._changed(now=True)
        self.ctrl._spawn(self._async_step())

    async def async_extend(self, minutes: float = 15) -> None:
        """+N minutes; starts a timer of N minutes if none runs."""
        s = self._s
        if s is None:
            await self.async_set(minutes)
            return
        await self.ctrl.wake.async_finish("sleep_timer")
        async with self._lock:
            await self._end_fade(restore=True)
            s = self._s
            if s is None:  # cancelled meanwhile
                return
            ends = max(s["ends_at"], _now()) + minutes * 60
            s["ends_at"] = min(ends, _now() + MAX_MINUTES * 60)
            s["fade"] = min(s["fade"], s["ends_at"] - _now())
            self._changed(now=True)
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
    def _changed(self, now: bool = False) -> None:
        self._save(now)
        self.ctrl._notify()

    @callback
    def _save(self, now: bool = False) -> None:
        """Fade steps come more often than the delayed save: important points go out at once."""
        if now:
            self.ctrl._save_now()
        else:
            self.ctrl._save()

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

    def _fade_output(self):
        """The output to fade: the active player's, or the volume device."""
        src = self.ctrl.active_source
        if src is None or src.suppressed or self.ctrl.player_state(src) not in _POWERED:
            return None
        out = self.ctrl.vol(src)
        return out if out.can_set else None

    def _level(self, output_id: str | None) -> float | None:
        out, _src = self.ctrl.output(output_id)
        return out.level() if out is not None else None

    def _tolerance(self, output_id: str | None) -> float:
        out, _src = self.ctrl.output(output_id)
        return out.tolerance if out is not None else VOLUME_TOLERANCE

    async def _fade_step(self, s: dict[str, Any], now: float) -> None:
        out = self._fade_output()
        if s.get("fade_player") is not None and (out is None or out.id != s["fade_player"]):
            if out is not None:
                # another player took over: the old one gets its volume back
                await self._end_fade(restore=True)
            return  # nothing to fade right now (e.g. paused into standby)
        if out is None:
            return
        current = out.level()
        if current is None:
            return
        if s.get("fade_player") is None:
            pending = self._restores.pop(out.id, None)
            unchanged = pending and self._unchanged(current, pending.get("left"), out.tolerance)
            start = pending["volume"] if unchanged else None
            s["fade_player"] = out.id
            # a preset fade's last steps may still be reported: they are no change
            preset_steps = self.ctrl.volume.recent_sets(out.id)
            self.ctrl.volume.async_cancel_fade(out.id)
            s["fade_from"] = start if start is not None else current
            s["recent"] = [current, *preset_steps]
            self._save(now=True)
        elif self._external(s, current, out.tolerance):
            self._abort_fade(s, current)
            return
        left = max(0.0, s["ends_at"] - now)
        target = round(s["fade_from"] * min(1.0, left / s["fade"]), 3)
        if target < current - 0.004:
            s["recent"] = [*s.get("recent", []), target][-RECENT_SETS:]
            self._save()
            await out.async_set(target)
        self.ctrl._notify()

    @staticmethod
    def _unchanged(current: float | None, expected: float | None, tolerance: float = VOLUME_TOLERANCE) -> bool:
        return expected is None or current is None or abs(current - expected) <= tolerance

    @staticmethod
    def _external(s: dict[str, Any], current: float, tolerance: float = VOLUME_TOLERANCE) -> bool:
        """The volume is none of ours: someone else changed it.

        Our own sets may be reported late, so any of the last few values we
        set (plus rounding) still counts as ours.
        """
        recent = s.get("recent") or []
        if not recent:
            return False
        return all(abs(current - v) > tolerance for v in recent)

    def _check_external(self, s: dict[str, Any]) -> None:
        """Right before ending a fade: a change since the last step counts too."""
        if s.get("fade_player") is None or s.get("fade_aborted"):
            return
        current = self._level(s["fade_player"])
        if current is not None and self._external(s, current, self._tolerance(s["fade_player"])):
            self._abort_fade(s, current)

    def _abort_fade(self, s: dict[str, Any], current: float) -> None:
        _LOGGER.info("%s: volume changed during the sleep fade, keeping %.2f", self.ctrl.entry.title, current)
        s["fade_aborted"] = True
        s["fade_from"] = None
        s["recent"] = []
        self._changed(now=True)

    async def _finish(self) -> None:
        s = self._s
        if s is None:
            return
        self._check_external(s)
        # clear first: nothing below may start a second shutdown
        self.ctrl.data.pop("sleep", None)
        self._unschedule()
        self._changed()
        if self.ctrl.turning_off:
            pass  # a "room off" is running already
        elif any(self.ctrl.source_playing(x) for x in self.ctrl.sources.values()) or (
            self.ctrl.room_on and not self._expired_while_down(s)
        ):
            _LOGGER.info("%s: sleep timer expired, turning the room off", self.ctrl.entry.title)
            await self.ctrl.async_turn_off()
        if s.get("fade_from") is not None and not s.get("fade_aborted") and s.get("fade_player"):
            self._restores[s["fade_player"]] = {"volume": s["fade_from"], "left": self._left(s)}
            self._save(now=True)
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
        self._check_external(s)
        player, start = s["fade_player"], s.get("fade_from")
        aborted, left = s.get("fade_aborted"), self._left(s)
        s.update(fade_player=None, fade_from=None, recent=[], fade_aborted=False)
        if restore and start is not None and not aborted:
            self._restores[player] = {"volume": start, "left": left}
            await self.async_apply_restores()
        self._save(now=True)

    @staticmethod
    def _left(s: dict[str, Any]) -> float | None:
        recent = s.get("recent") or []
        return recent[-1] if recent else None

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
        for player, item in list(pending.items()):
            if not isinstance(item, dict):
                item = {"volume": item}
            out, src = self.ctrl.output(player)
            stale = False
            if out is None:
                # left from before the volume output changed (players <-> device):
                # a player still gets its own volume back, an old device nothing
                out, src = self.ctrl.player_output(player)
                stale = True
            if out is None:
                pending.pop(player, None)
                self.ctrl._save()
                continue
            if self.ctrl.wake.owns(player):
                pending.pop(player, None)  # the wake sets this output's volume
                self.ctrl._save()
                continue
            if src is not None:
                state = self.ctrl.player_state(src)
                if state not in _POWERED or (state in _PLAYING and src.suppressed):
                    continue
            elif not out.ready or any(
                self.ctrl.player_state(x) in _PLAYING and x.suppressed for x in self.ctrl.sources.values()
            ):
                continue  # the device is off, or a player is still on its way to stop
            if pending.pop(player, None) is None:
                continue  # done meanwhile by a concurrent call
            self.ctrl._save()
            if not self._unchanged(out.level(), item.get("left"), out.tolerance):
                continue  # someone set a volume meanwhile (e.g. follow me's join): it wins
            if out.can_set:
                # the room's volume is what the player would get next anyway
                room = None if stale else self.ctrl.volume.value
                target = room if room is not None else item["volume"]
                if stale:
                    await self.ctrl._call(
                        "media_player", "volume_set", {"entity_id": player, "volume_level": target}
                    )
                else:
                    await out.async_set(target)

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
