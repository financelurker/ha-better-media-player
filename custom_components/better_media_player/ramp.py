"""A volume ramp: moves one media player's volume linearly to a target.

Used to fade the volume back up when the night mode ends (and later for wake
up). One scheduled callback at a time; a volume change from outside during
the ramp stops it and keeps that volume. Our own steps may be reported late,
so a reported value among the last few we set still counts as ours.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

STEP_SECONDS = 5
TOLERANCE = 0.011
RECENT = 4


def _now() -> float:
    return dt_util.utcnow().timestamp()


class VolumeRamp:
    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        set_volume: Callable[[float], Awaitable[None]],
        spawn: Callable[[Awaitable[None]], None],
        on_done: Callable[[], None] | None = None,
    ) -> None:
        self._on_done = on_done
        self.hass = hass
        self.entity_id = entity_id
        self._set = set_volume
        self._spawn = spawn
        self._unsub: CALLBACK_TYPE | None = None
        self._start = 0.0
        self._target = 0.0
        self._t0 = 0.0
        self._duration = 0.0
        self._recent: list[float] = []
        self.running = False

    def _volume(self) -> float | None:
        state = self.hass.states.get(self.entity_id)
        value = state.attributes.get("volume_level") if state else None
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @callback
    def start(self, target: float, seconds: float) -> bool:
        """Ramp from the current volume to target; False if there is nothing to do."""
        self.running = False  # a new ramp replaces the old one (no "done")
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        current = self._volume()
        if current is None or abs(current - target) <= TOLERANCE:
            return False
        if seconds <= 0:
            self._spawn(self._set(target))
            return True
        self._start, self._target = current, target
        self._t0, self._duration = _now(), float(seconds)
        self._recent = [current]
        self.running = True
        self._schedule()
        return True

    @callback
    def stop(self) -> None:
        """Stop without "done" (shutdown: the owner resumes after a restart)."""
        self.running = False
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def cancel(self) -> None:
        was = self.running
        self.running = False
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if was and self._on_done is not None:
            self._on_done()

    @callback
    def _schedule(self) -> None:
        at = min(_now() + STEP_SECONDS, self._t0 + self._duration)
        self._unsub = async_track_point_in_utc_time(self.hass, self._due, dt_util.utc_from_timestamp(at))

    @callback
    def _due(self, _now_dt: datetime) -> None:
        self._unsub = None
        if self.running:
            self._spawn(self._step())

    async def _step(self) -> None:
        if not self.running:
            return
        current = self._volume()
        if current is None:
            self.cancel()  # player gone (e.g. turned off)
            return
        if all(abs(current - v) > TOLERANCE for v in self._recent):
            _LOGGER.info("%s: volume changed during the ramp, keeping %.2f", self.entity_id, current)
            self.cancel()
            return
        progress = min(1.0, (_now() - self._t0) / self._duration)
        value = round(self._start + (self._target - self._start) * progress, 3)
        done = progress >= 1.0
        if not done:
            self._schedule()
        if abs(value - current) > 0.004 or done:
            self._recent = [*self._recent, value][-RECENT:]
            await self._set(value)
        if done:
            self.running = False
            if self._on_done is not None:
                self._on_done()
