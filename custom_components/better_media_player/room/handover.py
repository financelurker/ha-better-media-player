"""Handover: what plays in this room goes on in another room, this room goes off."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from ..const import DOMAIN, SOURCE_AUDIO, SOURCE_VIDEO
from .power import GROUP_ACTIVE_STATES

if TYPE_CHECKING:
    from .controller import RoomController, Source

_LOGGER = logging.getLogger(__name__)


class RoomHandover:
    """The room's side of a handover (the source room's)."""

    def __init__(self, ctrl: RoomController) -> None:
        self.ctrl = ctrl

    def source(self, source: str | None = None) -> Source | None:
        """What would be handed over: the given source, or the one that plays
        (paused counts; music also in a group)."""
        ctrl = self.ctrl
        src = ctrl.sources.get(source) if source else ctrl.active_source
        if src is None:
            return None
        state = ctrl.player_state(src)
        if src.key == SOURCE_VIDEO:
            return src if state in GROUP_ACTIVE_STATES else None
        if state in GROUP_ACTIVE_STATES or any(
            ctrl.player_state_of(g) in GROUP_ACTIVE_STATES for g in src.backend.groups_of(src.player)
        ):
            return src
        return None

    def blocker(
        self, dest: RoomController | None, source: str | None = None, keep: bool = False, dest_id: str | None = None
    ):
        """Why the playback cannot go to dest (None: it can): (reason, placeholders)."""
        ctrl = self.ctrl
        names = {"room": ctrl.entry.title, "destination": dest.entry.title if dest else str(dest_id)}
        if dest is None:
            return "handover_not_a_room", names
        if dest is ctrl:
            return "handover_same_room", names
        src = self.source(source)
        if src is None:
            return "handover_nothing_playing", names
        player = self._name(src.player)
        target = dest.sources.get(src.key)
        if target is None:
            return "handover_no_player", {**names, "player": player}
        if not src.backend.handover_to(target.backend):
            return "handover_not_supported", {**names, "player": player, "target": self._name(target.player)}
        if keep and not src.backend.keeps_source:
            return "handover_keep_not_supported", {**names, "player": player}
        hub = ctrl.hub
        groups = [
            g for g in src.backend.groups_of(src.player) if hub is None or not hub.follows(g)
        ] if src.key == SOURCE_AUDIO else []
        if groups:
            # the group plays it, not the player (follow me: it ends, see below)
            return "handover_in_group", {**names, "player": player, "group": ", ".join(self._name(g) for g in groups)}
        return None

    def _name(self, entity_id: str) -> str:
        state = self.ctrl.hass.states.get(entity_id)
        return str(state.attributes.get("friendly_name") or entity_id) if state else entity_id

    async def async_hand_over(
        self, dest: RoomController | None, source: str | None = None, keep: bool = False, dest_id: str | None = None
    ) -> None:
        """Continue what plays here in dest; this room goes off (unless keep, or
        its other source is in use)."""
        ctrl = self.ctrl
        if (blocker := self.blocker(dest, source, keep, dest_id)) is not None:
            key, placeholders = blocker
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders
            )
        assert dest is not None
        src = self.source(source)
        assert src is not None
        target = dest.sources[src.key]
        _LOGGER.info("%s: handing %s over to %s", ctrl.entry.title, src.key, dest.entry.title)
        hub = ctrl.hub
        if src.key == SOURCE_AUDIO and hub is not None and any(hub.follows(g) for g in src.backend.groups_of(src.player)):
            # follow me plays it: it ends, the music goes to dest (the others stop)
            await dest.async_prepare(src.key)
            await hub.async_follow_stop(ctrl, to=dest)
        else:
            await dest.async_prepare(src.key)
            if not await src.backend.handover(src.player, target.player, keep):
                raise HomeAssistantError(f"{ctrl.entry.title}: could not hand over to {dest.entry.title}")
        # the destination plays at its room volume (night mode, presets)
        await dest.volume.async_apply(target, left_group=True)
        others_in_use = any(ctrl.source_active(s) for s in ctrl.sources.values() if s is not src)
        if not keep and not others_in_use:
            await ctrl.async_turn_off()
