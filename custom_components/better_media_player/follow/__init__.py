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

from ..const import DOMAIN

if TYPE_CHECKING:
    from .manager import FollowManager

PLAYING = {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING}
PRESENT = "present"
EMPTY = "empty"
UNKNOWN = "unknown"
START_GRACE_SECONDS = 30  # after a start, MA may report "playing" late


def get_hub(hass: HomeAssistant) -> FollowManager | None:
    """The central entry's follow me (None: no central entry set up)."""
    return hass.data.get(DOMAIN, {}).get("hub")
