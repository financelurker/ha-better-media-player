"""A group of nodes of one owner: their state and switching them.

On: plugs first, then the devices behind them. Off: devices first, the plugs
once the devices are off (and, with power sensors, once they are in standby).
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from .api import NodeOwner
from .const import (
    ACTION_ID,
    COMMAND_RETRY_SECONDS,
    DEVICE_DOMAINS,
    DEVICE_OFF_SECONDS,
    NOT_AVAILABLE,
    OWN_CHANGE_SECONDS,
    POWERED_STATES,
    STANDBY_HOLD_SECONDS,
    SWITCH_DOMAINS,
)
from .model import Control, Mode, Node

_LOGGER = logging.getLogger(__name__)


def _now() -> float:
    return dt_util.utcnow().timestamp()


INPUT_VERIFY_SECONDS = 5  # after choosing an input: when it is checked (and chosen once more)


@dataclass
class _Input:
    """The input wanted on a node, and how far choosing it got."""

    target: str
    mode: Mode
    sent_at: float | None = None  # chosen (or, CEC: expected since)
    tries: int = 0
    done: bool = False  # it was there
    manual: bool = False  # changed by hand afterwards: left alone until the next wish
    failed: bool = False


def _float(state: State | None) -> float | None:
    if state is None or state.state in NOT_AVAILABLE:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


class DeviceGroup:
    """The nodes of one owner."""

    def __init__(self, hass: HomeAssistant, owner: NodeOwner, nodes: Iterable[Node]) -> None:
        self.hass = hass
        self.owner = owner
        self.nodes: dict[str, Node] = {n.key: n for n in nodes}
        # devices left off until the next start (turned off by hand while needed,
        # or did not become available)
        self.held: set[str] = set()
        self._last_command: dict[str, tuple[bool, float]] = {}
        # recent commands per entity, to recognise late device reports as our own
        self._recent_commands: dict[str, deque[tuple[bool, float]]] = {}
        # CEC: power states expected from someone else, since when
        self._expected: dict[str, tuple[bool, float]] = {}
        self._inputs: dict[str, _Input] = {}
        self._wish_key: str | None = None  # whose inputs are wanted (a source)
        self._starting: set[str] = set()  # nodes being brought up (no input yet)
        self._applying = False

    # ---------------------------------------------------------------- reading

    @property
    def entities(self) -> list[str]:
        """All switched power devices of the group (unique)."""
        seen: list[str] = []
        for node in self.nodes.values():
            for ent in node.entities:
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
            return self.owner.assumed(entity_id)  # what was last sent
        return self.state_on(entity_id, self.hass.states.get(entity_id))

    def is_available(self, entity_id: str) -> bool:
        if self.kind(entity_id) == "action":
            return True
        state = self.hass.states.get(entity_id)
        return state is not None and state.state not in NOT_AVAILABLE

    def node_on(self, node: Node) -> bool:
        return any(self.is_on(e) or self._pending_on(e) for e in node.entities)

    def _pending_on(self, entity_id: str) -> bool:
        """Turn-on was sent recently (slow devices, Zigbee & co., report later), or
        is expected from someone else (CEC)."""
        now = _now()
        last = self._last_command.get(entity_id)
        if last is not None and last[0] and now - last[1] < COMMAND_RETRY_SECONDS:
            return True
        expected = self._expected.get(entity_id)
        return expected is not None and expected[0] and now - expected[1] < self.control_of(entity_id).timeout

    def control_of(self, entity_id: str) -> Control:
        """How the entity is switched (only devices have a mode; plugs and own
        actions are always switched by BMP)."""
        if self.kind(entity_id) == "device":
            for node in self.nodes.values():
                if entity_id in node.entities:
                    return node.control
        return Control()

    def settle(self) -> None:
        """Forget what CEC was expected to do once it is done (call on changes)."""
        for entity_id, (on, _since) in list(self._expected.items()):
            if self.is_on(entity_id) == on:
                del self._expected[entity_id]

    def restart_expectations(self) -> None:
        """What CEC is expected to do is counted from now: the player that does it
        has just started (its CEC comes once it is up)."""
        now = _now()
        for entity_id, (on, _since) in list(self._expected.items()):
            self._expected[entity_id] = (on, now)
        for wish in self._inputs.values():
            if wish.mode is Mode.CEC and wish.tries == 0 and wish.sent_at is not None:
                wish.sent_at = now

    def plugs_of(self, node: Node) -> list[str]:
        """The node's plugs, and those of other nodes that feed one of its devices
        (a TV on the display's plug that also serves as the speakers)."""
        devices = {e for e in node.entities if self.kind(e) == "device"}
        plugs: list[str] = []
        for n in self.nodes.values():
            if n is node or devices & set(n.entities):
                plugs += [e for e in n.entities if self.kind(e) == "plug" and e not in plugs]
        return plugs

    def node_fully_on(self, node: Node) -> bool:
        """Everything of the node is on (a device behind a plug only once it is there
        and on; something unavailable otherwise does not hold it up)."""
        plugs = self.plugs_of(node)
        return all(self.is_on(e) or self._pending_on(e) or not self.is_available(e) for e in plugs) and all(
            self.is_on(e)
            or self._pending_on(e)
            or e in self.held
            or (not self.is_available(e) and not (plugs and self.kind(e) == "device"))
            for e in node.entities
            if self.kind(e) != "plug" and self.control_of(e).on is not Mode.NEVER  # not BMP's to switch on
        )

    def devices_on(self, node: Node) -> bool:
        """A device of the node (not needed by another node) is still on: its plugs wait."""
        needed = self._needed_elsewhere(node)
        return any(
            self.kind(e) != "plug" and e not in needed and self.is_on(e) and self.is_available(e)
            for e in node.entities
        )

    def missing(self) -> list[Node]:
        """Demanded nodes that are not fully on."""
        return [n for n in self.nodes.values() if self.owner.demanded(n) and not self.node_fully_on(n)]

    def recent_command(self, entity_id: str, new: State) -> bool:
        """The new state is what we recently sent (a slow device reporting late)."""
        now = _now()
        return any(
            self.state_on(entity_id, new) == on and now - ts < OWN_CHANGE_SECONDS
            for on, ts in self._recent_commands.get(entity_id, ())
        )

    def forget_command(self, entity_id: str) -> None:
        """Our turn-on is no longer on its way (the device was turned off from outside)."""
        self._last_command.pop(entity_id, None)

    # -------------------------------------------------------------- automatic

    async def async_auto_off(self, now: float, order: Iterable[str]) -> None:
        """Switch nodes off in the given order.

        A node waits while an earlier node of the same source is still on and not
        needed (run-down, standby wait, or its off command is still on its way),
        so e.g. the player's hardware is never cut before the display.
        """
        holding: list[Node] = []
        for key in order:
            node = self.nodes.get(key)
            if node is None:
                continue
            if not self.node_on(node):
                if not self._off_recent(node, now):
                    # keep force_off a little longer: a slow device may still
                    # report a late "on" from an earlier command
                    node.force_off = False
                node.stop_since = None
                node.low_since = None
                node.devices_off_since = None
                continue
            if self.owner.demanded(node):
                node.devices_off_since = None
                continue
            blocked = any(set(h.sources) & set(node.sources) for h in holding)
            due = node.force_off or (
                self.owner.automatic
                and node.last_demand is not None
                and now - node.last_demand >= self.owner.run_down(node)
            )
            if not due or blocked:
                holding.append(node)
                continue
            # devices first; their plugs once they are off (or did not within
            # DEVICE_OFF_SECONDS: the plug is what cuts them then), and in standby
            await self._switch_devices_off(node)
            if node.devices_off_since is None:
                node.devices_off_since = now
            if self.devices_on(node) and now - node.devices_off_since < DEVICE_OFF_SECONDS:
                holding.append(node)
                continue
            if not self._standby_reached(node, now):
                holding.append(node)
                continue
            await self._switch_plugs_off(node)
            if self.node_on(node) and self._off_recent(node, now):
                holding.append(node)

    def _off_recent(self, node: Node, now: float) -> bool:
        """An off command for this node is still on its way."""
        for entity_id in node.entities:
            last = self._last_command.get(entity_id)
            if last is not None and not last[0] and now - last[1] < 2 * COMMAND_RETRY_SECONDS:
                return True
        return False

    def _standby_reached(self, node: Node, now: float) -> bool:
        """Wait until the device went to standby on its own (or max time); no
        standby wait for the node: reached at once."""
        standby = self.owner.standby(node)
        if standby is None:
            return True
        if node.stop_since is None:
            node.stop_since = now
            node.low_since = None
        values = [_float(self.hass.states.get(e)) for e in node.power or []]
        known = [v for v in values if v is not None]
        if known and all(v <= standby.watts for v in known):
            if node.low_since is None:
                node.low_since = now
        else:
            node.low_since = None
        if node.low_since is not None and now - node.low_since >= STANDBY_HOLD_SECONDS:
            return True
        return now - node.stop_since >= standby.max_seconds

    # --------------------------------------------------------------- commands

    async def _switch(self, entity_id: str, on: bool, force: bool = False) -> None:
        if not self.is_available(entity_id):
            return
        if self.is_on(entity_id) == on and not (not on and self._pending_on(entity_id)):
            self._expected.pop(entity_id, None)
            return
        now = _now()
        control = self.control_of(entity_id)
        mode = control.on if on else control.off
        if mode is Mode.NEVER:
            return
        if mode is Mode.CEC:
            expected = self._expected.get(entity_id)
            if expected is None or expected[0] != on:
                self._expected[entity_id] = (on, now)  # someone else does it
                return
            if now - expected[1] < control.timeout:
                return
            del self._expected[entity_id]
            if not control.fallback:
                if on:
                    _LOGGER.warning(
                        "%s: %s was not switched on over CEC, left off until the next start", self.owner.name, entity_id
                    )
                    self.held.add(entity_id)
                return
            _LOGGER.info("%s: %s not switched over CEC, switching it %s", self.owner.name, entity_id, "on" if on else "off")
            force = True
        last = self._last_command.get(entity_id)
        if not force and last is not None and last[0] == on and now - last[1] < COMMAND_RETRY_SECONDS:
            return
        self._last_command[entity_id] = (on, now)
        self._recent_commands.setdefault(entity_id, deque(maxlen=4)).append((on, now))
        service = SERVICE_TURN_ON if on else SERVICE_TURN_OFF
        kind = self.kind(entity_id)
        if kind == "action":
            # no feedback: the command is trusted
            node = next(n for n in self.nodes.values() if entity_id in n.actions)
            await self.owner.run_action(node.actions[entity_id][0 if on else 1], on)
            self.owner.set_assumed(entity_id, on)
            return
        domain = entity_id.split(".", 1)[0]
        if domain not in (*SWITCH_DOMAINS, *DEVICE_DOMAINS):
            domain = "homeassistant"
        await self.owner.call(domain, service, {ATTR_ENTITY_ID: entity_id})

    def _needed_elsewhere(self, node: Node) -> set[str]:
        """What another (still demanded) node needs: never cut."""
        return {e for n in self.nodes.values() if n is not node and self.owner.demanded(n) for e in n.entities}

    async def async_switch_node(self, node: Node, on: bool, force: bool = False) -> None:
        """On: plugs first, then the devices behind them. Off: devices first, the
        plugs only once the devices are off."""
        if on:
            for entity_id in self.plugs_of(node):
                await self._switch(entity_id, True, force)
            for entity_id in node.entities:
                if self.kind(entity_id) != "plug" and entity_id not in self.held:
                    await self._switch(entity_id, True, force)
            node.last_demand = _now()
            return
        await self._switch_devices_off(node, force)
        if not self.devices_on(node):
            await self._switch_plugs_off(node, force)
        # else: async_auto_off cuts the plugs once the devices are off
        # force_off / standby state are reset in async_auto_off once the node is really off

    async def async_node_up(self, node: Node) -> None:
        """Plugs on, then the devices behind them once they are there."""
        await self.async_switch_node(node, True, force=True)
        if not self.plugs_of(node):
            return
        waiting = [
            e
            for e in node.entities
            if self.kind(e) == "device"
            and e not in self.held
            and not self.is_available(e)
            and self.control_of(e).on is not Mode.NEVER
        ]
        if not waiting:
            return
        if not await self.owner.wait_until(lambda: all(self.is_available(e) for e in waiting), self.owner.start_timeout()):
            for entity_id in waiting:
                if not self.is_available(entity_id):
                    _LOGGER.warning(
                        "%s: %s did not become available, left off until the next start", self.owner.name, entity_id
                    )
                    self.held.add(entity_id)
        await self.async_switch_node(node, True, force=True)

    async def async_bring_up(self, nodes: Iterable[Node], switched: list[Node]) -> None:
        """Switch the nodes on in turn, each with its wait after switching on;
        what is on already only renews its demand, what the owner does not enable
        (any more) is left out. switched collects what was switched (to take it
        down again if the start fails)."""
        for node in nodes:
            if not self.owner.enabled(node):
                continue
            if self.node_fully_on(node):
                node.last_demand = _now()
                continue
            switched.append(node)
            self._starting.add(node.key)
            try:
                await self.async_node_up(node)
                if (wait := self.owner.on_wait(node)) > 0:
                    await asyncio.sleep(wait)
            finally:
                self._starting.discard(node.key)
            await self._apply_input(node)

    async def async_take_down(self, nodes: Iterable[Node]) -> None:
        """Switch the nodes off again (in reverse), retried until really off."""
        for node in reversed(list(nodes)):
            node.force_off = True  # retried by async_auto_off until really off
            await self.async_switch_node(node, False, force=True)

    async def _switch_devices_off(self, node: Node, force: bool = False) -> None:
        needed = self._needed_elsewhere(node)
        for entity_id in node.entities:
            if self.kind(entity_id) != "plug" and entity_id not in needed:
                await self._switch(entity_id, False, force)

    async def _switch_plugs_off(self, node: Node, force: bool = False) -> None:
        needed = self._needed_elsewhere(node)
        if any(e in needed for e in node.entities if self.kind(e) == "device"):
            return  # its plugs feed a device another node still needs
        for entity_id in node.entities:
            if self.kind(entity_id) == "plug" and entity_id not in needed:
                await self._switch(entity_id, False, force)

    # ----------------------------------------------------------------- inputs

    def want_inputs(self, key: str | None, wanted: Mapping[str, tuple[str, Mode]]) -> None:
        """The input each node should be on for key (a source; node -> input, who
        chooses it); a node not listed has no wish. A new key (another source)
        starts afresh: a change by hand or a failure is forgotten."""
        if key != self._wish_key:
            self._wish_key = key
            self._inputs.clear()
        for node in list(self._inputs):
            if node not in wanted:
                del self._inputs[node]
        for node, (target, mode) in wanted.items():
            current = self._inputs.get(node)
            if current is None or (current.target, current.mode) != (target, mode):
                self._inputs[node] = _Input(target, mode)

    def input_of(self, node: Node) -> str | None:
        """The input the node reports (None: it does not tell)."""
        entity = node.inputs.entity if node.inputs else None
        state = self.hass.states.get(entity) if entity else None
        return state.attributes.get("source") if state is not None else None

    def reports_input(self, node: Node) -> bool:
        """The node tells its input (a media player with a list of sources)."""
        entity = node.inputs.entity if node.inputs else None
        state = self.hass.states.get(entity) if entity else None
        return state is not None and ("source_list" in state.attributes or "source" in state.attributes)

    def input_manual(self, node: Node) -> bool:
        """The node's input was changed by hand (left alone until the next wish)."""
        wish = self._inputs.get(node.key)
        return wish is not None and wish.manual

    def input_ready(self, node: Node) -> bool:
        """The node can take an input: its devices are really on (not only told to
        be, nor expected over CEC), and it is not being brought up (its wait after
        switching on: a TV ignores inputs while starting)."""
        if node.key in self._starting:
            return False
        entity = node.inputs.entity if node.inputs else None
        if entity:
            return self.is_on(entity) and self.is_available(entity)
        # its devices (or, a device only on a plug, the plugs)
        check = [e for e in node.entities if self.kind(e) != "plug"] or node.entities
        return bool(check) and all(self.is_on(e) for e in check)

    async def async_apply_inputs(self) -> None:
        """Choose the wanted inputs on the nodes that can take them (one run at a time)."""
        if self._applying:
            return
        self._applying = True
        try:
            for key in list(self._inputs):
                if (node := self.nodes.get(key)) is not None:
                    await self._apply_input(node)
        finally:
            self._applying = False

    async def _apply_input(self, node: Node) -> None:
        wish = self._inputs.get(node.key)
        inputs = node.inputs
        if wish is None or inputs is None or wish.mode is Mode.NEVER:
            return
        if not self.input_ready(node):
            entity = inputs.entity
            if not (self.is_on(entity) if entity else self.node_on(node)):
                # off: the next time it is on, its input is chosen afresh (it may
                # come back on its default input; that is no change by hand)
                wish.done = wish.manual = wish.failed = False
                wish.tries, wish.sent_at = 0, None
            return
        if wish.manual or wish.failed:
            return
        current = self.input_of(node)
        if current == wish.target:
            wish.done = True
            wish.sent_at = None
            return
        if wish.done:
            if current is not None:
                _LOGGER.info("%s: input of %s changed by hand to %s, left so", self.owner.name, node.key, current)
                wish.manual = True
            return
        now = _now()
        if wish.mode is Mode.CEC and wish.tries == 0:
            if wish.sent_at is None:
                wish.sent_at = now  # someone else chooses it
                return
            if now - wish.sent_at < node.control.timeout:
                return
            if not self.reports_input(node):
                wish.done = True  # it does not tell: nothing to check
                return
            if not node.control.fallback:
                _LOGGER.warning("%s: %s did not get input %s over CEC", self.owner.name, node.key, wish.target)
                wish.failed = True
                return
        elif wish.sent_at is not None and now - wish.sent_at < INPUT_VERIFY_SECONDS:
            return
        if wish.tries and not self.reports_input(node):
            wish.done = True  # it does not tell: what was chosen counts
            return
        if wish.tries >= 2:
            _LOGGER.warning("%s: %s did not take input %s (is %s)", self.owner.name, node.key, wish.target, current)
            wish.failed = True
            return
        wish.tries += 1
        wish.sent_at = now
        device = inputs.entity
        if (action := inputs.actions.get(wish.target)) is not None:
            await self.owner.run_action(action, True)
        elif device:
            await self.owner.call("media_player", "select_source", {ATTR_ENTITY_ID: device, "source": wish.target})
        if inputs.pause > 0:
            await asyncio.sleep(inputs.pause)
