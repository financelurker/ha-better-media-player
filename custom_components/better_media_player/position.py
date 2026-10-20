"""The playback position goes along when a queue moves to another player.

Music Assistant's queue transfer starts the current track on the target, but
not always where it was (from a player that has just joined the group it
starts from the beginning). The position is read before the move; once the
target plays the same track, it is set there if it is clearly behind.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

BEHIND = 3.0  # s the target may be behind before the position is set
WAIT = 20.0  # s for the target to play the track
END = 5.0  # s before the end of a track: not worth setting


def _track(state: State) -> str | None:
    return state.attributes.get("media_content_id") or state.attributes.get("media_title")


def _updated(state: State | None) -> datetime | None:
    """When the player last reported its position (by the clock of its server)."""
    updated = state.attributes.get("media_position_updated_at") if state is not None else None
    if isinstance(updated, str):
        updated = dt_util.parse_datetime(updated)
    return updated if isinstance(updated, datetime) else None


def _seconds(state: State) -> float | None:
    """The live position of a player (its last report plus the time since, while playing)."""
    position = state.attributes.get("media_position")
    if position is None:
        return None
    seconds = float(position)
    if state.state == "playing" and (updated := _updated(state)) is not None:
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


def last_report(hass: HomeAssistant, entity_id: str) -> datetime | None:
    return _updated(hass.states.get(entity_id))


@callback
def keep_position(
    hass: HomeAssistant,
    target: str,
    position: Position,
    seek: Callable[[float], None],
    reported: datetime | None,
) -> CALLBACK_TYPE:
    """Watch target until it plays the track (a position report newer than
    reported, its last one before the move: the same clock, the server's); if
    it is clearly behind there, seek(seconds) is called. Gives up after WAIT s,
    with another track, near the end of the track or for a stream without a
    duration (radio). Returns the cancel."""
    unsubs: list[CALLBACK_TYPE] = []

    @callback
    def _done() -> None:
        while unsubs:
            unsubs.pop()()

    @callback
    def _check(state: State | None) -> None:
        if state is None or state.state != "playing":
            return
        updated = _updated(state)
        if updated is None or (reported is not None and updated <= reported):
            return  # not yet reported since the move
        _done()
        if _track(state) != position.track:
            return  # another track by now: nothing to keep
        now = _seconds(state)
        duration = state.attributes.get("media_duration")
        if now is None or position.seconds - now <= BEHIND:
            return
        if not duration or position.seconds >= float(duration) - END:
            return
        seek(round(position.seconds, 1))

    @callback
    def _changed(event: Event[EventStateChangedData]) -> None:
        _check(event.data["new_state"])

    unsubs.append(async_track_state_change_event(hass, [target], _changed))
    unsubs.append(async_call_later(hass, WAIT, lambda _now: _done()))
    _check(hass.states.get(target))
    return _done
