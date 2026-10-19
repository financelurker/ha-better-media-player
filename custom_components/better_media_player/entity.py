"""Common base for all entities of a room."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_AREA,
    CONF_DYNAMIC_CONTROLS,
    CONF_HIDE_DELAY,
    DOMAIN,
    HUB_DEFAULTS,
    NAME,
    SOURCE_AUDIO,
    SOURCE_VIDEO,
)
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


def controls_settings(hass) -> tuple[bool, float]:
    """Dynamic controls on (central settings, default on) and the hide delay (s)."""
    hub = hass.data.get(DOMAIN, {}).get("hub")
    opts = hub.opts if hub is not None else HUB_DEFAULTS
    return bool(opts.get(CONF_DYNAMIC_CONTROLS, True)), float(opts.get(CONF_HIDE_DELAY, 3) or 0)


class Visibility:
    """Hidden (hidden_by: integration) while it makes no sense right now.

    So dashboards only show what can be used in the room's state. Shown at
    once when it makes sense again, hidden only after the hide delay (no
    flicker between two items). A hidden control still works (actions,
    automations); pressing a hidden button gives an error or does nothing
    (see the control). Never against the user: a control they hid stays
    hidden, one they showed again while hidden is not hidden any more. With
    dynamic controls off (central settings) nothing is hidden.
    """

    _hide_unsub: Callable[[], None] | None = None

    @property
    def usable(self) -> bool:
        raise NotImplementedError

    def _visibility_owner(self):
        """The controller (room) or the hub: data (marks), save, listener."""
        raise NotImplementedError

    def _start_visibility(self) -> None:
        owner = self._visibility_owner()
        self.async_on_remove(owner.async_add_listener(self._sync_hidden))
        self.async_on_remove(self._cancel_hide)
        self._sync_hidden(fresh=True)

    @callback
    def _cancel_hide(self) -> None:
        if self._hide_unsub is not None:
            self._hide_unsub()
            self._hide_unsub = None

    @callback
    def _sync_hidden(self, fresh: bool = False) -> None:
        reg = er.async_get(self.hass)
        entry = reg.async_get(self.entity_id) if self.entity_id else None
        if entry is None:
            return
        enabled, delay = controls_settings(self.hass)
        owner = self._visibility_owner()
        marks: dict[str, str] = owner.data.setdefault("controls", {})
        mark = marks.get(entry.unique_id)
        if not enabled or self.usable:
            self._cancel_hide()
            if entry.hidden_by == er.RegistryEntryHider.INTEGRATION:
                reg.async_update_entity(self.entity_id, hidden_by=None)
                marks[entry.unique_id] = "shown"
                owner._save()
            return
        if mark == "user" or entry.hidden_by is not None:
            return
        if mark == "hidden":
            marks[entry.unique_id] = "user"  # shown again by the user
            owner._save()
            return
        if fresh or delay <= 0:
            self._hide(reg)
        elif self._hide_unsub is None:

            @callback
            def _due(_now) -> None:
                self._hide_unsub = None
                if not self.usable and controls_settings(self.hass)[0]:
                    current = reg.async_get(self.entity_id)
                    if current is not None and current.hidden_by is None:
                        self._hide(reg)

            self._hide_unsub = async_call_later(self.hass, delay, _due)

    @callback
    def _hide(self, reg) -> None:
        owner = self._visibility_owner()
        reg.async_update_entity(self.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION)
        owner.data.setdefault("controls", {})[reg.async_get(self.entity_id).unique_id] = "hidden"
        owner._save()


class ControlEntity(Visibility, BetterMediaPlayerEntity):
    """A room control that is hidden while it makes no sense right now."""

    def _visibility_owner(self):
        return self.ctrl

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._start_visibility()


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
