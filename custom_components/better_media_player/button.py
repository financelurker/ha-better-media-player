"""Room buttons: all off, sleep timer and wake controls."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterMediaPlayerConfigEntry
from .const import WAKE_END_STOPPED
from .entity import ControlEntity, prune_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BetterMediaPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    ctrl = entry.runtime_data
    entities: list[ButtonEntity] = [
        AllOffButton(entry, ctrl, "all_off"),
        SleepPlusButton(entry, ctrl, "sleep_plus_15"),
        WakeSnoozeButton(entry, ctrl, "wake_snooze"),
        WakeStopButton(entry, ctrl, "wake_stop"),
    ]
    prune_entities(hass, entry, "button", entities)  # (e.g. the follow me buttons, back after a downgrade)
    async_add_entities(entities)


class AllOffButton(ControlEntity, ButtonEntity):
    """Same as turning the room player off: stop sources, power down."""

    @property
    def usable(self) -> bool:
        return self.ctrl.room_active or self.ctrl.power.devices.has_power

    async def async_press(self) -> None:
        await self.ctrl.async_turn_off(all_off=True)


class SleepPlusButton(ControlEntity, ButtonEntity):
    """Sleep timer +15 minutes (starts one if none runs)."""


    @property
    def usable(self) -> bool:
        return self.ctrl.room_active or self.ctrl.sleep.active

    async def async_press(self) -> None:
        await self.ctrl.async_extend_sleep_timer(15)


class WakeSnoozeButton(ControlEntity, ButtonEntity):

    @property
    def usable(self) -> bool:
        return self.ctrl.wake.active

    async def async_press(self) -> None:
        await self.ctrl.wake.async_snooze(None)


class WakeStopButton(ControlEntity, ButtonEntity):

    @property
    def usable(self) -> bool:
        return self.ctrl.wake.active

    async def async_press(self) -> None:
        await self.ctrl.wake.async_finish(WAKE_END_STOPPED)

