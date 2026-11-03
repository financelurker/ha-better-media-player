"""What a room is doing right now, for a view (the panel's live state).

room_live: the whole room (its players, each switched device, the room's
state); room_line: one line for the list of rooms. Times are epoch seconds
(with "now", so a view can count down against its own clock).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .. import clock
from ..devices.const import NOT_AVAILABLE, POWERED_STATES

if TYPE_CHECKING:
    from .controller import RoomController, Source


def _source(ctrl: RoomController, src: Source) -> dict[str, Any]:
    state = ctrl.player_state(src)
    seq = ctrl.sequence
    return {
        "player": src.player,
        "state": state,
        "available": state not in (None, *NOT_AVAILABLE),
        "on": state in POWERED_STATES,
        "playing": ctrl.source_active(src) and ctrl.source_playing(src),
        "in_use": bool(src.in_use),
        "since": src.hold_since,  # nothing plays since (while in use)
        "hold_end": ctrl.hold_end(src),
        "expired": src.expired,
        "going_off": ctrl.player_going_off(src),
        "starting": seq.running and seq.source == src.key,
    }


def room_live(ctrl: RoomController) -> dict[str, Any]:
    now = clock.now()
    power = ctrl.power
    devices = power.devices
    nodes = {}
    for key, node in power.nodes.items():
        live = devices.live(node, now)
        live.update(
            why=power.reasons(node),
            needed=power.needed(node),
            off_at=ctrl.run_down_at.get(key),
            night_off=not ctrl.night.enabled(node),
            waits_player=power.waits(node) and devices.node_on(node) and not power.needed(node),
        )
        nodes[key] = live
    return {
        "now": now,
        "status": ctrl.status,
        "error": ctrl.error,
        "keep_on": ctrl.keep_on,
        "manual": ctrl.manual,
        "auto_power": ctrl.auto_power,
        "night": ctrl.night.active,
        "quiet": ctrl.quiet,
        "idle_timeout": ctrl.idle_timeout(),
        "starting": ctrl.sequence.running,
        "start_source": ctrl.sequence.source if ctrl.sequence.running else None,
        "sources": {key: _source(ctrl, src) for key, src in ctrl.sources.items()},
        "nodes": nodes,
    }


def room_line(ctrl: RoomController) -> dict[str, Any]:
    """The room in one line: its status, what plays or holds, when it goes off."""
    playing = next((k for k, s in ctrl.sources.items() if ctrl.source_active(s) and ctrl.source_playing(s)), None)
    holding = next((k for k, s in ctrl.sources.items() if s.in_use and s.hold_since is not None), None)
    ends = [t for t in ctrl.run_down_at.values()]
    return {
        "status": ctrl.status,
        "playing": playing,
        "holding": None if playing else holding,
        "starting": ctrl.sequence.source if ctrl.sequence.running else None,
        "off_at": max(ends) if ends and not playing else None,
        "error": ctrl.error,
    }
