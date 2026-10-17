"""Auto-power and quiet switches."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterMediaPlayerConfigEntry
from .entity import BetterMediaPlayerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterMediaPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    ctrl = entry.runtime_data
    entities: list[SwitchEntity] = [AutoPowerSwitch(entry, ctrl, "auto_power")]
    if ctrl.audio_player is not None:
        entities.append(QuietSwitch(entry, ctrl, "quiet"))
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


class QuietSwitch(BetterMediaPlayerEntity, SwitchEntity):
    """On: follow music leaves this room out (e.g. bedroom at night)."""

    _attr_icon = "mdi:sleep"

    @property
    def is_on(self) -> bool:
        return self.ctrl.quiet

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.ctrl.async_set_quiet(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.ctrl.async_set_quiet(False)
