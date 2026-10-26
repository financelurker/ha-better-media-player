"""The room's side of follow me: its presence, its part and state in a session.

Follow me itself (central: which room joins when, the group's volume) is in
follow/; this is what a room knows and is asked about: its occupancy sensors,
whether it takes part, its state in the current session and its prewarm.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import STATE_ON
from homeassistant.core import callback

from ..const import CONF_OCCUPANCY, CONF_PRESENCE_RUN_DOWN, CONF_PREWARM, CONF_VOLUME_OFFSET, FOLLOW_INACTIVE
from ..follow import get_hub
from .power import NOT_AVAILABLE

if TYPE_CHECKING:
    from .controller import RoomController, Source


class RoomFollow:
    """Follow me as one room sees it."""

    def __init__(self, ctrl: RoomController) -> None:
        self.ctrl = ctrl
        self.occupancy: list[str] = list(ctrl.opts.get(CONF_OCCUPANCY) or [])
        self.prewarm = False
        self.state = FOLLOW_INACTIVE

    @property
    def capable(self) -> bool:
        """The room can take part: a music player and occupancy sensors."""
        return self.ctrl.audio_player is not None and bool(self.occupancy)

    @property
    def takes_part(self) -> bool:
        """The room takes part in follow me (follow me is set up)."""
        hub = get_hub(self.ctrl.hass)
        return self.capable and hub is not None and hub.follow_player is not None

    @property
    def tracked_entities(self) -> list[str]:
        out = list(self.occupancy)
        out += [s.player for s in self.ctrl.sources.values()]
        return out

    @property
    def prewarm_enabled(self) -> bool:
        return bool(self.ctrl.setting(CONF_PREWARM))

    @property
    def presence_run_down(self) -> float:
        return float(self.ctrl.setting(CONF_PRESENCE_RUN_DOWN)) * 60.0

    @property
    def volume_offset(self) -> float:
        return float(self.ctrl.setting(CONF_VOLUME_OFFSET))

    def presence(self) -> str:
        """'present', 'empty' or 'unknown' (all sensors unavailable)."""
        known = False
        for entity_id in self.occupancy:
            state = self.ctrl.hass.states.get(entity_id)
            if state is None or state.state in NOT_AVAILABLE:
                continue
            known = True
            if state.state == STATE_ON:
                return "present"
        return "empty" if known else "unknown"

    def group_counts(self, src: Source) -> bool:
        """Group playback counts, except follow me in a quiet room (unless it wakes there)."""
        ctrl = self.ctrl
        hub = get_hub(ctrl.hass)
        groups = src.backend.groups_of(src.player)
        if hub is None or not groups or not ctrl.quiet or ctrl.wake.follow_holding:
            return True
        return any(not hub.follows(g) for g in groups)

    @callback
    def set_state(self, state: str) -> None:
        if self.state != state:
            self.state = state
            self.ctrl._notify()

    @callback
    def set_prewarm(self, on: bool) -> None:
        if self.prewarm == on:
            return
        self.prewarm = on
        if self.ctrl._started:
            self.ctrl._spawn(self.ctrl.async_update("prewarm"))
