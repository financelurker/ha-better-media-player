"""Where the room's volume is heard: the playing player, or an own device.

By default the room's volume is set on its players (each source's player).
With a volume device (amplifier, AV receiver, soundbar; a media player or a
number / input_number) the room's volume is that device's volume: the
players are left at their own level and the room's features (room volume,
sleep timer fade, wake fade, night mode limit and fade back) act on the
device. Group playback (Music Assistant groups, follow me) still sets the
players' volumes: there the group decides.

Every output speaks 0..1; a number is mapped from its min..max.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.const import ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES

if TYPE_CHECKING:
    from .controller import RoomController, Source

TOLERANCE = 0.011  # players round to whole percent
_NOT_READY = {"off", "standby", "unavailable", "unknown"}
NUMBER_DOMAINS = ("number", "input_number")


def _float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class PlayerOutput:
    """The volume of one source's player."""

    is_device = False
    tolerance = TOLERANCE

    def __init__(self, ctrl: RoomController, src: Source) -> None:
        self.ctrl = ctrl
        self.src = src
        self.id = src.player

    def level(self) -> float | None:
        state = self.ctrl.hass.states.get(self.id)
        return _float(state.attributes.get("volume_level")) if state else None

    @property
    def can_set(self) -> bool:
        return self.ctrl.player_supports(self.src, MediaPlayerEntityFeature.VOLUME_SET)

    async def async_set(self, volume: float) -> None:
        # the night mode's limit and "ours" are handled by the controller
        await self.ctrl._player(self.src, "volume_set", {"volume_level": volume})


class DeviceOutput:
    """The room's own volume device."""

    is_device = True

    def __init__(self, ctrl: RoomController, entity_id: str) -> None:
        self.ctrl = ctrl
        self.id = entity_id
        self.domain = entity_id.split(".", 1)[0]

    def _state(self):
        return self.ctrl.hass.states.get(self.id)

    def _range(self, state=None) -> tuple[float, float, float] | None:
        state = state or self._state()
        if state is None:
            return None
        lo, hi = _float(state.attributes.get("min")), _float(state.attributes.get("max"))
        step = _float(state.attributes.get("step")) or 0.0
        if lo is None or hi is None or hi <= lo:
            return None
        return lo, hi, step

    @property
    def ready(self) -> bool:
        return self.level() is not None

    def level(self, state=None) -> float | None:
        """0..1, None while the device is off or unavailable (state: another
        reported state of the device instead of the current one)."""
        state = state or self._state()
        if state is None or state.state in _NOT_READY:
            return None
        if self.domain in NUMBER_DOMAINS:
            rng, value = self._range(state), _float(state.state)
            if rng is None or value is None:
                return None
            lo, hi, _ = rng
            return round(max(0.0, min(1.0, (value - lo) / (hi - lo))), 4)
        return _float(state.attributes.get("volume_level"))

    @property
    def tolerance(self) -> float:
        """A coarse number (e.g. 0..30) reports its own steps."""
        rng = self._range() if self.domain in NUMBER_DOMAINS else None
        if rng is None or not rng[2]:
            return TOLERANCE
        lo, hi, step = rng
        return max(TOLERANCE, step / (hi - lo) / 2 + 0.001)

    def _features(self) -> int:
        state = self._state()
        return int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) or 0) if state else 0

    @property
    def can_set(self) -> bool:
        if self.domain in NUMBER_DOMAINS:
            return self.ready
        return self.ready and bool(self._features() & MediaPlayerEntityFeature.VOLUME_SET)

    @property
    def can_mute(self) -> bool:
        return self.domain == "media_player" and bool(self._features() & MediaPlayerEntityFeature.VOLUME_MUTE)

    @property
    def muted(self) -> bool | None:
        state = self._state()
        return state.attributes.get("is_volume_muted") if state is not None and self.can_mute else None

    def _effective(self, volume: float) -> tuple[float, float | None]:
        """The level the device will report (and the number value to send)."""
        volume = max(0.0, min(1.0, volume))
        if self.domain not in NUMBER_DOMAINS:
            return volume, None
        rng = self._range()
        if rng is None:
            return volume, None
        lo, hi, step = rng
        value = lo + volume * (hi - lo)
        if step:
            value = lo + round((value - lo) / step) * step
        value = round(min(hi, max(lo, value)), 6)
        return round((value - lo) / (hi - lo), 4), value

    async def async_set(self, volume: float) -> None:
        """Set the device's volume (the night mode's limit holds)."""
        ctrl = self.ctrl
        level, value = self._effective(ctrl.night.limit(float(volume)))
        ctrl.volume.note_own(self.id, level)
        if self.domain in NUMBER_DOMAINS:
            if value is None:
                return
            await ctrl._call(self.domain, "set_value", {ATTR_ENTITY_ID: self.id, "value": value})
        else:
            await ctrl._call("media_player", "volume_set", {ATTR_ENTITY_ID: self.id, "volume_level": level})

    async def async_step(self, up: bool) -> None:
        """One step up or down (the device's own step, else 5 %)."""
        if self.domain == "media_player" and self._features() & MediaPlayerEntityFeature.VOLUME_STEP:
            await self.ctrl._call("media_player", "volume_up" if up else "volume_down", {ATTR_ENTITY_ID: self.id})
            return
        current = self.level()
        if current is None:
            return
        rng = self._range() if self.domain in NUMBER_DOMAINS else None
        delta = max(0.05, (rng[2] / (rng[1] - rng[0])) if rng and rng[2] else 0.0)
        target = current + delta if up else current - delta
        self.ctrl.volume.learn(max(0.0, min(1.0, target)))
        await self.async_set(target)

    async def async_mute(self, mute: bool) -> None:
        if self.can_mute:
            await self.ctrl._call("media_player", "volume_mute", {ATTR_ENTITY_ID: self.id, "is_volume_muted": mute})


class MemberOutput:
    """A group member's player volume, set by the central entry (follow me)."""

    is_device = False
    tolerance = TOLERANCE
    can_set = True

    def __init__(self, hass, player: str, set_volume) -> None:
        self.hass = hass
        self.id = player
        self._set = set_volume

    def level(self) -> float | None:
        state = self.hass.states.get(self.id)
        return _float(state.attributes.get("volume_level")) if state else None

    async def async_set(self, volume: float) -> None:
        await self._set(volume)
