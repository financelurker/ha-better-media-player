"""The sleep timer as a number entity (the room's settings are set with set_setting)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterMediaPlayerConfigEntry
from .entity import ControlEntity, prune_entities
from .room.sleep import MAX_MINUTES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterMediaPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    entities = [SleepTimerNumber(entry, entry.runtime_data, "sleep_timer")]
    prune_entities(hass, entry, "number", entities)
    async_add_entities(entities)


class SleepTimerNumber(ControlEntity, NumberEntity):
    """Minutes until the room turns itself off; 0 = no timer."""

    @property
    def usable(self) -> bool:
        return self.ctrl.room_active or self.ctrl.sleep.active

    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_MINUTES
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    @property
    def native_value(self) -> float:
        return float(self.ctrl.sleep.remaining_minutes)

    async def async_set_native_value(self, value: float) -> None:
        await self.ctrl.async_set_sleep_timer(value)
