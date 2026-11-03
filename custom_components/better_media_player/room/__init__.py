"""A room: its controller and what it does (power, power-on sequence, volume, night,
sleep, wake, announcements, handover, follow me, browser)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import DOMAIN

if TYPE_CHECKING:
    from .controller import RoomController


def get_rooms(hass: HomeAssistant) -> dict[str, RoomController]:
    """The rooms that are set up, by config entry id."""
    return hass.data.setdefault(DOMAIN, {}).setdefault("rooms", {})


def room_device(hass: HomeAssistant, entry_id: str) -> dr.DeviceEntry | None:
    """The device of a room (or of the central entry) in Home Assistant."""
    reg = dr.async_get(hass)
    if (by_identifier := getattr(reg, "async_get_device_by_identifier", None)) is not None:
        return by_identifier((DOMAIN, entry_id), entry_id)
    return reg.async_get_device(identifiers={(DOMAIN, entry_id)})  # (older Home Assistant)


def room_of(hass: HomeAssistant, entity_id: str | None) -> RoomController | None:
    """The room whose room player this is."""
    entry = er.async_get(hass).async_get(entity_id) if entity_id else None
    if entry is None or entry.platform != DOMAIN or entry.config_entry_id is None:
        return None
    return get_rooms(hass).get(entry.config_entry_id)
