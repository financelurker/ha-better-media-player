"""What the device layer switches: nodes and the settings their owner gives them."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    """Power devices with one job (a room's display, its speakers, a player's plug).

    entities  plugs (switched power), devices (turn themselves on/off: a TV or AV
              receiver as media player, a remote) and the node's own on/off actions
              (virtual ids, one more device with an assumed state)
    sources   the sources (paths) the node is on: switching off, a node waits
              while an earlier node of the same source is still on
    """

    key: str
    entities: list[str]
    sources: tuple[str, ...]
    power: list[str] | None = None  # power sensors (wait for standby)
    actions: dict[str, tuple[str, str]] = field(default_factory=dict)  # own on/off actions per virtual id
    # runtime
    last_demand: float | None = None
    force_off: bool = False
    stop_since: float | None = None
    low_since: float | None = None
    devices_off_since: float | None = None  # its devices were told to go off


@dataclass(frozen=True)
class Standby:
    """Wait for standby before the node's plugs go off."""

    watts: float  # at or below: standby
    max_seconds: float  # the plugs go off after this at the latest
