"""Volume preset of a room."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterMediaPlayerConfigEntry
from .const import PRESET_MANUAL
from .entity import BetterMediaPlayerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterMediaPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    ctrl = entry.runtime_data
    if ctrl.volume.presets:
        async_add_entities([VolumePresetSelect(entry, ctrl, "volume_preset")])


class VolumePresetSelect(BetterMediaPlayerEntity, SelectEntity):
    """The room's volume by name; "manual" once it was changed otherwise."""

    _attr_icon = "mdi:volume-equal"

    @property
    def options(self) -> list[str]:
        return [*self.ctrl.volume.presets, PRESET_MANUAL]

    @property
    def current_option(self) -> str:
        return self.ctrl.volume.preset or PRESET_MANUAL

    async def async_select_option(self, option: str) -> None:
        await self.ctrl.async_set_volume_preset(None if option == PRESET_MANUAL else option)
