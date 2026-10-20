"""Common base for all entities of a room."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_AREA,
    CONF_DYNAMIC_CONTROLS,
    CONF_ENTRY_TYPE,
    CONF_HIDE_DELAY,
    DOMAIN,
    ENTRY_TYPE_HUB,
    HUB_ENTITY_DEFAULTS,
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


def controls_settings(hass) -> tuple[bool, float] | None:
    """Dynamic controls on (central device, default on) and the hide delay (s).

    None while central settings exist but are not loaded (start, reload):
    nothing changes until their values are known."""
    entry = next(
        (e for e in hass.config_entries.async_entries(DOMAIN) if e.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB), None
    )
    if entry is None:
        values = HUB_ENTITY_DEFAULTS
        return bool(values[CONF_DYNAMIC_CONTROLS]), float(values[CONF_HIDE_DELAY])
    hub = hass.data.get(DOMAIN, {}).get("hub")
    if entry.state is not ConfigEntryState.LOADED or hub is None:
        return None
    return bool(hub.setting(CONF_DYNAMIC_CONTROLS)), float(hub.setting(CONF_HIDE_DELAY) or 0)


class Visibility:
    """Hidden (hidden_by: integration) while it makes no sense right now.

    So dashboards only show what can be used in the room's state. Shown at
    once when it makes sense again, hidden only after the hide delay (no
    flicker between two items), and not before the owner is ready after a
    start (the players have reported). A hidden control still works
    (actions, automations); pressing a hidden button gives an error or does
    nothing (see the control). Never against the user: a control they hid
    stays hidden, one they showed again (after it was hidden) stays shown.
    With dynamic controls off (central settings) nothing is hidden.

    Marks per unique id in the owner's data["controls"]: shown / hidden (by
    us), user_hidden (hidden by the user), user (shown by the user).
    """

    _hide_unsub: Callable[[], None] | None = None
    _hide_delay: float | None = None

    @property
    def usable(self) -> bool:
        raise NotImplementedError

    def _visibility_owner(self):
        """The controller (room) or the hub: data (marks), _save, listener, visibility_ready."""
        raise NotImplementedError

    def _start_visibility(self) -> None:
        owner = self._visibility_owner()
        self.async_on_remove(owner.async_add_listener(self._sync_hidden))
        self.async_on_remove(self._cancel_hide)
        self._sync_hidden()

    @callback
    def _cancel_hide(self) -> None:
        if self._hide_unsub is not None:
            self._hide_unsub()
            self._hide_unsub = None

    @callback
    def _mark(self, uid: str, mark: str) -> None:
        owner = self._visibility_owner()
        marks: dict[str, str] = owner.data.setdefault("controls", {})
        if marks.get(uid) != mark:
            marks[uid] = mark
            owner._save()

    @callback
    def _sync_hidden(self) -> None:
        reg = er.async_get(self.hass)
        entry = reg.async_get(self.entity_id) if self.entity_id else None
        if entry is None:
            return
        settings = controls_settings(self.hass)
        if settings is None:
            return  # the central settings are loading: nothing changes yet
        enabled, delay = settings
        owner = self._visibility_owner()
        uid = entry.unique_id
        mark = owner.data.get("controls", {}).get(uid)
        if entry.hidden_by == er.RegistryEntryHider.USER:
            self._cancel_hide()
            self._mark(uid, "user_hidden")  # the user's: never shown by us
            return
        if not enabled or self.usable:
            self._cancel_hide()
            if entry.hidden_by == er.RegistryEntryHider.INTEGRATION:
                reg.async_update_entity(self.entity_id, hidden_by=None)
                self._mark(uid, "shown")
            return
        if mark == "user" or entry.hidden_by is not None:
            return
        if mark in ("hidden", "user_hidden"):
            self._mark(uid, "user")  # shown again by the user: stays shown
            return
        if not owner.visibility_ready:
            return  # still starting: nothing is hidden before the room is known
        if delay <= 0:
            self._cancel_hide()
            self._hide(reg)
            return
        if self._hide_unsub is not None and self._hide_delay == delay:
            return
        self._cancel_hide()  # (another delay from the central settings)
        self._hide_delay = delay

        @callback
        def _due(_now) -> None:
            self._hide_unsub = None
            current = reg.async_get(self.entity_id)
            if current is not None and current.hidden_by is None:  # (usable again: cancelled)
                self._hide(reg)

        self._hide_unsub = async_call_later(self.hass, delay, _due)

    @callback
    def _hide(self, reg) -> None:
        reg.async_update_entity(self.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION)
        self._mark(reg.async_get(self.entity_id).unique_id, "hidden")


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
