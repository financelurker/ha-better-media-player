"""Music Assistant: how the rooms and follow me use it.

Everything Better Media Player does with Music Assistant goes through here:
its group players (which, their members, joining and leaving), moving a queue
to another player (the current track goes on where it was, see position.py),
a player's queue for the media browser, and recognising its players.

Follow me decides who joins a group when; this module only knows how. It is
the one place another system with groups would be added.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.media_player import DOMAIN as MP_DOMAIN
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .position import Position, keep_position, last_report

_LOGGER = logging.getLogger(__name__)

PLATFORM = "music_assistant"
ATTR_PLAYER_TYPE = "mass_player_type"
TYPE_GROUP = "group"

# a group "has" a player while it plays (or is paused); idle=True: also while it
# does not play right now (e.g. joined by follow me before the group starts).
# Groups that are off are left out: static groups list their members even then.
_ACTIVE = {MediaPlayerState.PLAYING, MediaPlayerState.PAUSED, MediaPlayerState.BUFFERING}
_OFF = {"off", "standby", "unavailable", "unknown"}


class MusicAssistant:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._watch: CALLBACK_TYPE | None = None  # the moved track goes on where it was
        self._tasks: set[asyncio.Task] = set()

    # ---------------------------------------------------------------- players

    def is_group(self, entity_id: str | None) -> bool:
        """A Music Assistant group player."""
        state = self.hass.states.get(entity_id) if entity_id else None
        return state is not None and state.attributes.get(ATTR_PLAYER_TYPE) == TYPE_GROUP

    def is_player(self, entity_id: str | None) -> bool:
        """A player of Music Assistant (also a group)."""
        entry = er.async_get(self.hass).async_get(entity_id) if entity_id else None
        return entry is not None and entry.platform == PLATFORM

    def groups(self) -> list[str]:
        return [s.entity_id for s in self.hass.states.async_all(MP_DOMAIN) if s.attributes.get(ATTR_PLAYER_TYPE) == TYPE_GROUP]

    def members(self, group: str | None) -> list[str]:
        state = self.hass.states.get(group) if group else None
        return list(state.attributes.get("group_members") or []) if state else []

    def state(self, group: str | None) -> str | None:
        state = self.hass.states.get(group) if group else None
        return state.state if state else None

    def groups_of(self, player: str, idle: bool = False) -> list[str]:
        """Media players (groups) that have player as a member right now."""
        out = []
        for state in self.hass.states.async_all(MP_DOMAIN):
            if state.entity_id == player:
                continue
            if (state.state in _OFF) if idle else (state.state not in _ACTIVE):
                continue
            if player in (state.attributes.get("group_members") or []):
                out.append(state.entity_id)
        return out

    # --------------------------------------------------------------- commands

    async def join(self, group: str, player: str, context: Context | None = None) -> bool:
        return await self._call(MP_DOMAIN, "join", {ATTR_ENTITY_ID: group, "group_members": [player]}, context)

    async def unjoin(self, player: str, context: Context | None = None) -> bool:
        return await self._call(MP_DOMAIN, "unjoin", {ATTR_ENTITY_ID: player}, context)

    async def resume(self, group: str) -> bool:
        return await self._call(MP_DOMAIN, "media_play", {ATTR_ENTITY_ID: group})

    def position(self, entity_id: str) -> Position | None:
        """Where a player is in its current track (read it before joining or
        leaving: that may stop it)."""
        return Position.of(self.hass, entity_id)

    async def transfer(
        self, source: str, target: str, auto_play: bool = True, position: Position | None = None
    ) -> bool:
        """Move the queue of source to target. With a position (read before),
        the current track goes on there where it was."""
        reported = last_report(self.hass, target)
        if not await self._call(
            PLATFORM, "transfer_queue", {ATTR_ENTITY_ID: target, "source_player": source, "auto_play": auto_play}
        ):
            return False
        if auto_play and position is not None:

            @callback
            def _seek(seconds: float) -> None:
                _LOGGER.debug("%s goes on at %.0f s", target, seconds)
                self._spawn(self._call(MP_DOMAIN, "media_seek", {ATTR_ENTITY_ID: target, "seek_position": seconds}))

            if self._watch is not None:
                self._watch()
            self._watch = keep_position(self.hass, target, position, _seek, reported)
        return True

    async def queue(self, player: str) -> dict[str, Any]:
        """A player's queue (current and next item: all that Home Assistant exposes)."""
        try:
            response = await self.hass.services.async_call(
                PLATFORM, "get_queue", {ATTR_ENTITY_ID: player}, blocking=True, return_response=True
            )
        except Exception as err:  # noqa: BLE001 - no active queue, MA not available, ...
            _LOGGER.debug("get_queue for %s failed: %s", player, err)
            return {}
        return (response or {}).get(player) or {}

    @callback
    def async_stop(self) -> None:
        if self._watch is not None:
            self._watch()
            self._watch = None
        for task in list(self._tasks):
            task.cancel()

    # --------------------------------------------------------------- internal

    async def _call(self, domain: str, service: str, data: dict, context: Context | None = None) -> bool:
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True, context=context or Context())
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("%s.%s %s failed: %s", domain, service, data, err)
            return False
        return True

    @callback
    def _spawn(self, coro) -> None:
        task = self.hass.async_create_task(coro, eager_start=False)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
