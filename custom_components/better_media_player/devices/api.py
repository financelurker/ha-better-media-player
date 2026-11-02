"""The interface between the device layer and its owners (rooms).

An owner registers its nodes and answers what only it knows: whether a node is
needed, how long it runs down, and how commands are sent (its own contexts,
so it can tell its changes from others'). The device layer does the switching.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .model import Node, Standby


class NodeOwner(Protocol):
    """What the device layer asks the owner of a group of nodes."""

    name: str  # for the log

    @property
    def automatic(self) -> bool:
        """Nodes switch off by themselves after their run-down."""

    def demanded(self, node: Node) -> bool:
        """The node is needed now."""

    def enabled(self, node: Node) -> bool:
        """The node may be switched on now (e.g. not at night)."""

    def on_wait(self, node: Node) -> float:
        """Seconds to wait after switching the node on (before the next one)."""

    def run_down(self, node: Node) -> float:
        """Seconds the node stays on after its demand ended."""

    def waits(self, node: Node) -> bool:
        """The node, due to go off, waits a little longer (e.g. for something else first)."""

    def standby(self, node: Node) -> Standby | None:
        """Wait for standby before its plugs go off (None: no waiting)."""

    def start_timeout(self) -> float:
        """Seconds a device behind a plug may take to become available."""

    async def call(self, domain: str, service: str, data: dict) -> bool:
        """Call an action (in a context of the owner's)."""

    async def run_action(self, action: str, on: bool) -> None:
        """Run one of a node's own on/off actions (a script, button, scene …)."""

    def assumed(self, entity_id: str) -> bool:
        """What was last sent to an own on/off action (no feedback)."""

    def set_assumed(self, entity_id: str, on: bool) -> None:
        """Remember what was sent to an own on/off action (persisted)."""

    async def wait_until(self, cond: Callable[[], bool], timeout: float) -> bool:
        """Wait (on state changes) until cond holds or the timeout ends."""
