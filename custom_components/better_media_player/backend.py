"""What the room needs to know about the system behind one of its players.

The room itself only knows its two roles: a video player (needs the display,
is active while it is on) and a music player (active while it plays, may play
in groups). What a particular system can do on top – browse while it is off,
bring its own queue or its own play next / add entries, groups – is answered
by a backend:

    PlayerBackend                 every player
    ├── VideoBackend              the room's video player
    │   └── Kodi                  kodi.py
    │       └── BetterKodi
    └── MusicBackend              the room's music player (groups)
        └── MusicAssistant        music_assistant.py

A player of an unknown system gets the plain VideoBackend / MusicBackend: what
Home Assistant offers for every media player. Another system is one more
module with its subclass, listed in BACKENDS.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player import DOMAIN as MP_DOMAIN
from homeassistant.components.media_player import BrowseMedia, MediaPlayerState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import SOURCE_AUDIO, SOURCE_VIDEO

if TYPE_CHECKING:
    from .position import Position

_LOGGER = logging.getLogger(__name__)

# a group "has" a player while it plays (or is paused); idle=True: also while it
# does not play right now (e.g. joined by follow me before the group starts).
# Groups that are off are left out: static groups list their members even then.
_ACTIVE = {MediaPlayerState.PLAYING, MediaPlayerState.PAUSED, MediaPlayerState.BUFFERING}
_OFF = {"off", "standby", "unavailable", "unknown"}


def groups_of(hass: HomeAssistant, player: str, idle: bool = False) -> list[str]:
    """Media players (groups of any system) that have player as a member right now."""
    out = []
    for state in hass.states.async_all(MP_DOMAIN):
        if state.entity_id == player:
            continue
        if (state.state in _OFF) if idle else (state.state not in _ACTIVE):
            continue
        if player in (state.attributes.get("group_members") or []):
            out.append(state.entity_id)
    return out


def _video_content(item: BrowseMedia) -> bool:
    """Media sources that make sense on a video player."""
    content_type = str(item.media_content_type or "")
    return not content_type.startswith(("audio/", "image/"))


class PlayerBackend:
    """A player of the room, as far as its system matters."""

    platform: str | None = None  # the integration it comes from (None: any)
    role: str = ""  # the room's source it serves: SOURCE_VIDEO / SOURCE_AUDIO
    browse_while_off = True  # its library can be browsed while the player is off
    # Home Assistant's media sources (e.g. Jellyfin) are offered for it, with this
    # filter (None: not offered): while it cannot browse, and for their ids
    media_sources: Callable[[BrowseMedia], bool] | None = None
    room_choices = True  # the room offers play now / next / add / replace for its items

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    @classmethod
    def handles(cls, hass: HomeAssistant, entity_id: str | None) -> bool:
        if cls.platform is None:
            return True
        entry = er.async_get(hass).async_get(entity_id) if entity_id else None
        return entry is not None and entry.platform == cls.platform

    def can_browse(self, state: str | None) -> bool:
        return self.browse_while_off or state not in ("off", "unavailable", "unknown", None)

    def queue_entry(self, root: BrowseMedia | None) -> BrowseMedia | None:
        """Its queue as an entry of its own browser root (lifted to the room's root)."""
        return None

    has_queue = False  # queue() below gives its queue

    async def queue(self, player: str) -> dict[str, Any]:
        """Current and next item: {"current_item": ..., "next_item": ...}."""
        return {}

    def enqueue_mode(self, media_id: str | None) -> str | None:
        """Play now / next / add / replace, if the player carries it in its own ids."""
        return None

    async def unjoin(self, player: str, context: Context | None = None) -> bool:
        """Leave the group(s) it is in (Home Assistant's own grouping)."""
        return await self._call(MP_DOMAIN, "unjoin", {ATTR_ENTITY_ID: player}, context)

    async def _call(self, domain: str, service: str, data: dict, context: Context | None = None) -> bool:
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True, context=context or Context())
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("%s.%s %s failed: %s", domain, service, data, err)
            return False
        return True


class VideoBackend(PlayerBackend):
    """The room's video player: browsing needs it on; media sources while it is off."""

    role = SOURCE_VIDEO
    browse_while_off = False
    media_sources = staticmethod(_video_content)
    room_choices = False  # (video players bring their own, if any)


class MusicBackend(PlayerBackend):
    """The room's music player: groups, as far as the system has them."""

    role = SOURCE_AUDIO

    def is_group(self, entity_id: str | None) -> bool:
        """A group player of this system (its members' volumes are follow me's business)."""
        return False

    def groups(self) -> list[str]:
        return []

    def members(self, group: str | None) -> list[str]:
        state = self.hass.states.get(group) if group else None
        return list(state.attributes.get("group_members") or []) if state else []

    def state(self, group: str | None) -> str | None:
        state = self.hass.states.get(group) if group else None
        return state.state if state else None

    def groups_of(self, player: str, idle: bool = False) -> list[str]:
        return groups_of(self.hass, player, idle)

    async def join(self, group: str, player: str, context: Context | None = None) -> bool:
        return await self._call(MP_DOMAIN, "join", {ATTR_ENTITY_ID: group, "group_members": [player]}, context)

    async def resume(self, group: str) -> bool:
        return await self._call(MP_DOMAIN, "media_play", {ATTR_ENTITY_ID: group})

    can_transfer = False  # transfer() moves a queue to another player

    def position(self, entity_id: str) -> Position | None:
        return None

    async def transfer(
        self, source: str, target: str, auto_play: bool = True, position: Position | None = None
    ) -> bool:
        return False

    def async_stop(self) -> None:
        """Unloaded: whatever still runs ends."""


def _backends() -> list[type[PlayerBackend]]:
    from .kodi import BetterKodi, Kodi
    from .music_assistant import MusicAssistant

    return [Kodi, BetterKodi, MusicAssistant]


def backend_for(hass: HomeAssistant, entity_id: str | None, role: str) -> PlayerBackend:
    """The backend of a player in this role (the plain one of the role if its system is unknown)."""
    for cls in _backends():
        if cls.role == role and cls.handles(hass, entity_id):
            return cls(hass)
    return VideoBackend(hass) if role == SOURCE_VIDEO else MusicBackend(hass)


def suggested_role(hass: HomeAssistant, entry: er.RegistryEntry) -> str | None:
    """For the setup: the role a player of a known system is pre-selected for."""
    for cls in _backends():
        if cls.platform != entry.platform:
            continue
        backend = cls(hass)
        if isinstance(backend, MusicBackend) and backend.is_group(entry.entity_id):
            return None  # a group is no room's own player
        return cls.role
    return None
