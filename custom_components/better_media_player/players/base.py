"""The interface of a player system: what every video / music player can do, and the
groups players are in (see __init__.py for the systems).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.media_player import DOMAIN as MP_DOMAIN
from homeassistant.components.media_player import BrowseMedia, MediaPlayerState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import SOURCE_AUDIO, SOURCE_VIDEO

if TYPE_CHECKING:
    from .position import Position

_LOGGER = logging.getLogger(__name__)

ENQUEUE_MODES = ("play", "next", "add", "replace")

_PLAYING = {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING}
_POWERED = {
    MediaPlayerState.ON,
    MediaPlayerState.IDLE,
    MediaPlayerState.PLAYING,
    MediaPlayerState.PAUSED,
    MediaPlayerState.BUFFERING,
}
# a group "has" a player while it plays (or is paused); idle=True: also while it
# does not play right now (e.g. joined by follow me before the group starts), as
# long as the player does not play on its own. Groups that are off are left out:
# static groups list their members even then; idle ones may too.
_GROUP_ACTIVE = {MediaPlayerState.PLAYING, MediaPlayerState.PAUSED, MediaPlayerState.BUFFERING}
_OFF = {"off", "standby", "unavailable", "unknown"}


def _video_content(item: BrowseMedia) -> bool:
    """Media sources that make sense on a video player."""
    content_type = str(item.media_content_type or "")
    return not content_type.startswith(("audio/", "image/"))


@dataclass(frozen=True)
class QueueItem:
    """An item of a player's queue, for the room's browser."""

    title: str
    item_id: str | None = None
    image: str | None = None


@dataclass(frozen=True)
class Queue:
    current: QueueItem | None = None
    next: QueueItem | None = None


class PlayerBackend:
    """A player of the room, as far as its system matters."""

    platform: str | None = None  # the integration it comes from (None: any)
    role: str = ""  # the room's source it serves: SOURCE_VIDEO / SOURCE_AUDIO
    browse_while_off = True  # its library can be browsed while the player is off
    # Home Assistant's media sources (e.g. Jellyfin) are offered for it, with this
    # filter (None: not offered): while it cannot browse, and for their ids
    media_sources: Callable[[BrowseMedia], bool] | None = None
    room_choices = True  # the room offers play now / next / add / replace for its items
    lifts_queue = False  # its browser root has a queue entry (queue_entry) the room lifts
    has_queue = False  # queue() gives its queue
    can_handover = False  # handover() continues its playback on another player of the system
    keeps_source = False  # ... and can let the source play on

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    @classmethod
    def handles(cls, hass: HomeAssistant, entity_id: str | None) -> bool:
        if cls.platform is None:
            return True
        entry = er.async_get(hass).async_get(entity_id) if entity_id else None
        return entry is not None and entry.platform == cls.platform

    def is_active(self, state: str | None) -> bool:
        """The player is in use (it needs its devices) in this state."""
        return state in _PLAYING

    def can_browse(self, state: str | None) -> bool:
        return self.browse_while_off or state not in (*_OFF, None)

    def is_group(self, entity_id: str | None) -> bool:
        """A group player of this system (no room's own player)."""
        return False

    def groups_of(self, player: str, idle: bool = False) -> list[str]:
        """Media players (groups of any system) that have player as a member right now."""
        return groups_of(self.hass, player, idle)

    def queue_entry(self, root: BrowseMedia | None) -> BrowseMedia | None:
        """Its queue as an entry of its own browser root (lifted to the room's root)."""
        return None

    async def queue(self, player: str) -> Queue:
        return Queue()

    def enqueue_mode(self, media_id: str | None) -> str | None:
        """Play now / next / add / replace, if the player carries it in its own ids."""
        return None

    def handover_to(self, other: PlayerBackend) -> bool:
        """Its playback can continue on a player with that backend."""
        return self.can_handover and type(other) is type(self)

    async def handover(self, source: str, target: str, keep_source_playing: bool = False) -> bool:
        """Continue the playback of source on target (it is up already)."""
        return False

    async def unjoin(self, player: str, context: Context | None = None) -> bool:
        """Leave the group(s) it is in (Home Assistant's own grouping)."""
        return await self._call(MP_DOMAIN, "unjoin", {ATTR_ENTITY_ID: player}, context)

    def async_stop(self) -> None:
        """Unloaded: whatever still runs ends."""

    async def _call(self, domain: str, service: str, data: dict, context: Context | None = None) -> bool:
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True, context=context or Context())
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("%s.%s %s failed: %s", domain, service, data, err)
            return False
        return True


class VideoBackend(PlayerBackend):
    """The room's video player: active while it is on (also in its menu); browsing
    needs it on, Home Assistant's media sources while it is off."""

    role = SOURCE_VIDEO
    browse_while_off = False
    media_sources = staticmethod(_video_content)
    room_choices = False  # (video players bring their own, if any)

    def is_active(self, state: str | None) -> bool:
        return state in _POWERED


class MusicBackend(PlayerBackend):
    """The room's music player: active while it plays; groups as far as the system
    has them (follow me needs one that can move a queue)."""

    role = SOURCE_AUDIO

    def groups(self) -> list[str]:
        return [s.entity_id for s in self.hass.states.async_all(MP_DOMAIN) if self.is_group(s.entity_id)]

    def members(self, group: str | None) -> list[str]:
        state = self.hass.states.get(group) if group else None
        return list(state.attributes.get("group_members") or []) if state else []

    def state(self, group: str | None) -> str | None:
        state = self.hass.states.get(group) if group else None
        return state.state if state else None

    async def join(self, group: str, player: str, context: Context | None = None) -> bool:
        return await self._call(MP_DOMAIN, "join", {ATTR_ENTITY_ID: group, "group_members": [player]}, context)

    async def resume(self, group: str) -> bool:
        return await self._call(MP_DOMAIN, "media_play", {ATTR_ENTITY_ID: group})

    def position(self, entity_id: str) -> Position | None:
        """Where a player is in its current track (read before joining or leaving)."""
        return None

    async def transfer(
        self, source: str, target: str, auto_play: bool = True, position: Position | None = None
    ) -> bool:
        """Move the queue of source to target (False: the system cannot)."""
        return False


def groups_of(hass: HomeAssistant, player: str, idle: bool = False) -> list[str]:
    """Media players (groups of any system) that have player as a member right now."""
    own = hass.states.get(player)
    on_its_own = own is not None and own.state in _GROUP_ACTIVE  # (a group it plays in plays too)
    out = []
    for state in hass.states.async_all(MP_DOMAIN):
        if state.entity_id == player:
            continue
        if state.state not in _GROUP_ACTIVE and (not idle or state.state in _OFF or on_its_own):
            continue
        if player in (state.attributes.get("group_members") or []):
            out.append(state.entity_id)
    return out
