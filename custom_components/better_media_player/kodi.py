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
  the room reads it (and decides by it), the id goes to the player unchanged.
"""

from __future__ import annotations

from homeassistant.components.media_player import BrowseMedia

from .backend import ENQUEUE_MODES, VideoBackend

QUEUE_TYPE = "better_kodi_queue"
ENQUEUE_PREFIX = "better_kodi://enqueue/"


class Kodi(VideoBackend):
    """What every video player does (cannot browse while off) is all Kodi needs."""

    platform = "kodi"


class BetterKodi(Kodi):
    platform = "better_kodi"
    lifts_queue = True

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
