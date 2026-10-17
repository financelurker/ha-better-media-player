"""Common base for all entities of a room."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import CONF_AREA, DOMAIN, NAME, SOURCE_AUDIO, SOURCE_VIDEO, VERSION
from .controller import RoomController


def _model(ctrl: RoomController) -> str:
    parts = []
    if SOURCE_VIDEO in ctrl.sources:
        parts.append("Video")
    if SOURCE_AUDIO in ctrl.sources:
        parts.append("Audio")
    return "Room player (" + " + ".join(parts) + ")"


class BetterMediaPlayerEntity(Entity):
    """One device per room, updated by the controller."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, controller: RoomController, key: str | None) -> None:
        self.ctrl = controller
        self._attr_unique_id = f"{entry.entry_id}_{key or 'player'}"
        if key:
            self._attr_translation_key = key
        area_name = None
        area_id = controller.opts.get(CONF_AREA)
        if area_id:
            area = ar.async_get(controller.hass).async_get_area(area_id)
            area_name = area.name if area else None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=NAME,
            model=_model(controller),
            sw_version=VERSION,
            suggested_area=area_name,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.ctrl.async_add_listener(self.async_write_ha_state))


class HubEntity(Entity):
    """Entities of the central entry."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, hub, key: str) -> None:
        self.hub = hub
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=NAME,
            model="Central settings",
            sw_version=VERSION,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.hub.async_add_listener(self.async_write_ha_state))
