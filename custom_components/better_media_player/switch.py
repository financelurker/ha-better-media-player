"""Auto-power, quiet and night mode switches."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterMediaPlayerConfigEntry
from .entity import BetterMediaPlayerEntity, ControlEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterMediaPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    ctrl = entry.runtime_data
    entities: list[SwitchEntity] = [AutoPowerSwitch(entry, ctrl, "auto_power")]
    if ctrl.audio_player is not None:
        entities.append(QuietSwitch(entry, ctrl, "quiet"))
    entities.append(NightModeSwitch(entry, ctrl, "night_mode"))
    async_add_entities(entities)


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

    Only matters for follow me (also while nothing plays here): shown while
    follow me runs, or while it is on."""

    @property
    def usable(self) -> bool:
        return self.ctrl.follow_running or self.ctrl.quiet

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

    Shown while the room plays, while it is on, and while follow me runs
    (music may come in: it joins limited)."""

    @property
    def usable(self) -> bool:
        return self.ctrl.room_active or self.ctrl.night.active or self.ctrl.follow_running

    _attr_icon = "mdi:weather-night"

    @property
    def is_on(self) -> bool:
        return self.ctrl.night.active

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.ctrl.async_set_night_mode(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.ctrl.async_set_night_mode(False)
