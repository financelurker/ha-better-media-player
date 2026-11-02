"""Room status, room follow state and central follow sensor."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterMediaPlayerConfigEntry, is_hub
from .const import FOLLOW_STATES, STATUSES
from .entity import BetterMediaPlayerEntity, ControlEntity, HubEntity, Visibility
from .room.wake import PHASES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterMediaPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    if is_hub(entry):
        async_add_entities([FollowSensor(entry, entry.runtime_data, "follow")])
        return
    ctrl = entry.runtime_data
    entities: list[SensorEntity] = [
        StatusSensor(entry, ctrl, "status"),
        SleepEndSensor(entry, ctrl, "sleep_timer_end"),
        WakeSensor(entry, ctrl, "wake"),
    ]
    if ctrl.follow.capable:
        entities.append(FollowStateSensor(entry, ctrl, "follow_state"))
    off_at = [OffAtSensor(entry, ctrl, key) for key in ctrl.power.nodes]
    # a device removed from the room takes its sensor along
    keep = {e.unique_id for e in off_at}
    reg = er.async_get(hass)
    for item in er.async_entries_for_config_entry(reg, entry.entry_id):
        if item.domain == "sensor" and item.unique_id.endswith(OFF_AT) and item.unique_id not in keep:
            reg.async_remove(item.entity_id)
    async_add_entities(entities + off_at)


OFF_AT = "_off_at"


class OffAtSensor(BetterMediaPlayerEntity, SensorEntity):
    """When a device (or a player's hardware plug) goes off after its run-down;
    unknown while none is planned (needed, off, kept on, automatic off)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry, ctrl, key: str) -> None:
        super().__init__(entry, ctrl, f"{key}{OFF_AT}")
        self._key = key
        node = ctrl.power.nodes[key]
        if node.device is None:
            self._attr_translation_key = f"{node.sources[0]}_host_off_at"
        elif node.device.get("name"):
            self._attr_translation_key = "device_off_at"
            self._attr_translation_placeholders = {"device": node.device["name"]}
        else:
            self._attr_translation_key = f"{node.device['kind']}_off_at"
        # the entity id from the device's id (it stays when the device is renamed)
        self.entity_id = f"sensor.{slugify(entry.title)}_{key}{OFF_AT}"

    @property
    def native_value(self) -> datetime | None:
        at = self.ctrl.run_down_at.get(self._key)
        return dt_util.utc_from_timestamp(at) if at is not None else None


class StatusSensor(BetterMediaPlayerEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUSES
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str:
        return self.ctrl.status

    @property
    def extra_state_attributes(self) -> dict:
        ctrl = self.ctrl
        src = ctrl.active_source
        return {
            "active_source": src.key if src else None,
            "last_source": ctrl.last_source,
            "error": ctrl.error,
            "suppressed_sources": [s.key for s in ctrl.sources.values() if s.suppressed],
        }


class SleepEndSensor(ControlEntity, SensorEntity):
    """When the sleep timer turns the room off (unknown: no timer)."""

    @property
    def usable(self) -> bool:
        return self.ctrl.sleep.active

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        return self.ctrl.sleep.ends_at

    @property
    def extra_state_attributes(self) -> dict:
        return {"fading": self.ctrl.sleep.fading}


class WakeSensor(ControlEntity, SensorEntity):
    """Where the room's wake stands (idle: none)."""

    @property
    def usable(self) -> bool:
        return self.ctrl.wake.active

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = PHASES

    @property
    def native_value(self) -> str:
        return self.ctrl.wake.phase

    @property
    def extra_state_attributes(self) -> dict:
        return self.ctrl.wake.attributes


class FollowStateSensor(BetterMediaPlayerEntity, SensorEntity):
    """Where this room stands in the current follow session."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = FOLLOW_STATES
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str:
        return self.ctrl.follow.state

    @property
    def extra_state_attributes(self) -> dict:
        return {"presence": self.ctrl.follow.presence(), "prewarm": self.ctrl.follow.prewarm}


class FollowSensor(Visibility, HubEntity, SensorEntity):
    """Rooms that play along with follow me right now."""

    @property
    def usable(self) -> bool:
        return self.hub.active

    def _visibility_owner(self):
        return self.hub

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._start_visibility()

    _attr_native_unit_of_measurement = "rooms"

    @property
    def native_value(self) -> int:
        return len(self.hub.follow_members)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "rooms": self.hub.follow_members,
            "follow_player": self.hub.follow_player,
            "active": self.hub.active,
            "started_in": self.hub.origin.entry.title if self.hub.origin else None,
        }
