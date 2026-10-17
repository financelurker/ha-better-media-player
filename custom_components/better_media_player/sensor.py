"""Room status sensor."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterMediaPlayerConfigEntry
from .const import STATUSES
from .entity import BetterMediaPlayerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterMediaPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([StatusSensor(entry, entry.runtime_data, "status")])


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
