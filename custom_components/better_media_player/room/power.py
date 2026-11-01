"""The room's power roles: which devices it has, and when they are needed.

role     A group of power devices with the same job: display, audio, bass and
         the optional host plugs that power a player's own hardware.
demand   A role is demanded while a source that needs it is active.
run-down After the demand ends, a role stays on for its run-down time.

The roles are nodes of the device layer (devices/), which switches them; the
room owns them and answers what only it knows: demand, run-down, standby, and
what a change from outside means for the room (manual mode, held devices).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.core import State, callback

from ..const import (
    CONF_AUDIO_PLAYER,
    CONF_AUDIO_POWER,
    CONF_BASS,
    CONF_CONNECTIONS,
    CONF_DISPLAY,
    CONF_DISPLAY_POWER,
    CONF_SPEAKERS,
    CONF_START_TIMEOUT,
    CONF_VIDEO_PLAYER,
    CONN_FROM,
    CONN_INPUT,
    CONN_MODE,
    CONN_SIGNALS,
    CONN_TO,
    INPUT_ROLES,
    ROLE_ACTIONS,
    ROLE_AUDIO,
    ROLE_AUDIO_HOST,
    ROLE_BASS,
    ROLE_DISPLAY,
    ROLE_VIDEO_HOST,
    SOURCE_AUDIO,
    SOURCE_VIDEO,
    role_conf,
)
from ..devices import DeviceGroup, Node, Standby, get_control
from ..devices.const import ACTION_ID
from ..devices.graph import Route, route
from ..devices.model import Control, Edge, Inputs, Mode, Signal

if TYPE_CHECKING:
    from .controller import RoomController

_LOGGER = logging.getLogger(__name__)

# Order in which roles are switched on / off
ON_ORDER = (ROLE_VIDEO_HOST, ROLE_AUDIO_HOST, ROLE_DISPLAY, ROLE_AUDIO, ROLE_BASS)
OFF_ORDER = (ROLE_BASS, ROLE_AUDIO, ROLE_DISPLAY, ROLE_VIDEO_HOST, ROLE_AUDIO_HOST)

# the ends of a connection (option keys) as nodes of the room
ROLE_OF = {CONF_DISPLAY: ROLE_DISPLAY, CONF_SPEAKERS: ROLE_AUDIO, CONF_BASS: ROLE_BASS}
SOURCE_OF = {CONF_VIDEO_PLAYER: SOURCE_VIDEO, CONF_AUDIO_PLAYER: SOURCE_AUDIO}
NEEDS = {SOURCE_VIDEO: (Signal.PICTURE, Signal.SOUND), SOURCE_AUDIO: (Signal.SOUND,)}
GIVES = {CONF_DISPLAY: frozenset({Signal.PICTURE}), CONF_SPEAKERS: frozenset({Signal.SOUND}), CONF_BASS: frozenset({Signal.SOUND})}


def edges_of(options: dict) -> list[Edge]:
    """The room's connections (ends as option keys)."""
    return [
        Edge(
            source=c[CONN_FROM],
            target=c[CONN_TO],
            signals=frozenset(Signal(x) for x in c.get(CONN_SIGNALS) or ()),
            input=c.get(CONN_INPUT) or None,
            mode=Mode(c.get(CONN_MODE) or Mode.BMP),
        )
        for c in options.get(CONF_CONNECTIONS) or []
    ]


def _inputs(options: dict, conf: str) -> Inputs | None:
    entity = options.get(role_conf(conf, "inputs"))
    actions = dict(options.get(role_conf(conf, "input_actions")) or {})
    if not entity and not actions:
        return None
    return Inputs(entity=entity, actions=actions, pause=float(options.get(role_conf(conf, "input_pause")) or 0))


def _control(options: dict, conf: str) -> Control:
    return Control(
        on=Mode(options.get(role_conf(conf, "power_on")) or Mode.BMP),
        off=Mode(options.get(role_conf(conf, "power_off")) or Mode.BMP),
        fallback=bool(options.get(role_conf(conf, "cec_fallback"))),
        timeout=float(options.get(role_conf(conf, "cec_timeout")) or Control().timeout),
    )


# playing or paused: in use (also what a group shares, and what can be handed over)
GROUP_ACTIVE_STATES = {MediaPlayerState.PLAYING, MediaPlayerState.PAUSED, MediaPlayerState.BUFFERING}


@dataclass
class Role(Node):
    """A node of the room: power devices with the same job."""

    run_down_key: str = ""  # key in controller.data (minutes)
    prefix: str | None = None  # of its settings on the device ("display": display_on_wait, ...)


class RoomPower:
    """The room's roles: what it has, when they are needed; owner of their nodes."""

    def __init__(self, ctrl: RoomController) -> None:
        self.ctrl = ctrl
        self.name = ctrl.entry.title
        o = ctrl.opts
        self.roles: dict[str, Role] = {}
        for conf, role_key, run_down, prefix, power in (
            (CONF_DISPLAY, ROLE_DISPLAY, "display_run_down", "display", CONF_DISPLAY_POWER),
            (CONF_SPEAKERS, ROLE_AUDIO, "audio_run_down", "audio", CONF_AUDIO_POWER),
            (CONF_BASS, ROLE_BASS, "audio_run_down", None, None),
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
                    sources=(),  # from the routes, below
                    run_down_key=run_down,
                    prefix=prefix,
                    power=list(o.get(power) or []) if power else None,
                    actions=actions,
                    gives=GIVES[conf],
                    inputs=_inputs(o, conf) if conf in INPUT_ROLES else None,
                    control=_control(o, conf) if conf in INPUT_ROLES else Control(),
                )
        # what each source needs: its route through the connections
        self.routes: dict[str, Route] = {}
        ends = {conf: self.roles[key] for conf, key in ROLE_OF.items() if key in self.roles}
        edges = edges_of(o)
        for conf, src_key in SOURCE_OF.items():
            if src_key in ctrl.sources:
                r = route(conf, NEEDS[src_key], ends, edges)
                self.routes[src_key] = Route(
                    frozenset(ROLE_OF[c] for c in r.nodes),
                    {ROLE_OF[c]: wish for c, wish in r.inputs.items()},
                )
        for role in self.roles.values():
            role.sources = tuple(s for s, r in self.routes.items() if role.key in r.nodes)
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
        self.devices: DeviceGroup = get_control(ctrl.hass).register(ctrl.entry.entry_id, self, list(self.roles.values()))

    def unregister(self) -> None:
        get_control(self.ctrl.hass).unregister(self.ctrl.entry.entry_id, self.devices)

    # ------------------------------------------------- owner of the nodes

    @property
    def automatic(self) -> bool:
        return self.ctrl.auto_power

    def demanded(self, node: Node) -> bool:
        return self.role_demand(self.roles[node.key])

    def enabled(self, node: Node) -> bool:
        return self.ctrl.night.role_enabled(node.key)

    def on_wait(self, node: Node) -> float:
        prefix = self.roles[node.key].prefix
        return float(self.ctrl.setting(f"{prefix}_on_wait") or 0) if prefix else 0.0

    def run_down(self, node: Node) -> float:
        key = self.roles[node.key].run_down_key
        if key == "display_run_down" and self.ctrl.night.active:
            key = "night_display_run_down"
        return float(self.ctrl.data.get(key, 0.0)) * 60.0

    def standby(self, node: Node) -> Standby | None:
        role = self.roles[node.key]
        if not self.role_standby(role):
            return None
        return Standby(
            watts=float(self.ctrl.setting(f"{role.prefix}_standby_w")),
            max_seconds=float(self.ctrl.setting(f"{role.prefix}_standby_max")) * 60,
        )

    def start_timeout(self) -> float:
        return float(self.ctrl.setting(CONF_START_TIMEOUT))

    async def call(self, domain: str, service: str, data: dict) -> bool:
        return await self.ctrl._call(domain, service, data)

    async def run_action(self, action: str, on: bool) -> None:
        await self.ctrl._run_action(action, on)

    def assumed(self, entity_id: str) -> bool:
        return bool(self.ctrl.data.get("assumed", {}).get(entity_id))

    def set_assumed(self, entity_id: str, on: bool) -> None:
        self.ctrl.data.setdefault("assumed", {})[entity_id] = on
        self.ctrl._save()

    async def wait_until(self, cond: Callable[[], bool], timeout: float) -> bool:
        return await self.ctrl._wait_until(cond, timeout)

    # ----------------------------------------------------------------- inputs

    def want_inputs(self, source: str | None) -> None:
        """The inputs of source's route are wanted (None: no source, no wishes)."""
        r = self.routes.get(source) if source else None
        self.devices.want_inputs(r.inputs if r else {})

    # ---------------------------------------------------------------- reading

    @property
    def held(self) -> set[str]:
        """Devices left off until the next start."""
        return self.devices.held

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

    def role_standby(self, role: Role) -> bool:
        """Wait for standby before its plug goes off (only with power sensors)."""
        return self.can_standby(role) and bool(self.ctrl.setting(f"{role.prefix}_standby"))

    def can_standby(self, role: Role) -> bool:
        """Power sensors and a plug to cut after the standby (display, speakers)."""
        return (
            bool(role.power)
            and role.prefix in ("display", "audio")
            and any(self.devices.kind(e) == "plug" for e in role.entities)
        )

    def fully_off(self) -> bool:
        """Room off, and every device has reported (not still unknown after a start)."""
        ctrl = self.ctrl
        if ctrl.room_on:
            return False
        # devices are often gone while off (behind their plug, deep standby)
        devices = self.devices
        ids = [s.player for s in ctrl.sources.values()] + [e for e in devices.entities if devices.kind(e) == "plug"]
        return all(devices.is_available(e) for e in ids)

    # --------------------------------------------------------- outside changes

    def is_own_change(self, entity_id: str, new: State) -> bool:
        ctx = new.context
        own = self.ctrl._own_contexts
        if ctx is not None and (ctx.id in own or ctx.parent_id in own):
            return True
        return self.devices.recent_command(entity_id, new)

    @callback
    def outside_change(self, entity_id: str, on: bool) -> None:
        """A power device was switched by someone else.

        A device (TV, AVR) turned off while needed stays off until the next start,
        everything else goes on automatically; anything else (a plug, a device
        turned on) is the user's: manual mode until everything is off."""
        ctrl = self.ctrl
        kind = self.devices.kind(entity_id)
        demanded = ctrl.sequence.running or any(
            entity_id in r.entities and self.role_demand(r) for r in self.roles.values()
        )
        if kind == "device" and demanded:
            # needed anyway (e.g. woken by another device over HDMI-CEC): stays automatic
            if on:
                self.held.discard(entity_id)
            else:
                _LOGGER.info("%s: %s turned off from outside, left off until the next start", ctrl.entry.title, entity_id)
                self.held.add(entity_id)
                self.devices.forget_command(entity_id)
            return
        if kind == "device" and not on:
            return
        _LOGGER.info(
            "%s: %s switched %s from outside, switching to manual mode",
            ctrl.entry.title,
            entity_id,
            "on" if on else "off",
        )
        ctrl.manual = True
