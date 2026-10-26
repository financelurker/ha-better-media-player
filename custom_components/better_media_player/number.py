"""Run-down times as number entities."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterMediaPlayerConfigEntry, is_hub
from .const import (
    CONF_DEFAULT_VOLUME,
    CONF_HIDE_DELAY,
    CONF_PRESET,
    PRESETS,
    ROLE_AUDIO,
    ROLE_AUDIO_HOST,
    ROLE_BASS,
    ROLE_DISPLAY,
    ROLE_VIDEO_HOST,
    SOURCE_AUDIO,
    SOURCE_VIDEO,
)
from .entity import BetterMediaPlayerEntity, ControlEntity, HubEntity, prune_entities
from .room.sleep import MAX_MINUTES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterMediaPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    if is_hub(entry):
        hub = entry.runtime_data
        async_add_entities(
            [
                HubNumber(entry, hub, CONF_DEFAULT_VOLUME, 1, 100, 1, PERCENTAGE),
                HubNumber(entry, hub, CONF_HIDE_DELAY, 0, 300, 1, UnitOfTime.SECONDS),
            ]
        )
        return
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
    entities += [PresetNumber(entry, ctrl, name) for name in PRESETS]
    entities.append(SettingNumber(entry, ctrl, "preset_fade", 0, 30, 0.5, UnitOfTime.SECONDS))
    if SOURCE_VIDEO in ctrl.sources and SOURCE_AUDIO in ctrl.sources:
        entities.append(SettingNumber(entry, ctrl, "announce_video_lower", 0, 100, 5, PERCENTAGE))
    # how the room behaves (set on the device, not in the options)
    s, mins = UnitOfTime.SECONDS, UnitOfTime.MINUTES
    entities.append(SettingNumber(entry, ctrl, "start_timeout", 10, 600, 5, s))
    for role_key, prefix in ((ROLE_DISPLAY, "display"), (ROLE_AUDIO, "audio")):
        role = ctrl.roles.get(role_key)
        if role is None:
            continue
        entities.append(SettingNumber(entry, ctrl, f"{prefix}_on_wait", 0, 120, 1, s))
        if ctrl.can_standby(role):
            entities.append(SettingNumber(entry, ctrl, f"{prefix}_standby_w", 0, 200, 0.5, UnitOfPower.WATT))
            entities.append(SettingNumber(entry, ctrl, f"{prefix}_standby_max", 1, 120, 1, mins))
    if ROLE_VIDEO_HOST in ctrl.roles or ROLE_AUDIO_HOST in ctrl.roles:
        entities.append(SettingNumber(entry, ctrl, "host_on_wait", 0, 120, 1, s))
    entities.append(SettingNumber(entry, ctrl, "sleep_fade", 0, 60, 1, mins))
    entities.append(SettingNumber(entry, ctrl, "snooze_minutes", 1, 120, 1, mins))
    entities.append(SettingNumber(entry, ctrl, "night_fade_in", 0, 30, 1, mins))
    if ctrl.follow_capable:
        entities.append(SettingNumber(entry, ctrl, "presence_run_down", 0, 60, 0.5, mins))
        entities.append(SettingNumber(entry, ctrl, "volume_offset", -50, 50, 1, PERCENTAGE))
    entities.append(SleepTimerNumber(entry, ctrl, "sleep_timer"))
    prune_entities(hass, entry, "number", entities)
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

    def __init__(self, entry, ctrl, key: str) -> None:
        super().__init__(entry, ctrl, key, 0, 100)

    async def async_set_native_value(self, value: float) -> None:
        await self.ctrl.async_set_number(self._key, value)
        await self.ctrl.night.async_limit_changed()


class PresetNumber(MinutesNumber):
    """The volume of a preset in %; 0 = not offered in the room."""

    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, entry, ctrl, name: str) -> None:
        super().__init__(entry, ctrl, CONF_PRESET.format(name), 0, 100)
        self._name = name

    async def async_set_native_value(self, value: float) -> None:
        await self.ctrl.async_set_number(self._key, value)
        await self.ctrl.volume.async_preset_changed(self._name)


class SettingNumber(MinutesNumber):
    """A setting of the room's behaviour (seconds, minutes, %, W)."""

    def __init__(self, entry, ctrl, key: str, minimum: float, maximum: float, step: float, unit: str) -> None:
        super().__init__(entry, ctrl, key, minimum, maximum)
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit

    @property
    def native_value(self) -> float:
        return float(self.ctrl.setting(self._key))


class HubNumber(HubEntity, NumberEntity):
    """A setting of the central device."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, entry, hub, key: str, minimum: float, maximum: float, step: float, unit: str) -> None:
        super().__init__(entry, hub, key)
        self._key = key
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit

    @property
    def native_value(self) -> float:
        return float(self.hub.setting(self._key))

    async def async_set_native_value(self, value: float) -> None:
        await self.hub.async_set_setting(self._key, float(value))


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
