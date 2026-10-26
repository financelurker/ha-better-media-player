"""Demand and problem binary sensors."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterMediaPlayerConfigEntry
from .const import ROLE_AUDIO, ROLE_BASS, ROLE_DISPLAY
from .entity import BetterMediaPlayerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterMediaPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    ctrl = entry.runtime_data
    entities: list[BinarySensorEntity] = [ProblemSensor(entry, ctrl, "problem")]
    if ROLE_DISPLAY in ctrl.power.roles:
        entities.append(DemandSensor(entry, ctrl, "display_demand", ROLE_DISPLAY))
    if ROLE_AUDIO in ctrl.power.roles or ROLE_BASS in ctrl.power.roles:
        role = ROLE_AUDIO if ROLE_AUDIO in ctrl.power.roles else ROLE_BASS
        entities.append(DemandSensor(entry, ctrl, "audio_demand", role))
    async_add_entities(entities)


class DemandSensor(BetterMediaPlayerEntity, BinarySensorEntity):
    """On while a source needs this role (without run-down)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry, ctrl, key: str, role: str) -> None:
        super().__init__(entry, ctrl, key)
        self._role = role

    @property
    def is_on(self) -> bool:
        return self.ctrl.power.demand(self._role)


class ProblemSensor(BetterMediaPlayerEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool:
        return self.ctrl.error is not None

    @property
    def extra_state_attributes(self) -> dict:
        return {"reason": self.ctrl.error}
