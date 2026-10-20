"""Music Assistant: how the rooms and follow me use it.

Everything Better Media Player does with Music Assistant goes through here:
its group players (recognised by mass_player_type), moving a queue to another
player (the current track goes on where it was, see position.py) and a
player's queue for the media browser. Joining and leaving are Home
Assistant's own (see backend.py).

Follow me decides who joins a group when; this module only knows how. Group
players with static members cannot be recognised (Home Assistant does not
show it): they stay a note in the README.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.media_player import DOMAIN as MP_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback

from .backend import MusicBackend, Queue, QueueItem
from .position import Position, keep_position, last_report

_LOGGER = logging.getLogger(__name__)

PLATFORM = "music_assistant"
ATTR_PLAYER_TYPE = "mass_player_type"
TYPE_GROUP = "group"


class MusicAssistant(MusicBackend):
    platform = PLATFORM
    has_queue = True

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass)
        self._watch: CALLBACK_TYPE | None = None  # the moved track goes on where it was
        self._tasks: set[asyncio.Task] = set()
        self._stopped = False

    def is_group(self, entity_id: str | None) -> bool:
        state = self.hass.states.get(entity_id) if entity_id else None
        return state is not None and state.attributes.get(ATTR_PLAYER_TYPE) == TYPE_GROUP

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
        if auto_play and position is not None and not self._stopped:

            @callback
            def _seek(seconds: float) -> None:
                _LOGGER.debug("%s goes on at %.0f s", target, seconds)
                self._spawn(self._call(MP_DOMAIN, "media_seek", {ATTR_ENTITY_ID: target, "seek_position": seconds}))

            if self._watch is not None:
                self._watch()
            self._watch = keep_position(self.hass, target, position, _seek, reported)
        return True

    async def queue(self, player: str) -> Queue:
        """Current and next item (all that Home Assistant exposes)."""
        try:
            response = await self.hass.services.async_call(
                PLATFORM, "get_queue", {ATTR_ENTITY_ID: player}, blocking=True, return_response=True
            )
        except Exception as err:  # noqa: BLE001 - no active queue, MA not available, ...
            _LOGGER.debug("get_queue for %s failed: %s", player, err)
            return Queue()
        queue = (response or {}).get(player) or {}
        return Queue(current=_item(queue.get("current_item")), next=_item(queue.get("next_item")))

    @callback
    def async_stop(self) -> None:
        self._stopped = True
        if self._watch is not None:
            self._watch()
            self._watch = None
        for task in list(self._tasks):
            task.cancel()

    @callback
    def _spawn(self, coro) -> None:
        if self._stopped:
            coro.close()
            return
        task = self.hass.async_create_task(coro, eager_start=False)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _item(entry: dict | None) -> QueueItem | None:
    if not entry:
        return None
    media_item = entry.get("media_item") or {}
    return QueueItem(
        title=media_item.get("name") or entry.get("name") or "",
        item_id=entry.get("queue_item_id"),
        image=media_item.get("image"),
    )
