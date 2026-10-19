"""Room status, room follow state and central follow sensor."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterMediaPlayerConfigEntry, is_hub
from .const import FOLLOW_STATES, STATUSES
from .entity import BetterMediaPlayerEntity, HubEntity
from .wake import PHASES


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
    if ctrl.follow_capable:
        entities.append(FollowStateSensor(entry, ctrl, "follow_state"))
    async_add_entities(entities)


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


class SleepEndSensor(BetterMediaPlayerEntity, SensorEntity):
    """When the sleep timer turns the room off (unknown: no timer)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:sleep"

    @property
    def native_value(self) -> datetime | None:
        return self.ctrl.sleep.ends_at

    @property
    def extra_state_attributes(self) -> dict:
        return {"fading": self.ctrl.sleep.fading}


class WakeSensor(BetterMediaPlayerEntity, SensorEntity):
    """Where the room's wake stands (idle: none)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = PHASES
    _attr_icon = "mdi:alarm"

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
        return self.ctrl.follow_state

    @property
    def extra_state_attributes(self) -> dict:
        return {"presence": self.ctrl.presence(), "prewarm": self.ctrl.prewarm}


class FollowSensor(HubEntity, SensorEntity):
    """Rooms that play along with follow me right now."""

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
