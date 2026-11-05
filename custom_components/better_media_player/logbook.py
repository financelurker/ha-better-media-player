"""Logbook: what a room's events mean (shown in its activity)."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_WAKE_FAILED
from .room.wake import FAILED_NOT_PLAYING, FAILED_START

# why a wake failed (event better_media_player_wake_failed, "reason")
WAKE_FAILED_REASONS = {FAILED_START: "the start failed", FAILED_NOT_PLAYING: "nothing played"}


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, str]]], None],
) -> None:
    @callback
    def _wake_failed(event: Event) -> dict[str, str]:
        data = event.data
        reason = WAKE_FAILED_REASONS.get(data.get("reason"), data.get("reason") or "unknown")
        message = f"wake failed: {reason}"
        if data.get("error"):
            message += f" ({data['error']})"
        out = {"name": data.get("room") or "Better Media Player", "message": message}
        if data.get(ATTR_ENTITY_ID):
            out[ATTR_ENTITY_ID] = data[ATTR_ENTITY_ID]
        return out

    async_describe_event(DOMAIN, EVENT_WAKE_FAILED, _wake_failed)
