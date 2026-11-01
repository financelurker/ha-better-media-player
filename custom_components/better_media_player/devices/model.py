"""What the device layer switches: nodes, how they are cabled, and the settings
their owner gives them."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class Signal(StrEnum):
    """What a connection carries."""

    PICTURE = "picture"
    SOUND = "sound"


class Mode(StrEnum):
    """Who does something: BMP sends the command, someone else does it (HDMI-CEC:
    BMP expects the effect), or nobody (BMP leaves it alone)."""

    BMP = "bmp"
    CEC = "cec"
    NEVER = "never"


@dataclass(frozen=True)
class Control:
    """How a node's devices are switched on and off."""

    on: Mode = Mode.BMP
    off: Mode = Mode.BMP
    fallback: bool = False  # CEC: BMP sends the command itself if the effect does not come
    timeout: float = 15.0  # s the effect of CEC is expected


@dataclass(frozen=True)
class Inputs:
    """How a node's input is chosen: select_source on an entity, or an action per input."""

    entity: str | None = None
    actions: Mapping[str, str] = field(default_factory=dict)  # input -> script, button, …
    pause: float = 0.0  # s after choosing an input (IR: before the next command)


@dataclass(frozen=True)
class Edge:
    """source feeds target with signals, on target's input (None: nothing to choose)."""

    source: str
    target: str
    signals: frozenset[Signal]
    input: str | None = None
    mode: Mode = Mode.BMP  # who chooses the input


@dataclass
class Node:
    """Power devices with one job (a room's display, its speakers, a player's plug).

    entities  plugs (switched power), devices (turn themselves on/off: a TV or AV
              receiver as media player, a remote) and the node's own on/off actions
              (virtual ids, one more device with an assumed state)
    sources   the sources (paths) the node is on: switching off, a node waits
              while an earlier node of the same source is still on
    gives     what it puts out (a display: picture; speakers: sound)
    """

    key: str
    entities: list[str]
    sources: tuple[str, ...]
    power: list[str] | None = None  # power sensors (wait for standby)
    actions: dict[str, tuple[str, str]] = field(default_factory=dict)  # own on/off actions per virtual id
    gives: frozenset[Signal] = frozenset()
    inputs: Inputs | None = None
    control: Control = field(default_factory=Control)
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
