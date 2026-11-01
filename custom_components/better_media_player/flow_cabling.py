"""Options steps for the room's cabling: the connections between its players and
devices, and how display and speakers choose inputs and are switched."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import ATTR_FRIENDLY_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector as sel

from .const import (
    ACTION_DOMAINS,
    CONF_CONNECTIONS,
    CONN_ENDS,
    CONN_FROM,
    CONN_INPUT,
    CONN_MODE,
    CONN_SIGNALS,
    CONN_SOURCES,
    CONN_TARGETS,
    CONN_TO,
    INPUT_ROLES,
    MODES,
    SIGNALS,
    role_conf,
)
from .devices.graph import cycles
from .devices.model import Edge, Mode, Signal

CONTROL_DEFAULTS = {"input_pause": 0, "power_on": Mode.BMP.value, "power_off": Mode.BMP.value, "cec_fallback": False, "cec_timeout": 15}
CONTROL_KEYS = ("inputs", "input_actions", "input_pause", "power_on", "power_off", "cec_fallback", "cec_timeout")


def _select(options: list[str], key: str | None, multiple: bool = False, custom: bool = False) -> sel.SelectSelector:
    config: dict[str, Any] = {"options": options, "multiple": multiple, "custom_value": custom, "mode": sel.SelectSelectorMode.DROPDOWN}
    if key:
        config["translation_key"] = key
    return sel.SelectSelector(sel.SelectSelectorConfig(**config))


def ends_present(options: dict) -> list[str]:
    """The ends a connection can have in this room (its players and roles)."""
    return [e for e in CONN_ENDS if options.get(e)]


def input_choices(hass: HomeAssistant, options: dict, target: str) -> list[str]:
    """The inputs the target offers (its source list, or its input actions)."""
    actions = list((options.get(role_conf(target, "input_actions")) or {}).keys())
    entity = options.get(role_conf(target, "inputs"))
    state = hass.states.get(entity) if entity else None
    listed = list(state.attributes.get("source_list") or []) if state is not None else []
    return listed + [a for a in actions if a not in listed]


def end_name(hass: HomeAssistant, options: dict, end: str) -> str:
    """A connection's end by the name of its (first) entity."""
    value = options.get(end)
    entity = value[0] if isinstance(value, list) and value else value
    state = hass.states.get(entity) if isinstance(entity, str) else None
    return str(state.attributes.get(ATTR_FRIENDLY_NAME) or entity) if state is not None else str(entity or end)


def connection_label(hass: HomeAssistant, options: dict, conn: dict) -> str:
    label = f"{end_name(hass, options, conn[CONN_FROM])} → {end_name(hass, options, conn[CONN_TO])}"
    label += f" ({', '.join(conn.get(CONN_SIGNALS) or [])})"
    if conn.get(CONN_INPUT):
        label += f": {conn[CONN_INPUT]}"
    if (conn.get(CONN_MODE) or Mode.BMP) != Mode.BMP:
        label += f" [{conn[CONN_MODE]}]"
    return label


def has_input_control(options: dict, target: str) -> bool:
    return target in INPUT_ROLES and bool(
        options.get(role_conf(target, "inputs")) or options.get(role_conf(target, "input_actions"))
    )


def valid_input_actions(value: Any) -> bool:
    """{input: action entity} (a script, button, scene …)."""
    return isinstance(value, dict) and all(
        isinstance(k, str) and isinstance(v, str) and v.split(".", 1)[0] in ACTION_DOMAINS for k, v in value.items()
    )


def prune(options: dict) -> dict:
    """Without the connections (and controls) of players and roles no longer there."""
    present = set(ends_present(options))
    out = dict(options)
    if CONF_CONNECTIONS in out:
        out[CONF_CONNECTIONS] = [c for c in out[CONF_CONNECTIONS] if c[CONN_FROM] in present and c[CONN_TO] in present]
    for role in INPUT_ROLES:
        if role not in present:
            for key in CONTROL_KEYS:
                out.pop(role_conf(role, key), None)
    return out


def _edges(connections: list[dict]) -> list[Edge]:
    return [
        Edge(c[CONN_FROM], c[CONN_TO], frozenset(Signal(x) for x in c.get(CONN_SIGNALS) or ()), c.get(CONN_INPUT))
        for c in connections
    ]


NEEDS = {CONN_SOURCES[0]: (Signal.PICTURE, Signal.SOUND), CONN_SOURCES[1]: (Signal.SOUND,)}


def validate_connections(hass: HomeAssistant, options: dict) -> dict[str, str]:
    """Errors of the cabling as a whole (key: 'base')."""
    connections = options.get(CONF_CONNECTIONS) or []
    present = set(ends_present(options))
    for c in connections:
        if c[CONN_FROM] == c[CONN_TO]:
            return {"base": "connection_to_itself"}
        if c[CONN_FROM] not in present or c[CONN_TO] not in present or c[CONN_TO] not in CONN_TARGETS:
            return {"base": "connection_end_missing"}
        if not c.get(CONN_SIGNALS) or (c.get(CONN_MODE) or Mode.BMP) not in MODES:
            return {"base": "connection_no_signal"}
    edges = _edges(connections)
    if cycles(edges):
        return {"base": "connection_cycle"}
    for source, needs in NEEDS.items():
        wanted: dict[str, set[str]] = {}
        for signal in needs:
            seen, todo = {source}, [source]
            while todo:
                at = todo.pop()
                for e in edges:
                    if e.source == at and signal in e.signals:
                        if e.input:
                            wanted.setdefault(e.target, set()).add(e.input)
                        if e.target not in seen:
                            seen.add(e.target)
                            todo.append(e.target)
        if any(len(v) > 1 for v in wanted.values()):
            return {"base": "input_conflict"}
    for role in INPUT_ROLES:
        actions = options.get(role_conf(role, "input_actions"))
        if actions is not None and not valid_input_actions(actions):
            return {"base": "invalid_input_actions"}
    return {}


class CablingSteps:
    """Mixin for the room's options flow (uses self.options, self.hass)."""

    options: dict[str, Any]
    hass: HomeAssistant
    _draft: dict[str, Any] | None = None
    _editing: int | None = None

    @property
    def _connections(self) -> list[dict]:
        return list(self.options.get(CONF_CONNECTIONS) or [])

    def _others(self) -> list[dict]:
        """The connections besides the one being edited."""
        return [c for i, c in enumerate(self._connections) if i != self._editing]

    async def async_step_cabling(self, user_input=None) -> ConfigFlowResult:
        menu = ["connection_add"]
        if self._connections:
            menu += ["connection_edit", "connection_remove"]
        menu += [f"control_{r}" for r in INPUT_ROLES if self.options.get(r)]
        menu.append("init")
        return self.async_show_menu(step_id="cabling", menu_options=menu)

    # ------------------------------------------------------- connections

    async def async_step_connection_add(self, user_input=None) -> ConfigFlowResult:
        self._editing = None
        return await self.async_step_connection(user_input)

    def _choices(self) -> list[sel.SelectOptionDict]:
        """The connections to pick from (by their place in the list)."""
        return [
            sel.SelectOptionDict(value=str(i), label=connection_label(self.hass, self.options, c))
            for i, c in enumerate(self._connections)
        ]

    async def async_step_connection_edit(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self._editing = int(user_input["connection"])
            self._draft = None
            return await self.async_step_connection()
        schema = vol.Schema({vol.Required("connection"): sel.SelectSelector(sel.SelectSelectorConfig(options=self._choices()))})
        return self.async_show_form(step_id="connection_edit", data_schema=schema)

    async def async_step_connection_remove(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            gone = {int(i) for i in user_input.get("connections") or []}
            self.options[CONF_CONNECTIONS] = [c for i, c in enumerate(self._connections) if i not in gone]
            return await self.async_step_cabling()
        schema = vol.Schema(
            {vol.Optional("connections"): sel.SelectSelector(sel.SelectSelectorConfig(options=self._choices(), multiple=True))}
        )
        return self.async_show_form(step_id="connection_remove", data_schema=schema)

    async def async_step_connection(self, user_input=None) -> ConfigFlowResult:
        """What feeds what, with which signals."""
        ends = ends_present(self.options)
        sources = [e for e in ends if e in (*CONN_SOURCES, *CONN_TARGETS)]
        targets = [e for e in ends if e in CONN_TARGETS]
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONN_FROM] == user_input[CONN_TO]:
                errors["base"] = "connection_to_itself"
            elif not user_input.get(CONN_SIGNALS):
                errors["base"] = "connection_no_signal"
            elif cycles(_edges(self._others() + [{**user_input, CONN_INPUT: None}])):
                errors["base"] = "connection_cycle"
            else:
                self._draft = {**(self._draft or {}), **user_input}
                return await self.async_step_connection_input()
        current = self._draft or (self._connections[self._editing] if self._editing is not None else {})
        schema = vol.Schema(
            {
                vol.Required(CONN_FROM): _select(sources, "connection_end"),
                vol.Required(CONN_TO): _select(targets, "connection_end"),
                vol.Required(CONN_SIGNALS, default=[Signal.SOUND.value]): _select(SIGNALS, "signal", multiple=True),
            }
        )
        return self.async_show_form(
            step_id="connection",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or current),
            errors=errors,
        )

    async def async_step_connection_input(self, user_input=None) -> ConfigFlowResult:
        """The input at the target, and who chooses it."""
        assert self._draft is not None
        target = self._draft[CONN_TO]
        choices = input_choices(self.hass, self.options, target)
        errors: dict[str, str] = {}
        if user_input is not None:
            conn = {
                CONN_FROM: self._draft[CONN_FROM],
                CONN_TO: target,
                CONN_SIGNALS: list(self._draft[CONN_SIGNALS]),
                CONN_INPUT: user_input.get(CONN_INPUT) or None,
                CONN_MODE: user_input.get(CONN_MODE) or Mode.BMP.value,
            }
            conns = self._connections
            if self._editing is not None:
                conns[self._editing] = conn
            else:
                conns.append(conn)
            candidate = {**self.options, CONF_CONNECTIONS: conns}
            if conn[CONN_INPUT] and not has_input_control(self.options, target):
                errors[CONN_INPUT] = "no_input_control"
            elif conn[CONN_INPUT] and choices and conn[CONN_INPUT] not in choices:
                errors[CONN_INPUT] = "unknown_input"
            else:
                errors = validate_connections(self.hass, candidate)
            if not errors:
                self.options[CONF_CONNECTIONS] = conns
                self._draft = None
                self._editing = None
                return await self.async_step_cabling()
        current = self._connections[self._editing] if self._editing is not None else {}
        schema = vol.Schema(
            {
                vol.Optional(CONN_INPUT): _select(choices, None, custom=True),
                vol.Required(CONN_MODE, default=Mode.BMP.value): _select(MODES, "mode"),
            }
        )
        return self.async_show_form(
            step_id="connection_input",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or current),
            errors=errors,
            description_placeholders={"target": end_name(self.hass, self.options, target)},
        )

    # ------------------------------------------------------------ control

    async def async_step_control_display(self, user_input=None) -> ConfigFlowResult:
        return await self._control("display", user_input)

    async def async_step_control_speakers(self, user_input=None) -> ConfigFlowResult:
        return await self._control("speakers", user_input)

    async def _control(self, role: str, user_input) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            actions = user_input.get("input_actions")
            if actions not in (None, {}) and not valid_input_actions(actions):
                errors["input_actions"] = "invalid_input_actions"
            else:
                for key in CONTROL_KEYS:
                    self.options.pop(role_conf(role, key), None)
                for key, value in user_input.items():
                    if value not in (None, "", {}, []) and value != CONTROL_DEFAULTS.get(key):
                        self.options[role_conf(role, key)] = value
                return await self.async_step_cabling()
        schema = vol.Schema(
            {
                vol.Optional("inputs"): sel.EntitySelector(sel.EntitySelectorConfig(domain="media_player")),
                vol.Optional("input_actions"): sel.ObjectSelector(),
                vol.Optional("input_pause", default=0): sel.NumberSelector(
                    sel.NumberSelectorConfig(min=0, max=10, step=0.5, unit_of_measurement="s", mode=sel.NumberSelectorMode.BOX)
                ),
                vol.Required("power_on", default=Mode.BMP.value): _select(MODES, "mode"),
                vol.Required("power_off", default=Mode.BMP.value): _select(MODES, "mode"),
                vol.Optional("cec_fallback", default=False): sel.BooleanSelector(),
                vol.Optional("cec_timeout", default=15): sel.NumberSelector(
                    sel.NumberSelectorConfig(min=5, max=120, step=1, unit_of_measurement="s", mode=sel.NumberSelectorMode.BOX)
                ),
            }
        )
        current = {k: self.options[role_conf(role, k)] for k in CONTROL_KEYS if role_conf(role, k) in self.options}
        return self.async_show_form(
            step_id=f"control_{role}",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or current),
            errors=errors,
        )

