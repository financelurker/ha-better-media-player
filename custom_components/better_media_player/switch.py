"""Auto-power, keep on, quiet and night mode switches."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterMediaPlayerConfigEntry
from .entity import BetterMediaPlayerEntity, ControlEntity, prune_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterMediaPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    ctrl = entry.runtime_data
    entities: list[SwitchEntity] = [AutoPowerSwitch(entry, ctrl, "auto_power"), KeepOnSwitch(entry, ctrl, "keep_on")]
    if ctrl.audio_player is not None:
        entities.append(QuietSwitch(entry, ctrl, "quiet"))
    entities.append(NightModeSwitch(entry, ctrl, "night_mode"))
    prune_entities(hass, entry, "switch", entities)
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


class KeepOnSwitch(ControlEntity, SwitchEntity):
    """On: nothing is switched off automatically (run-down, night, idle player);
    what is on stays on. Starting still switches on. Stays on until turned off
    or All off (also over a restart).

    Always shown: it is set ahead too (before the room stops)."""

    @property
    def usable(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.ctrl.keep_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.ctrl.async_set_keep_on(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.ctrl.async_set_keep_on(False)


class QuietSwitch(ControlEntity, SwitchEntity):
    """On: follow music leaves this room out (e.g. bedroom at night).

    Set ahead (also while nothing plays here, before follow me starts):
    always shown in a room that takes part in follow me; elsewhere only while
    it is on (it has no effect there)."""

    @property
    def usable(self) -> bool:
        return self.ctrl.follow.takes_part or self.ctrl.quiet


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


    @property
    def is_on(self) -> bool:
        return self.ctrl.night.active

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.ctrl.async_set_night_mode(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.ctrl.async_set_night_mode(False)
