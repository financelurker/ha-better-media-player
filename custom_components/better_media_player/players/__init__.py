"""What the room needs to know about the system behind one of its players.

The room itself only knows its two roles: a video player (needs the display)
and a music player (may play in groups). What a particular system can do on
top, or does differently – when it counts as active, browsing while it is off,
its own queue or its own play next / add entries, its group players, moving a
queue – is answered by a backend:

    PlayerBackend                 every player
    ├── VideoBackend              the room's video player
    │   └── Kodi                  kodi.py
    │       └── BetterKodi
    └── MusicBackend              the room's music player (groups)
        └── MusicAssistant        music_assistant.py

A player of an unknown system gets the plain VideoBackend / MusicBackend: what
Home Assistant offers for every media player. Another system is one more
module with its subclass, listed in _backends() below; the interface is in base.py.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import SOURCE_VIDEO
from .base import MusicBackend, PlayerBackend, VideoBackend
from .kodi import BetterKodi, Kodi
from .music_assistant import MusicAssistant


def _backends() -> list[type[PlayerBackend]]:
    return [Kodi, BetterKodi, MusicAssistant]


def backend_for(hass: HomeAssistant, entity_id: str | None, role: str) -> PlayerBackend:
    """The backend of a player in this role (the plain one of the role if its system is unknown)."""
    for cls in _backends():
        if cls.role == role and cls.handles(hass, entity_id):
            return cls(hass)
    return VideoBackend(hass) if role == SOURCE_VIDEO else MusicBackend(hass)


def is_group(hass: HomeAssistant, entity_id: str | None) -> bool:
    """A group player of any known system (no room's own player)."""
    return any(cls(hass).is_group(entity_id) for cls in _backends())


def suggested_role(hass: HomeAssistant, entry: er.RegistryEntry) -> str | None:
    """For the setup: the role a player of a known system is pre-selected for."""
    for cls in _backends():
        if cls.platform == entry.platform:
            return None if cls(hass).is_group(entry.entity_id) else cls.role
    return None
