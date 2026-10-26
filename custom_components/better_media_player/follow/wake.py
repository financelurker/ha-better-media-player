"""Follow wake, central side: the wake room in the follow-me player until it is left.

The room's side (start, fade, snooze of the wake) is in room/wake.py; it calls
async_start, async_resume and async_snooze here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .. import clock
from ..const import CONF_WAKE_DETECT_LEFT, DOMAIN, FOLLOW_WAITING, FOLLOW_WAKING, WAKE_ARRIVAL_WINDOW
from . import EMPTY, PLAYING, PRESENT, START_GRACE_SECONDS, UNKNOWN

if TYPE_CHECKING:
    from ..room.controller import RoomController
    from .manager import FollowManager

_LOGGER = logging.getLogger(__name__)


class FollowWake:
    """A follow wake's room in the follow-me player."""

    def __init__(self, manager: FollowManager) -> None:
        self.manager = manager
        self.hass = manager.hass
        # when presence newly appeared per room (to tell "left" from a sensor gap)
        self._presence_before: dict[str, str] = {}
        self._arrivals: dict[str, float] = {}

    async def async_room(self, room: RoomController, group: str, member: bool, presence: str, now: float) -> None:
        """The wake room of a follow wake: in the group until it is left.

        Left = presence was seen in it after the wake started, it then ran down,
        and someone turned up in another follow room. Never seen (motion sensors
        miss sleepers): it stays until the wake ends.
        """
        s = self.manager.session
        rid = room.entry.entry_id
        s.backed.add(rid)  # no override rules for it
        if presence == PRESENT:
            room.wake.mark_seen()
            s.empty_since.pop(rid, None)
        elif presence == UNKNOWN:
            s.empty_since.pop(rid, None)  # unknown is no "empty" (no left from a sensor outage)
        if not member:
            s.member_since.pop(rid, None)
            if rid not in s.leaving and room.wake.phase != "starting":
                await self.manager._join(room, group, keep_volume=True)  # dropped by MA: back in
            room.follow.set_state(FOLLOW_WAKING)
            return
        s.member_since.setdefault(rid, now)
        s.leaving.discard(rid)
        if room.wake.seen and presence == EMPTY and room.setting(CONF_WAKE_DETECT_LEFT):
            since = max(s.empty_since.setdefault(rid, now), s.member_since[rid])
            if now - since >= room.follow.presence_run_down and self._arrived_elsewhere(room):
                _LOGGER.info("Follow wake: %s was left, the wake is done", room.entry.title)
                await room.wake.async_finish("left")
                await self.manager._leave(room, "left after waking up")
                room.follow.set_state(FOLLOW_WAITING)
                return
        room.follow.set_state(FOLLOW_WAKING)

    def _arrived_elsewhere(self, room: RoomController) -> bool:
        """Someone turned up in another follow room around the time the wake room
        went empty (a sensor there may switch on before the wake room's goes off).
        A gap of the wake room's sensor alone (a sleeper missed) is no "left"."""
        last = room.wake.last_present
        if last is None:
            return False
        rid = room.entry.entry_id
        return any(ts >= last - WAKE_ARRIVAL_WINDOW for other, ts in self._arrivals.items() if other != rid)

    @callback
    def track_arrivals(self, now: float) -> None:
        """Remember when presence newly appeared in a follow room."""
        for room in self.manager.follow_rooms():
            rid = room.entry.entry_id
            presence = room.follow.presence()
            before = self._presence_before.get(rid)
            if presence == PRESENT and before is not None and before != PRESENT:
                self._arrivals[rid] = now
            if presence != UNKNOWN:
                self._presence_before[rid] = presence

    def _running(self) -> bool:
        """Follow me is on and plays (or was just started: MA may report it late)."""
        s = self.manager.session
        group = self.manager.follow_player
        return self.manager.active and (
            self.manager.ma.state(group) in PLAYING
            or (s.group == group and s.started_by_us and clock.now() - s.started_at < START_GRACE_SECONDS)
        )

    def _require_follow_player(self) -> str:
        if self.manager.follow_player is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_follow_player")
        return self.manager.follow_player

    async def async_start(
        self, room: RoomController, media: dict[str, Any] | None, volume: float | None
    ) -> str:
        """Follow wake: the wake room joins at its start volume.

        If follow me plays, the wake room joins it (its media goes on). Otherwise
        follow me starts from the wake room with the wake's media: players left
        in the group from before leave it, and rooms occupied at that moment
        stay out until their presence went and came again.
        """
        s = self.manager.session
        async with self.manager._lock:
            group = self._require_follow_player()
            running = self._running()
            if not running:
                self.manager._activate(room)
                for other in self.manager.follow_rooms():
                    if other is not room and other.follow.presence() == PRESENT:
                        s.held_out.add(other.entry.entry_id)
                for member in self.manager.ma.members(group):
                    if member != room.audio_player:
                        await self.manager._unjoin(member, "a follow wake starts follow me")
            s.backed.add(room.entry.entry_id)
            s.leaving.discard(room.entry.entry_id)
            s.opted_out.discard(room.entry.entry_id)
            s.held_out.discard(room.entry.entry_id)
            if room.audio_player not in self.manager.ma.members(group):
                self.manager._commands.pop((room.audio_player, f"join:{group}"), None)
                await self.manager._join(room, group, volume_override=volume)
            if not running:
                if media:
                    ok = await self.manager._call("play_media", {ATTR_ENTITY_ID: group, **media})
                else:
                    ok = await self.manager._call("media_play", {ATTR_ENTITY_ID: group})
                if not ok:
                    raise HomeAssistantError(f"Could not start playback on {group}")
        self.manager.async_schedule("wake")
        return group

    async def async_resume(self, room: RoomController, volume: float | None) -> str:
        """After a snooze: back into the follow-me player (playing it again if it paused)."""
        async with self.manager._lock:
            group = self._require_follow_player()
            if not self.manager.active:
                self.manager._activate(room)
            s = self.manager.session
            s.backed.add(room.entry.entry_id)
            s.leaving.discard(room.entry.entry_id)
            if room.audio_player not in self.manager.ma.members(group):
                self.manager._commands.pop((room.audio_player, f"join:{group}"), None)
                await self.manager._join(room, group, volume_override=volume)
            if self.manager.ma.state(group) not in PLAYING:
                s.started_at, s.started_by_us = clock.now(), True
                self.manager._commands.pop((group, "play"), None)
                await self.manager._resume(group)
        self.manager.async_schedule("wake")
        return group

    async def async_snooze(self, room: RoomController) -> None:
        """Snooze of a follow wake: only this room leaves the group meanwhile."""
        if room.audio_player in self.manager.ma.members(self.manager.session.group):
            await self.manager._leave(room, "snoozed")
        self.manager.async_schedule("wake_snooze")
