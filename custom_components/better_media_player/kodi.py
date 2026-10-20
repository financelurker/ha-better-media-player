"""Kodi as the room's video player (Home Assistant's Kodi, and Better Kodi).

Kodi:
- it cannot be browsed without a connection: while it is off the room offers
  Home Assistant's media sources (e.g. Jellyfin) instead (as every video player);
- turn_on / turn_off only fire Kodi's device triggers: the user's Wake-on-LAN
  and suspend automations do the actual work (nothing to do here).

Better Kodi, on top:
- it adds a queue entry to its browser; the room lifts it to its own root
  ("Queue: <player>");
- its own play now / next / add / replace entries carry the mode in their id:
  the room reads it (and decides by it), the id goes to the player unchanged;
- its handover continues the playback on another Better Kodi (it takes a
  snapshot and resumes there; the source may play on).
"""

from __future__ import annotations

from homeassistant.components.media_player import BrowseMedia
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Context

from .backend import ENQUEUE_MODES, VideoBackend

QUEUE_TYPE = "better_kodi_queue"
ENQUEUE_PREFIX = "better_kodi://enqueue/"


class Kodi(VideoBackend):
    """What every video player does (cannot browse while off) is all Kodi needs."""

    platform = "kodi"


class BetterKodi(Kodi):
    platform = "better_kodi"
    lifts_queue = True
    can_handover = True
    keeps_source = True

    async def handover(self, source: str, target: str, keep_source_playing: bool = False) -> bool:
        # (its own errors, e.g. nothing playing, reach the user as they are)
        await self.hass.services.async_call(
            "better_kodi",
            "handover",
            {ATTR_ENTITY_ID: source, "destination": target, "keep_source_playing": keep_source_playing, "resume": True},
            blocking=True,
            context=Context(),
        )
        return True

    def queue_entry(self, root: BrowseMedia | None) -> BrowseMedia | None:
        for item in (root.children or []) if root else []:
            if item.media_content_type == QUEUE_TYPE:
                return item
        return None

    def enqueue_mode(self, media_id: str | None) -> str | None:
        if media_id and media_id.startswith(ENQUEUE_PREFIX):
            mode = media_id.removeprefix(ENQUEUE_PREFIX).split("/", 1)[0]
            if mode in ENQUEUE_MODES:
                return mode
        return None
