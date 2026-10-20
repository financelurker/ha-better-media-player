"""The playback position goes along when a queue moves to another player.

Music Assistant's queue transfer starts the current track on the target, but
not always where it was (from a player that has just joined the group it
starts from the beginning). The position is read before the move; once the
target plays the same track, it is set there if it is clearly behind.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

BEHIND = 3.0  # s the target may be behind before the position is set
WAIT = 20.0  # s for the target to play the track
END = 5.0  # s before the end of a track: not worth setting


def _track(state: State) -> str | None:
    return state.attributes.get("media_content_id") or state.attributes.get("media_title")


def _seconds(state: State) -> float | None:
    """The live position of a player (its last report plus the time since, while playing)."""
    position = state.attributes.get("media_position")
    if position is None:
        return None
    seconds = float(position)
    if state.state == "playing":
        updated = state.attributes.get("media_position_updated_at")
        if isinstance(updated, str):
            updated = dt_util.parse_datetime(updated)
        if isinstance(updated, datetime):
            seconds += max(0.0, (dt_util.utcnow() - updated).total_seconds())
    return seconds


@dataclass(frozen=True)
class Position:
    track: str
    seconds: float

    @classmethod
    def of(cls, hass: HomeAssistant, entity_id: str) -> Position | None:
        """Where the player is in its current track (None: nothing to keep)."""
        state = hass.states.get(entity_id)
        if state is None or state.state not in ("playing", "paused"):
            return None
        track, seconds = _track(state), _seconds(state)
        if track is None or seconds is None:
            return None
        return cls(track, seconds)


@callback
def keep_position(
    hass: HomeAssistant,
    target: str,
    position: Position,
    since: datetime,
    seek: Callable[[float], Coroutine[Any, Any, Any]],
) -> CALLBACK_TYPE:
    """Watch target until it plays the track (reported after since); if it is
    clearly behind there, seek(seconds) is started. Gives up after WAIT s or
    with another track. Returns the cancel."""
    unsubs: list[CALLBACK_TYPE] = []

    @callback
    def _done() -> None:
        while unsubs:
            unsubs.pop()()

    @callback
    def _check(state: State | None) -> None:
        if state is None or state.state != "playing":
            return
        updated = state.attributes.get("media_position_updated_at")
        if isinstance(updated, str):
            updated = dt_util.parse_datetime(updated)
        if not isinstance(updated, datetime) or updated < since:
            return  # not yet reported since the move
        _done()
        if _track(state) != position.track:
            return  # another track by now: nothing to keep
        now = _seconds(state)
        duration = state.attributes.get("media_duration")
        if now is None or position.seconds - now <= BEHIND:
            return
        if duration is not None and position.seconds >= float(duration) - END:
            return
        hass.async_create_task(seek(round(position.seconds, 1)), eager_start=False)

    @callback
    def _changed(event: Event[EventStateChangedData]) -> None:
        _check(event.data["new_state"])

    unsubs.append(async_track_state_change_event(hass, [target], _changed))
    unsubs.append(async_call_later(hass, WAIT, lambda _now: _done()))
    _check(hass.states.get(target))
    return _done
