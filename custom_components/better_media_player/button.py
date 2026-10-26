"""Room buttons: all off, sleep timer, wake and follow me controls."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BetterMediaPlayerConfigEntry
from .entity import ControlEntity
from .follow import async_start_follow, async_stop_follow, get_hub


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
    if ctrl.follow.capable:
        entities.append(FollowStartButton(entry, ctrl, "follow_start"))
    if ctrl.audio_player is not None:
        entities.append(FollowStopButton(entry, ctrl, "follow_stop"))
    async_add_entities(entities)


class AllOffButton(ControlEntity, ButtonEntity):
    """Same as turning the room player off: stop sources, power down."""

    @property
    def usable(self) -> bool:
        return self.ctrl.room_active or self.ctrl.power.has_power

    async def async_press(self) -> None:
        await self.ctrl.async_turn_off()


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
        await self.ctrl.wake.async_finish("stopped")


class FollowStartButton(ControlEntity, ButtonEntity):
    """Follow me from here: only while music plays on this room's own player."""


    @property
    def usable(self) -> bool:
        hub = get_hub(self.hass)
        return hub is not None and hub.can_start(self.ctrl)

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Why it cannot be started here right now (None: it can)."""
        hub = get_hub(self.hass)
        blocker = hub.start_blocker(self.ctrl) if hub is not None else ("no_follow_player", {})
        return {"blocked_by": blocker[0] if blocker else None}

    async def async_press(self) -> None:
        await async_start_follow(self.hass, self.ctrl)


class FollowStopButton(ControlEntity, ButtonEntity):
    """Follow me off: the music stays here (or goes back where it was started)."""


    @property
    def usable(self) -> bool:
        hub = get_hub(self.hass)
        return hub is not None and hub.active

    async def async_press(self) -> None:
        await async_stop_follow(self.hass, self.ctrl)
