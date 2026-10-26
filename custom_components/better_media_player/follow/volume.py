"""Follow me's volume: the group's base volume, remembered, and night mode in groups.

The base volume is the group's volume without the rooms' offsets. Night mode
limits a group whose players all belong to rooms in night mode; a member whose
room leaves the night mode fades back to the group's volume.
"""

from __future__ import annotations

import logging
import statistics
from typing import TYPE_CHECKING

from homeassistant.const import ATTR_ENTITY_ID

from ..const import CONF_DEFAULT_VOLUME, CONF_NIGHT_FADE_IN
from ..room import get_rooms
from ..room.output import MemberOutput
from ..room.ramp import VolumeRamp
from . import PLAYING

if TYPE_CHECKING:
    from ..room.controller import RoomController
    from .manager import FollowManager

_LOGGER = logging.getLogger(__name__)


class FollowVolume:
    """The volume side of follow me (and of night mode in any Music Assistant group)."""

    def __init__(self, manager: FollowManager) -> None:
        self.manager = manager
        self.hass = manager.hass
        # night mode: members limited per group, members fading back
        self._group_ramps: dict[str, VolumeRamp] = {}
        self._night_limited: dict[str, set[str]] = {}

    def stop(self) -> None:
        for ramp in self._group_ramps.values():
            ramp.stop()

    def base_volume(self, group: str) -> float:
        """Group volume without the room offsets, in %.

        Music Assistant reports the group volume as the members' average, which
        already contains their offsets; subtracting the average offset keeps the
        base stable from session to session.
        """
        state = self.hass.states.get(group)
        vol = state.attributes.get("volume_level") if state else None
        members = self.manager.ma.members(group)
        faded = self._faded_members(members)
        if faded:
            # members whose volume is no wish (sleep fade, night limit, wake): the
            # group average is not the real volume; use the others' own volumes,
            # or what was remembered
            vols = []
            for room in get_rooms(self.hass).values():
                if room.audio_player in members and room.audio_player not in faded:
                    v = self.player_volume(room.audio_player)
                    if v is not None and v > 0:
                        vols.append(v * 100 - room.follow.volume_offset)
            if vols:
                return max(0.0, min(100.0, statistics.fmean(vols)))
            vol = None
        if vol is not None and float(vol) > 0 and members:
            by_player = {r.audio_player: r.follow.volume_offset for r in get_rooms(self.hass).values()}
            offset = statistics.fmean([by_player.get(p, 0.0) for p in members])
            return max(0.0, min(100.0, float(vol) * 100 - offset))
        stored = self.manager.data.get("volumes", {}).get(group)
        if stored:
            return float(stored)
        return float(self.manager.setting(CONF_DEFAULT_VOLUME))

    def _faded_members(self, members: list[str]) -> set[str]:
        """Members whose volume is not a wish right now: a sleep timer lowers
        it, the night mode limits it, or a wake fades it."""
        return {
            r.audio_player
            for r in get_rooms(self.hass).values()
            if r.audio_player in members
            and (
                r.sleep.fading_player(r.audio_player)
                or r.night.player_cap is not None
                or r.wake.owns(r.audio_player)
            )
        }

    def player_volume(self, player: str) -> float | None:
        state = self.hass.states.get(player)
        value = state.attributes.get("volume_level") if state else None
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def remember(self, group: str) -> None:
        state = self.hass.states.get(group)
        vol = state.attributes.get("volume_level") if state else None
        members = self.manager.ma.members(group)
        if vol is None or float(vol) <= 0 or not members:
            return
        if self._faded_members(members) or self.member_ramping(members):
            return  # not set by someone: limited, fading, or fading back
        base = round(self.base_volume(group), 1)
        if self.manager.data["volumes"].get(group) != base:
            self.manager.data["volumes"][group] = base
            self.manager._save_later()

    async def async_night_groups(self) -> None:
        """Night mode and Music Assistant groups.

        - A group whose players all belong to rooms in night mode is limited
          as a whole (to the highest of their limits; each member is limited
          to its own room's anyway).
        - A member whose room leaves the night mode fades back to the group's
          volume for it (follow me: the remembered base plus the room's offset;
          other groups: the group's volume from before the night), at once if
          the group does not play. The group volume itself is not raised: that
          would raise the members still in night mode as well.
        """
        by_player = {r.audio_player: r for r in get_rooms(self.hass).values() if r.audio_player}
        free: dict[str, float] = self.manager.data.setdefault("free_volumes", {})
        for group in self.manager.ma.groups():
            members = self.manager.ma.members(group)
            limited = {p for p in members if p in by_player and by_player[p].night.player_cap is not None}
            was = self._night_limited.get(group, set())
            self._night_limited[group] = limited
            caps = [by_player[p].night.player_cap if p in by_player else None for p in members]
            cap = max(caps) if members and all(c is not None for c in caps) else None
            vol = self.player_volume(group)
            if cap is not None:
                if vol is not None and vol > cap + 0.011 and self.manager._due(group, "night_cap"):
                    _LOGGER.debug("%s limited to %.2f (night mode)", group, cap)
                    await self.manager._call("volume_set", {ATTR_ENTITY_ID: group, "volume_level": cap})
            elif (
                not limited
                and not self.member_ramping(members)
                and members
                and vol is not None
                and vol > 0
                and free.get(group) != vol
            ):
                free[group] = vol  # the group's volume while nobody limits it
                self.manager._save_later()
            for player in (was - limited) & set(members):
                await self._member_back(group, by_player[player], free.get(group))

    async def _member_back(self, group: str, room: RoomController, free: float | None) -> None:
        """The member's room left the night mode: back to the group's volume."""
        base = self.manager.data.get("volumes", {}).get(group) if group == self.manager.follow_player else None
        if base is not None:
            target = max(0.0, min(100.0, float(base) + room.follow.volume_offset)) / 100
        elif free is not None:
            target = free
        else:
            return  # nothing known: stays
        player = room.audio_player
        seconds = float(room.setting(CONF_NIGHT_FADE_IN)) * 60 if self.manager.ma.state(group) in PLAYING else 0
        room.volume.async_cancel_fade(player)
        ramp = self._group_ramps.get(player)
        if ramp is None:
            ramp = self._group_ramps[player] = VolumeRamp(
                self.hass, MemberOutput(self.hass, player, self._member_setter(room)), self.manager._spawn
            )
        ramp.start(target, seconds)

    def _member_setter(self, room: RoomController):
        async def _set(volume: float) -> None:
            room.volume.note_own(room.audio_player, volume)  # the group's volume, not a room wish
            await self.manager._call("volume_set", {ATTR_ENTITY_ID: room.audio_player, "volume_level": volume})

        return _set

    def member_ramping(self, members: list[str]) -> bool:
        return any((r := self._group_ramps.get(p)) is not None and r.running for p in members)
