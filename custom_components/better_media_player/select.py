"""Volume preset, power-on preset and handover of a room."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterMediaPlayerConfigEntry
from .const import CONF_POWER_ON_PRESET, PRESET_MANUAL, PRESET_NONE, PRESETS
from .controller import RoomController
from .entity import BetterMediaPlayerEntity, ControlEntity
from .hub import get_rooms


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterMediaPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    ctrl = entry.runtime_data
    # always there: the presets can be offered later (number entities)
    async_add_entities(
        [
            VolumePresetSelect(entry, ctrl, "volume_preset"),
            PowerOnPresetSelect(entry, ctrl, CONF_POWER_ON_PRESET),
            HandoverSelect(entry, ctrl, "handover"),
        ]
    )


class PowerOnPresetSelect(BetterMediaPlayerEntity, SelectEntity):
    """The room's volume whenever it comes on (none: as it was)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:volume-source"
    _attr_options = [PRESET_NONE, *PRESETS]

    @property
    def current_option(self) -> str:
        value = self.ctrl.setting(CONF_POWER_ON_PRESET)
        return value if value in self._attr_options else PRESET_NONE

    async def async_select_option(self, option: str) -> None:
        await self.ctrl.async_set_setting(CONF_POWER_ON_PRESET, option)


class VolumePresetSelect(ControlEntity, SelectEntity):
    """The room's volume by name; "manual" once it was changed otherwise."""

    @property
    def usable(self) -> bool:
        return self.ctrl.room_active

    _attr_icon = "mdi:volume-equal"

    @property
    def options(self) -> list[str]:
        return [*self.ctrl.volume.presets, PRESET_MANUAL]

    @property
    def current_option(self) -> str:
        return self.ctrl.volume.preset or PRESET_MANUAL

    async def async_select_option(self, option: str) -> None:
        await self.ctrl.async_set_volume_preset(None if option == PRESET_MANUAL else option)


class HandoverSelect(ControlEntity, SelectEntity):
    """Continue what plays here in another room (by its name); this room goes off."""

    _attr_icon = "mdi:transfer-right"

    def _targets(self) -> dict[str, RoomController]:
        rooms = sorted(get_rooms(self.hass).values(), key=lambda r: r.entry.title)
        return {r.entry.title: r for r in rooms if self.ctrl.handover_blocker(r) is None}

    @property
    def usable(self) -> bool:
        return bool(self._targets())

    @property
    def options(self) -> list[str]:
        return list(self._targets())

    @property
    def current_option(self) -> str | None:
        return None  # an action, not a state

    async def async_select_option(self, option: str) -> None:
        await self.ctrl.async_handover(self._targets().get(option))
