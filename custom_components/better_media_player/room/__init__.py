"""A room: its controller and what it does (power, power-on sequence, volume, night,
sleep, wake, announcements, handover, follow me, browser)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import DOMAIN

if TYPE_CHECKING:
    from .controller import RoomController


def get_rooms(hass: HomeAssistant) -> dict[str, RoomController]:
    """The rooms that are set up, by config entry id."""
    return hass.data.setdefault(DOMAIN, {}).setdefault("rooms", {})


def room_of(hass: HomeAssistant, entity_id: str | None) -> RoomController | None:
    """The room whose room player this is."""
    entry = er.async_get(hass).async_get(entity_id) if entity_id else None
    if entry is None or entry.platform != DOMAIN or entry.config_entry_id is None:
        return None
    return get_rooms(hass).get(entry.config_entry_id)
