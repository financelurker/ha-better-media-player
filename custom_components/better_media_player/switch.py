"""Auto-power, quiet and night mode switches."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterMediaPlayerConfigEntry, is_hub
from .const import CONF_DYNAMIC_CONTROLS, ROLE_AUDIO, ROLE_DISPLAY
from .entity import BetterMediaPlayerEntity, ControlEntity, HubEntity, prune_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterMediaPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    if is_hub(entry):
        async_add_entities([HubSwitch(entry, entry.runtime_data, CONF_DYNAMIC_CONTROLS, "mdi:eye-refresh-outline")])
        return
    ctrl = entry.runtime_data
    entities: list[SwitchEntity] = [AutoPowerSwitch(entry, ctrl, "auto_power")]
    if ctrl.audio_player is not None:
        entities.append(QuietSwitch(entry, ctrl, "quiet"))
    entities.append(NightModeSwitch(entry, ctrl, "night_mode"))
    # how the room behaves (set on the device, not in the options)
    for role_key, prefix in ((ROLE_DISPLAY, "display"), (ROLE_AUDIO, "audio")):
        role = ctrl.roles.get(role_key)
        if role is not None and role.power:
            entities.append(SettingSwitch(entry, ctrl, f"{prefix}_standby", "mdi:power-sleep"))
    if ctrl.follow_capable:
        entities.append(SettingSwitch(entry, ctrl, "prewarm", "mdi:speaker-wireless"))
        entities.append(SettingSwitch(entry, ctrl, "wake_detect_left", "mdi:exit-run"))
    prune_entities(hass, entry, "switch", entities)
    async_add_entities(entities)


class SettingSwitch(BetterMediaPlayerEntity, SwitchEntity):
    """A setting of the room's behaviour (on / off)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry, ctrl, key: str, icon: str) -> None:
        super().__init__(entry, ctrl, key)
        self._key = key
        self._attr_icon = icon

    @property
    def is_on(self) -> bool:
        return bool(self.ctrl.setting(self._key))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.ctrl.async_set_setting(self._key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.ctrl.async_set_setting(self._key, False)


class HubSwitch(HubEntity, SwitchEntity):
    """A setting of the central device (on / off)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry, hub, key: str, icon: str) -> None:
        super().__init__(entry, hub, key)
        self._key = key
        self._attr_icon = icon

    @property
    def is_on(self) -> bool:
        return bool(self.hub.setting(self._key))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.hub.async_set_setting(self._key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hub.async_set_setting(self._key, False)


class AutoPowerSwitch(BetterMediaPlayerEntity, SwitchEntity):
    """On: the room switches its power devices automatically."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool:
        return self.ctrl.auto_power

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.ctrl.async_set_auto_power(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.ctrl.async_set_auto_power(False)


class QuietSwitch(ControlEntity, SwitchEntity):
    """On: follow music leaves this room out (e.g. bedroom at night).

    Set ahead (also while nothing plays here, before follow me starts):
    always shown in a room that takes part in follow me; elsewhere only while
    it is on (it has no effect there)."""

    @property
    def usable(self) -> bool:
        return self.ctrl.follow_room or self.ctrl.quiet

    _attr_icon = "mdi:sleep"

    @property
    def is_on(self) -> bool:
        return self.ctrl.quiet

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.ctrl.async_set_quiet(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.ctrl.async_set_quiet(False)


class NightModeSwitch(ControlEntity, SwitchEntity):
    """On: no subwoofer, shorter video times, volume limit. Stays on until turned off.

    Always shown: it is set ahead (also while nothing plays, e.g. at bedtime)
    for what comes later (playback, follow me, a wake)."""

    @property
    def usable(self) -> bool:
        return True

    _attr_icon = "mdi:weather-night"

    @property
    def is_on(self) -> bool:
        return self.ctrl.night.active

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.ctrl.async_set_night_mode(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.ctrl.async_set_night_mode(False)
