"""Follow me: the music follows the people from room to room (the central entry).

manager.py   the session: start, stop, who joins and leaves when
volume.py    the group's volume, remembered; night mode in groups
wake.py      a follow wake's room in the follow-me player

The rooms' side of follow me is in room/follow.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from ..const import DOMAIN

if TYPE_CHECKING:
    from ..room.controller import RoomController
    from .manager import FollowManager

PLAYING = {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING}
PRESENT = "present"
EMPTY = "empty"
UNKNOWN = "unknown"
START_GRACE_SECONDS = 30  # after a start, MA may report "playing" late


def get_hub(hass: HomeAssistant) -> FollowManager | None:
    """The central entry's follow me (None: no central entry set up)."""
    return hass.data.get(DOMAIN, {}).get("hub")


async def async_start_follow(hass: HomeAssistant, room: RoomController) -> None:
    """Follow me from this room (the room's button and action)."""
    hub = get_hub(hass)
    if hub is None:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_follow_player")
    await hub.async_follow_start(room)  # refuses if it cannot start here


async def async_stop_follow(hass: HomeAssistant, room: RoomController) -> None:
    """Follow me off, from this room (the room's button and action)."""
    hub = get_hub(hass)
    if hub is None:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="follow_not_running")
    await hub.async_follow_stop(room)
