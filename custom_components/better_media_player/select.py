"""Volume preset and handover of a room."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterMediaPlayerConfigEntry
from .const import PRESET_MANUAL
from .room.controller import RoomController
from .entity import ControlEntity, prune_entities
from .room import get_rooms


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterMediaPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    ctrl = entry.runtime_data
    # always there: the presets can be offered later (settings)
    entities: list[SelectEntity] = [
        VolumePresetSelect(entry, ctrl, "volume_preset"),
        HandoverSelect(entry, ctrl, "handover"),
    ]
    prune_entities(hass, entry, "select", entities)
    async_add_entities(entities)


class VolumePresetSelect(ControlEntity, SelectEntity):
    """The room's volume by name; "manual" once it was changed otherwise."""

    @property
    def usable(self) -> bool:
        return self.ctrl.room_active


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


    def _targets(self) -> dict[str, RoomController]:
        rooms = sorted(get_rooms(self.hass).values(), key=lambda r: r.entry.title)
        return {r.entry.title: r for r in rooms if self.ctrl.handover.blocker(r) is None}

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
        await self.ctrl.handover.async_hand_over(self._targets().get(option))
