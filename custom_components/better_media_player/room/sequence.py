"""The room's power-on sequence: roles and player up in a safe order.

Player hardware plug and display first, then the player is woken and waited
for, then speakers and subwoofer (each with its wait after switching on). If
the player is not ready within the start timeout, everything switched on is
switched off again and the room reports an error.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from homeassistant.core import callback

from ..const import CONF_START_TIMEOUT, DOMAIN
from .. import clock
from ..devices import Node

if TYPE_CHECKING:
    from .controller import RoomController

_LOGGER = logging.getLogger(__name__)


class _StartFailed(Exception):
    """Start sequence could not be completed."""


class PowerSequence:
    """Runs the power-on sequence as a background task (one at a time)."""

    def __init__(self, ctrl: RoomController) -> None:
        self.ctrl = ctrl
        self.task: asyncio.Task | None = None
        self.source: str | None = None  # the source an explicit start is for

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    @callback
    def cancel(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
        self.task = None

    @callback
    def start(self, source: str | None) -> None:
        """source=None: automatic start, only switches roles with demand.
        source=<key>: explicit start of that source, including the player itself."""
        ctrl = self.ctrl
        if ctrl._stopped:
            return
        if self.running:
            if source is None:
                return
            self.cancel()
        self.source = source
        # background task: may wait for a slow player, must not block HA
        self.task = ctrl.hass.async_create_background_task(
            self._run(source), f"{DOMAIN}_{ctrl.entry.entry_id}_start", eager_start=False
        )
        ctrl._update_status()
        ctrl._notify()

    async def _run(self, source: str | None) -> None:
        """Power the room up in a safe order."""
        ctrl = self.ctrl
        power = ctrl.power
        switched: list[Node] = []
        src = ctrl.sources.get(source) if source else None
        power.want_inputs(source or ctrl.input_source)
        try:
            if src is not None:
                needed_sources = {src.key}
                nodes = [
                    n
                    for k in power.on_order
                    if src.key in (n := power.nodes[k]).sources and ctrl.night.enabled(n)
                ]
            else:
                needed_sources = set()
                nodes = [n for k in power.on_order if power.needed(n := power.nodes[k])]

            # the players' plugs and what carries the picture before the player, the sound after
            before_player = [n for n in nodes if n.device is None or n.picture]
            after_player = [n for n in nodes if n not in before_player]

            await power.devices.async_bring_up(before_player, switched)

            if src is not None and src.key in needed_sources:
                # one deadline for the whole player start
                deadline = clock.now() + float(ctrl.setting(CONF_START_TIMEOUT))
                if src.host and not await ctrl._wait_until(
                    lambda: ctrl.player_available(src), deadline - clock.now()
                ):
                    raise _StartFailed(f"{src.player} did not become available")
                if not ctrl.source_ready(src):
                    await ctrl._source_on(src)
                    if not await ctrl._wait_until(lambda: ctrl.source_ready(src), deadline - clock.now()):
                        raise _StartFailed(f"{src.player} did not start")
                # the player is up: what it does over CEC is counted from now
                power.devices.restart_expectations()

            # (a role the night mode switched off during the start is left out)
            await power.devices.async_bring_up(after_player, switched)
            ctrl.error = None
        except _StartFailed as err:
            _LOGGER.warning("%s: start failed: %s", ctrl.entry.title, err)
            ctrl.error = str(err)
            await power.devices.async_take_down(switched)
        finally:
            if asyncio.current_task() is self.task:
                self.task = None
            ctrl._changed.set()  # (waiting for the sequence: it is through)
            if not ctrl._stopped:
                ctrl._update_status()
                ctrl._notify()
            if ctrl._started and not ctrl._stopped:
                ctrl._spawn(ctrl.async_update("sequence_done"))
