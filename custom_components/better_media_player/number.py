"""Run-down times as number entities."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterMediaPlayerConfigEntry
from .const import (
    ROLE_AUDIO,
    ROLE_AUDIO_HOST,
    ROLE_BASS,
    ROLE_DISPLAY,
    ROLE_VIDEO_HOST,
    SOURCE_VIDEO,
)
from .entity import BetterMediaPlayerEntity
from .sleep import MAX_MINUTES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterMediaPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    ctrl = entry.runtime_data
    entities = []
    if ROLE_DISPLAY in ctrl.roles:
        entities.append(MinutesNumber(entry, ctrl, "display_run_down", 0, 120))
    if ROLE_AUDIO in ctrl.roles or ROLE_BASS in ctrl.roles:
        entities.append(MinutesNumber(entry, ctrl, "audio_run_down", 0, 120))
    if ROLE_VIDEO_HOST in ctrl.roles or ROLE_AUDIO_HOST in ctrl.roles:
        entities.append(MinutesNumber(entry, ctrl, "host_run_down", 0, 120))
    if SOURCE_VIDEO in ctrl.sources:
        entities.append(MinutesNumber(entry, ctrl, "video_idle_timeout", 0, 480))
        entities.append(MinutesNumber(entry, ctrl, "night_video_idle_timeout", 0, 480))
    if ROLE_DISPLAY in ctrl.roles:
        entities.append(MinutesNumber(entry, ctrl, "night_display_run_down", 0, 120))
    entities.append(PercentNumber(entry, ctrl, "night_max_volume"))
    entities.append(SleepTimerNumber(entry, ctrl, "sleep_timer"))
    async_add_entities(entities)


class MinutesNumber(BetterMediaPlayerEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, entry, ctrl, key: str, minimum: float, maximum: float) -> None:
        super().__init__(entry, ctrl, key)
        self._key = key
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum

    @property
    def native_value(self) -> float:
        return float(self.ctrl.data.get(self._key, 0))

    async def async_set_native_value(self, value: float) -> None:
        await self.ctrl.async_set_number(self._key, value)


class PercentNumber(MinutesNumber):
    """Night mode volume limit in %; 0 = no limit."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:volume-medium"

    def __init__(self, entry, ctrl, key: str) -> None:
        super().__init__(entry, ctrl, key, 0, 100)

    async def async_set_native_value(self, value: float) -> None:
        await self.ctrl.async_set_number(self._key, value)
        await self.ctrl.night.async_limit_changed()


class SleepTimerNumber(BetterMediaPlayerEntity, NumberEntity):
    """Minutes until the room turns itself off; 0 = no timer."""

    _attr_icon = "mdi:sleep"
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
