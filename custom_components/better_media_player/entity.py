"""Common base for all entities of a room."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import CONF_AREA, DOMAIN, NAME, SOURCE_AUDIO, SOURCE_VIDEO
from .controller import RoomController


def integration_version(hass) -> str | None:
    """Version from the manifest (set from the release tag when a release is built)."""
    return hass.data.get(DOMAIN, {}).get("version")


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
            sw_version=integration_version(controller.hass),
            suggested_area=area_name,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.ctrl.async_add_listener(self.async_write_ha_state))


class ControlEntity(BetterMediaPlayerEntity):
    """A control that can only be used now and then (wake, follow me).

    While it cannot be used it is hidden (hidden_by: integration), so it does
    not clutter dashboards; pressing it anyway gives an error or does nothing
    (see the control). Never against the user:
    a control they hid stays hidden, one they showed again while it was not
    usable is not hidden any more.
    """

    @property
    def usable(self) -> bool:
        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.ctrl.async_add_listener(self._sync_hidden))
        self._sync_hidden()

    @callback
    def _sync_hidden(self) -> None:
        reg = er.async_get(self.hass)
        entry = reg.async_get(self.entity_id) if self.entity_id else None
        if entry is None:
            return
        marks: dict[str, str] = self.ctrl.data.setdefault("controls", {})
        mark = marks.get(entry.unique_id)
        if self.usable:
            if entry.hidden_by == er.RegistryEntryHider.INTEGRATION:
                reg.async_update_entity(self.entity_id, hidden_by=None)
                marks[entry.unique_id] = "shown"
                self.ctrl._save()
        elif mark != "user" and entry.hidden_by is None:
            if mark == "hidden":
                marks[entry.unique_id] = "user"  # shown again by the user
            else:
                reg.async_update_entity(self.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION)
                marks[entry.unique_id] = "hidden"
            self.ctrl._save()


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
            sw_version=integration_version(hub.hass),
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.hub.async_add_listener(self.async_write_ha_state))
