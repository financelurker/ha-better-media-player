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
from .entity import BetterMediaPlayerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterMediaPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    ctrl = entry.runtime_data
    entities: list[BinarySensorEntity] = [ProblemSensor(entry, ctrl, "problem")]
    devices = [n for n in ctrl.power.nodes.values() if n.device is not None]
    if any(n.picture for n in devices):  # (ids as before: "display needed")
        entities.append(DemandSensor(entry, ctrl, "display_demand", True))
    if any(not n.picture for n in devices):  # "speakers needed"
        entities.append(DemandSensor(entry, ctrl, "audio_demand", False))
    async_add_entities(entities)


class DemandSensor(BetterMediaPlayerEntity, BinarySensorEntity):
    """On while a device for the picture (or the sound) is needed (without run-down)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry, ctrl, key: str, picture: bool) -> None:
        super().__init__(entry, ctrl, key)
        self._picture = picture

    @property
    def is_on(self) -> bool:
        return self.ctrl.power.demand(self._picture)


class ProblemSensor(BetterMediaPlayerEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool:
        return self.ctrl.error is not None

    @property
    def extra_state_attributes(self) -> dict:
        return {"reason": self.ctrl.error}
