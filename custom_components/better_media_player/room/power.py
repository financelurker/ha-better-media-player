"""The room's power devices: roles, what they need, and switching them.

role     A group of power devices with the same job: display, audio, bass and
         the optional host plugs that power a player's own hardware.
demand   A role is demanded while a source that needs it is active.
run-down After the demand ends, a role stays on for its run-down time.

A role's entities are plugs (switched power), devices (turn themselves on/off:
a TV or AV receiver as media player, a remote) and the role's own on/off
actions (one more device with an assumed state). On: plugs first, then the
devices behind them; off: devices first, the plugs once the devices are off
(and, with power sensors, once they are in standby).
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import State, callback

from ..const import (
    CONF_AUDIO_POWER,
    CONF_BASS,
    CONF_DISPLAY,
    CONF_DISPLAY_POWER,
    CONF_SPEAKERS,
    CONF_START_TIMEOUT,
    DEVICE_DOMAINS,
    DOMAIN,
    ROLE_ACTIONS,
    ROLE_AUDIO,
    ROLE_AUDIO_HOST,
    ROLE_BASS,
    ROLE_DISPLAY,
    ROLE_VIDEO_HOST,
    SOURCE_AUDIO,
    SOURCE_VIDEO,
    STANDBY_HOLD_SECONDS,
    SWITCH_DOMAINS,
)
from .. import clock

if TYPE_CHECKING:
    from .controller import RoomController

_LOGGER = logging.getLogger(__name__)

# Order in which roles are switched on / off
ON_ORDER = (ROLE_VIDEO_HOST, ROLE_AUDIO_HOST, ROLE_DISPLAY, ROLE_AUDIO, ROLE_BASS)
OFF_ORDER = (ROLE_BASS, ROLE_AUDIO, ROLE_DISPLAY, ROLE_VIDEO_HOST, ROLE_AUDIO_HOST)

COMMAND_RETRY_SECONDS = 30
ACTION_ID = f"{DOMAIN}_action"  # virtual id of a role's own on/off actions
DEVICE_OFF_SECONDS = 60  # a role's plugs wait at most this long for its devices to go off
OWN_CHANGE_SECONDS = 30

POWERED_STATES = {
    MediaPlayerState.ON,
    MediaPlayerState.IDLE,
    MediaPlayerState.PLAYING,
    MediaPlayerState.PAUSED,
    MediaPlayerState.BUFFERING,
}
NOT_AVAILABLE = {STATE_UNAVAILABLE, STATE_UNKNOWN}
# playing or paused: in use (also what a group shares, and what can be handed over)
GROUP_ACTIVE_STATES = {MediaPlayerState.PLAYING, MediaPlayerState.PAUSED, MediaPlayerState.BUFFERING}


def _float(state: State | None) -> float | None:
    if state is None or state.state in NOT_AVAILABLE:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


@dataclass
class Role:
    """Power devices with the same job."""

    key: str
    entities: list[str]
    sources: tuple[str, ...]  # sources whose activity demands this role
    run_down_key: str  # key in controller.data (minutes)
    prefix: str | None = None  # of its settings on the device ("display": display_on_wait, ...)
    power: list[str] | None = None  # power sensors (wait for standby)
    actions: dict[str, tuple[str, str]] = field(default_factory=dict)  # own on/off actions per virtual id
    # runtime
    last_demand: float | None = None
    force_off: bool = False
    stop_since: float | None = None
    low_since: float | None = None
    devices_off_since: float | None = None  # its devices were told to go off


class RoomPower:
    """The room's roles: their state, their demand, switching them."""

    def __init__(self, ctrl: RoomController) -> None:
        self.ctrl = ctrl
        o = ctrl.opts
        self.roles: dict[str, Role] = {}
        both = tuple(s for s in (SOURCE_VIDEO, SOURCE_AUDIO) if s in ctrl.sources)
        for conf, role_key, sources, run_down, prefix, power in (
            (CONF_DISPLAY, ROLE_DISPLAY, (SOURCE_VIDEO,), "display_run_down", "display", CONF_DISPLAY_POWER),
            (CONF_SPEAKERS, ROLE_AUDIO, both, "audio_run_down", "audio", CONF_AUDIO_POWER),
            (CONF_BASS, ROLE_BASS, both, "audio_run_down", None, None),
        ):
            # plugs and devices; own on/off actions count as one more device (assumed state)
            entities = list(o.get(conf) or [])
            on_k, off_k = ROLE_ACTIONS[conf]
            actions = {}
            if o.get(on_k) and o.get(off_k):
                actions[f"{ACTION_ID}.{role_key}"] = (o[on_k], o[off_k])
                entities.append(f"{ACTION_ID}.{role_key}")
            if entities:
                self.roles[role_key] = Role(
                    key=role_key,
                    entities=entities,
                    sources=sources,
                    run_down_key=run_down,
                    prefix=prefix,
                    power=list(o.get(power) or []) if power else None,
                    actions=actions,
                )
        for role_key, src_key in ((ROLE_VIDEO_HOST, SOURCE_VIDEO), (ROLE_AUDIO_HOST, SOURCE_AUDIO)):
            src = ctrl.sources.get(src_key)
            if src is not None and src.host:
                self.roles[role_key] = Role(
                    key=role_key,
                    entities=[src.host],
                    sources=(src_key,),
                    run_down_key="host_run_down",
                    prefix="host",
                )
        # devices left off until the next start (turned off by hand while needed,
        # or did not become available)
        self.held: set[str] = set()
        self._last_command: dict[str, tuple[bool, float]] = {}
        # recent commands per entity, to recognise late device reports as our own
        self._recent_commands: dict[str, deque[tuple[bool, float]]] = {}

    # ---------------------------------------------------------------- reading

    @property
    def entities(self) -> list[str]:
        """All switched power devices of the room (unique)."""
        seen: list[str] = []
        for role in self.roles.values():
            for ent in role.entities:
                if ent not in seen:
                    seen.append(ent)
        return seen

    @property
    def has_power(self) -> bool:
        return any(self.is_on(e) for e in self.entities)

    @staticmethod
    def kind(entity_id: str) -> str:
        """plug (switched power), device (turns itself on/off) or action (own on/off actions)."""
        domain = entity_id.split(".", 1)[0]
        if domain == ACTION_ID:
            return "action"
        return "plug" if domain in SWITCH_DOMAINS else "device"

    @staticmethod
    def state_on(entity_id: str, state: State | None) -> bool:
        if state is None:
            return False
        if entity_id.startswith("media_player."):
            return state.state in POWERED_STATES
        return state.state == STATE_ON

    def is_on(self, entity_id: str) -> bool:
        if self.kind(entity_id) == "action":
            return bool(self.ctrl.data.get("assumed", {}).get(entity_id))  # what was last sent
        return self.state_on(entity_id, self.ctrl.hass.states.get(entity_id))

    def is_available(self, entity_id: str) -> bool:
        if self.kind(entity_id) == "action":
            return True
        state = self.ctrl.hass.states.get(entity_id)
        return state is not None and state.state not in NOT_AVAILABLE

    def role_on(self, role: Role) -> bool:
        return any(self.is_on(e) or self._pending_on(e) for e in role.entities)

    def _pending_on(self, entity_id: str) -> bool:
        """Turn-on was sent recently; slow devices (Zigbee & co.) report later."""
        last = self._last_command.get(entity_id)
        return last is not None and last[0] and clock.now() - last[1] < COMMAND_RETRY_SECONDS

    def plugs_of(self, role: Role) -> list[str]:
        """The role's plugs, and those of other roles that feed one of its devices
        (a TV on the display's plug that also serves as the speakers)."""
        devices = {e for e in role.entities if self.kind(e) == "device"}
        plugs: list[str] = []
        for r in self.roles.values():
            if r is role or devices & set(r.entities):
                plugs += [e for e in r.entities if self.kind(e) == "plug" and e not in plugs]
        return plugs

    def role_fully_on(self, role: Role) -> bool:
        """Everything of the role is on (a device behind a plug only once it is there
        and on; something unavailable otherwise does not hold it up)."""
        plugs = self.plugs_of(role)
        return all(self.is_on(e) or self._pending_on(e) or not self.is_available(e) for e in plugs) and all(
            self.is_on(e)
            or self._pending_on(e)
            or e in self.held
            or (not self.is_available(e) and not (plugs and self.kind(e) == "device"))
            for e in role.entities
            if self.kind(e) != "plug"
        )

    def devices_on(self, role: Role) -> bool:
        """A device of the role (not needed by another role) is still on: its plugs wait."""
        needed = self._needed_elsewhere(role)
        return any(
            self.kind(e) != "plug" and e not in needed and self.is_on(e) and self.is_available(e)
            for e in role.entities
        )

    def role_demand(self, role: Role) -> bool:
        ctrl = self.ctrl
        if not ctrl.night.role_enabled(role.key):
            return False  # night mode: no subwoofer
        if ctrl.wake.holds(role):
            return True  # snoozed: the room stays ready
        if ctrl.announce.holds(role):
            return True  # an announcement plays
        if ctrl.follow.prewarm and role.key in (ROLE_AUDIO, ROLE_BASS, ROLE_AUDIO_HOST) and not ctrl.quiet:
            return True
        return any(
            ctrl.source_active(ctrl.sources[s]) or ctrl._just_turned_on(ctrl.sources[s])
            for s in role.sources
            if s in ctrl.sources
        )

    def demand(self, role_key: str) -> bool:
        role = self.roles.get(role_key)
        return role is not None and self.role_demand(role)

    def run_down(self, role: Role) -> float:
        key = role.run_down_key
        if key == "display_run_down" and self.ctrl.night.active:
            key = "night_display_run_down"
        return float(self.ctrl.data.get(key, 0.0)) * 60.0

    def role_on_wait(self, role: Role) -> float:
        if role.prefix is None:
            return 0.0
        return float(self.ctrl.setting(f"{role.prefix}_on_wait") or 0)

    def role_standby(self, role: Role) -> bool:
        """Wait for standby before its plug goes off (only with power sensors)."""
        return self.can_standby(role) and bool(self.ctrl.setting(f"{role.prefix}_standby"))

    def can_standby(self, role: Role) -> bool:
        """Power sensors and a plug to cut after the standby (display, speakers)."""
        return (
            bool(role.power)
            and role.prefix in ("display", "audio")
            and any(self.kind(e) == "plug" for e in role.entities)
        )

    def fully_off(self) -> bool:
        """Room off, and every device has reported (not still unknown after a start)."""
        ctrl = self.ctrl
        if ctrl.room_on:
            return False
        # devices are often gone while off (behind their plug, deep standby)
        ids = [s.player for s in ctrl.sources.values()] + [e for e in self.entities if self.kind(e) == "plug"]
        return all(self.is_available(e) for e in ids)

    # --------------------------------------------------------- outside changes

    def is_own_change(self, entity_id: str, new: State) -> bool:
        ctx = new.context
        own = self.ctrl._own_contexts
        if ctx is not None and (ctx.id in own or ctx.parent_id in own):
            return True
        now = clock.now()
        for on, ts in self._recent_commands.get(entity_id, ()):
            if self.state_on(entity_id, new) == on and now - ts < OWN_CHANGE_SECONDS:
                return True
        return False

    @callback
    def outside_change(self, entity_id: str, on: bool) -> None:
        """A power device was switched by someone else.

        A device (TV, AVR) turned off while needed stays off until the next start,
        everything else goes on automatically; anything else (a plug, a device
        turned on) is the user's: manual mode until everything is off."""
        ctrl = self.ctrl
        demanded = ctrl.sequence.running or any(
            entity_id in r.entities and self.role_demand(r) for r in self.roles.values()
        )
        if self.kind(entity_id) == "device" and demanded:
            # needed anyway (e.g. woken by another device over HDMI-CEC): stays automatic
            if on:
                self.held.discard(entity_id)
            else:
                _LOGGER.info("%s: %s turned off from outside, left off until the next start", ctrl.entry.title, entity_id)
                self.held.add(entity_id)
                self._last_command.pop(entity_id, None)  # our turn-on is no longer on its way
            return
        if self.kind(entity_id) == "device" and not on:
            return
        _LOGGER.info(
            "%s: %s switched %s from outside, switching to manual mode",
            ctrl.entry.title,
            entity_id,
            "on" if on else "off",
        )
        ctrl.manual = True

    # -------------------------------------------------------------- automatic

    def missing(self) -> list[Role]:
        """Demanded roles that are not fully on."""
        return [r for r in self.roles.values() if self.role_demand(r) and not self.role_fully_on(r)]

    async def async_auto_off(self, now: float) -> None:
        """Switch roles off in OFF_ORDER.

        A role waits while an earlier role of the same source is still on and not
        needed (run-down, standby wait, or its off command is still on its way),
        so e.g. the player's hardware is never cut before the display.
        """
        holding: list[Role] = []
        for key in OFF_ORDER:
            role = self.roles.get(key)
            if role is None:
                continue
            if not self.role_on(role):
                if not self._off_recent(role, now):
                    # keep force_off a little longer: a slow device may still
                    # report a late "on" from an earlier command
                    role.force_off = False
                role.stop_since = None
                role.low_since = None
                role.devices_off_since = None
                continue
            if self.role_demand(role):
                role.devices_off_since = None
                continue
            blocked = any(set(h.sources) & set(role.sources) for h in holding)
            due = role.force_off or (
                self.ctrl.auto_power
                and role.last_demand is not None
                and now - role.last_demand >= self.run_down(role)
            )
            if not due or blocked:
                holding.append(role)
                continue
            # devices first; their plugs once they are off (or did not within
            # DEVICE_OFF_SECONDS: the plug is what cuts them then), and in standby
            await self._switch_devices_off(role)
            if role.devices_off_since is None:
                role.devices_off_since = now
            if self.devices_on(role) and now - role.devices_off_since < DEVICE_OFF_SECONDS:
                holding.append(role)
                continue
            if self.role_standby(role) and not self._standby_reached(role, now):
                holding.append(role)
                continue
            await self._switch_plugs_off(role)
            if self.role_on(role) and self._off_recent(role, now):
                holding.append(role)

    def _off_recent(self, role: Role, now: float) -> bool:
        """An off command for this role is still on its way."""
        for entity_id in role.entities:
            last = self._last_command.get(entity_id)
            if last is not None and not last[0] and now - last[1] < 2 * COMMAND_RETRY_SECONDS:
                return True
        return False

    def _standby_reached(self, role: Role, now: float) -> bool:
        """Wait until the device went to standby on its own (or max time)."""
        if role.stop_since is None:
            role.stop_since = now
            role.low_since = None
        values = [_float(self.ctrl.hass.states.get(e)) for e in role.power or []]
        known = [v for v in values if v is not None]
        if known and all(v <= self.ctrl.setting(f"{role.prefix}_standby_w") for v in known):
            if role.low_since is None:
                role.low_since = now
        else:
            role.low_since = None
        if role.low_since is not None and now - role.low_since >= STANDBY_HOLD_SECONDS:
            return True
        return now - role.stop_since >= self.ctrl.setting(f"{role.prefix}_standby_max") * 60

    # --------------------------------------------------------------- commands

    async def _switch(self, entity_id: str, on: bool, force: bool = False) -> None:
        if not self.is_available(entity_id):
            return
        if self.is_on(entity_id) == on and not (not on and self._pending_on(entity_id)):
            return
        now = clock.now()
        last = self._last_command.get(entity_id)
        if not force and last is not None and last[0] == on and now - last[1] < COMMAND_RETRY_SECONDS:
            return
        self._last_command[entity_id] = (on, now)
        self._recent_commands.setdefault(entity_id, deque(maxlen=4)).append((on, now))
        service = SERVICE_TURN_ON if on else SERVICE_TURN_OFF
        kind = self.kind(entity_id)
        if kind == "action":
            # no feedback: the command is trusted
            role = next(r for r in self.roles.values() if entity_id in r.actions)
            await self.ctrl._run_action(role.actions[entity_id][0 if on else 1], on)
            self.ctrl.data.setdefault("assumed", {})[entity_id] = on
            self.ctrl._save()
            return
        domain = entity_id.split(".", 1)[0]
        if domain not in (*SWITCH_DOMAINS, *DEVICE_DOMAINS):
            domain = "homeassistant"
        await self.ctrl._call(domain, service, {ATTR_ENTITY_ID: entity_id})

    def _needed_elsewhere(self, role: Role) -> set[str]:
        """What another (still demanded) role needs: never cut."""
        return {e for r in self.roles.values() if r is not role and self.role_demand(r) for e in r.entities}

    async def async_switch_role(self, role: Role, on: bool, force: bool = False) -> None:
        """On: plugs first, then the devices behind them. Off: devices first, the
        plugs only once the devices are off."""
        if on:
            for entity_id in self.plugs_of(role):
                await self._switch(entity_id, True, force)
            for entity_id in role.entities:
                if self.kind(entity_id) != "plug" and entity_id not in self.held:
                    await self._switch(entity_id, True, force)
            role.last_demand = clock.now()
            return
        await self._switch_devices_off(role, force)
        if not self.devices_on(role):
            await self._switch_plugs_off(role, force)
        # else: async_auto_off cuts the plugs once the devices are off
        # force_off / standby state are reset in async_auto_off once the role is really off

    async def async_role_up(self, role: Role) -> None:
        """Plugs on, then the devices behind them once they are there."""
        await self.async_switch_role(role, True, force=True)
        if not self.plugs_of(role):
            return
        waiting = [e for e in role.entities if self.kind(e) == "device" and e not in self.held and not self.is_available(e)]
        if not waiting:
            return
        timeout = float(self.ctrl.setting(CONF_START_TIMEOUT))
        if not await self.ctrl._wait_until(lambda: all(self.is_available(e) for e in waiting), timeout):
            for entity_id in waiting:
                if not self.is_available(entity_id):
                    _LOGGER.warning(
                        "%s: %s did not become available, left off until the next start", self.ctrl.entry.title, entity_id
                    )
                    self.held.add(entity_id)
        await self.async_switch_role(role, True, force=True)

    async def _switch_devices_off(self, role: Role, force: bool = False) -> None:
        needed = self._needed_elsewhere(role)
        for entity_id in role.entities:
            if self.kind(entity_id) != "plug" and entity_id not in needed:
                await self._switch(entity_id, False, force)

    async def _switch_plugs_off(self, role: Role, force: bool = False) -> None:
        needed = self._needed_elsewhere(role)
        if any(e in needed for e in role.entities if self.kind(e) == "device"):
            return  # its plugs feed a device another role still needs
        for entity_id in role.entities:
            if self.kind(entity_id) == "plug" and entity_id not in needed:
                await self._switch(entity_id, False, force)
