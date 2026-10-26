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

from ..const import CONF_START_TIMEOUT, DOMAIN, ROLE_AUDIO_HOST, ROLE_DISPLAY, ROLE_VIDEO_HOST
from .. import clock
from .power import ON_ORDER, Role

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
        # background task: may wait for a slow player, must not block HA
        self.task = ctrl.hass.async_create_background_task(
            self._run(source), f"{DOMAIN}_{ctrl.entry.entry_id}_start", eager_start=False
        )
        ctrl._update_status()
        ctrl._notify()

    async def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds)

    async def _run(self, source: str | None) -> None:
        """Power the room up in a safe order."""
        ctrl = self.ctrl
        power = ctrl.power
        switched: list[Role] = []
        src = ctrl.sources.get(source) if source else None
        try:
            if src is not None:
                needed_sources = {src.key}
                roles = [
                    r
                    for k in ON_ORDER
                    if (r := power.roles.get(k)) is not None and src.key in r.sources and ctrl.night.role_enabled(k)
                ]
            else:
                needed_sources = set()
                roles = [
                    r for k in ON_ORDER if (r := power.roles.get(k)) is not None and power.role_demand(r)
                ]

            before_player = [r for r in roles if r.key in (ROLE_VIDEO_HOST, ROLE_AUDIO_HOST, ROLE_DISPLAY)]
            after_player = [r for r in roles if r not in before_player]

            for role in before_player:
                if not ctrl.night.role_enabled(role.key):
                    continue
                if not power.role_fully_on(role):
                    switched.append(role)
                    await power.async_role_up(role)
                    await self._sleep(power.role_on_wait(role))
                else:
                    role.last_demand = clock.now()

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

            for role in after_player:
                if not ctrl.night.role_enabled(role.key):
                    continue  # night mode switched on during the start
                if not power.role_fully_on(role):
                    switched.append(role)
                    await power.async_role_up(role)
                    await self._sleep(power.role_on_wait(role))
                else:
                    role.last_demand = clock.now()
            ctrl.error = None
        except _StartFailed as err:
            _LOGGER.warning("%s: start failed: %s", ctrl.entry.title, err)
            ctrl.error = str(err)
            for role in reversed(switched):
                role.force_off = True  # retried by async_auto_off until really off
                await power.async_switch_role(role, False, force=True)
        finally:
            if asyncio.current_task() is self.task:
                self.task = None
            ctrl._changed.set()  # (waiting for the sequence: it is through)
            if not ctrl._stopped:
                ctrl._update_status()
                ctrl._notify()
            if ctrl._started and not ctrl._stopped:
                ctrl._spawn(ctrl.async_update("sequence_done"))
