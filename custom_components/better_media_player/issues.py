"""Repairs: what the rooms are set up with, checked against Home Assistant.

- A renamed entity is followed: its new id replaces the old one in every
  entry's options (the entry reloads).
- Entities that do not exist (any more) or are disabled, entities that
  several rooms switch, and group players where a room's own player belongs
  (or no group where follow me needs one) become issues in Repairs. They
  clear by themselves once the cause is gone.

Checked a while after Home Assistant has started (integrations may add their
entities late), then again whenever entities, entries or the missing
entities' states change.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CALLBACK_TYPE, CoreState, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import selector as sel
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .backend import is_group
from .const import (
    CONF_AUDIO_HOST,
    CONF_AUDIO_PLAYER,
    CONF_BASS,
    CONF_DISPLAY,
    CONF_ENTRY_TYPE,
    CONF_FOLLOW_PLAYER,
    CONF_SPEAKERS,
    CONF_VIDEO_HOST,
    CONF_VIDEO_PLAYER,
    CONF_VOLUME_CONTROL,
    CONF_VOLUME_ENTITY,
    DOMAIN,
    ENTRY_TYPE_HUB,
    VOLUME_DEVICE,
)

_LOGGER = logging.getLogger(__name__)

START_DELAY = 60  # s after Home Assistant has started
RECHECK_DELAY = 1  # s: changes come in bursts (a reload, several registry updates)
PLAYER_KEYS = (CONF_VIDEO_PLAYER, CONF_AUDIO_PLAYER)
# what a room switches or plays on: one room each
SWITCHED_KEYS = (*PLAYER_KEYS, CONF_VIDEO_HOST, CONF_AUDIO_HOST, CONF_DISPLAY, CONF_SPEAKERS, CONF_BASS, CONF_VOLUME_ENTITY)


def entity_keys() -> list[str]:
    """The room options that name entities (every entity selector of the setup)."""
    from .config_flow import _fields  # (the setup's fields are the source of truth)

    return [key for key, field in _fields().items() if isinstance(field, sel.EntitySelector)]


def _is_hub(entry: ConfigEntry) -> bool:
    return entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_HUB


def _ids(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str) and v]
    return []


def configured(entry: ConfigEntry, keys: tuple[str, ...] | list[str] | None = None) -> dict[str, list[str]]:
    """The entities an entry is set up with, by option key."""
    options = entry.options
    if _is_hub(entry):
        keys = [CONF_FOLLOW_PLAYER]
    elif keys is None:
        keys = entity_keys()
    out = {}
    for key in keys:
        if key == CONF_VOLUME_ENTITY and options.get(CONF_VOLUME_CONTROL) != VOLUME_DEVICE:
            continue  # (left from before: the room's volume is on the players)
        if ids := _ids(options.get(key)):
            out[key] = ids
    return out


def renamed_options(options: dict, keys: list[str], old: str, new: str) -> dict | None:
    """The options with old replaced by new, or None if old is not in them."""
    out = dict(options)
    changed = False
    for key in keys:
        value = out.get(key)
        if value == old:
            out[key] = new
            changed = True
        elif isinstance(value, list) and old in value:
            out[key] = [new if v == old else v for v in value]
            changed = True
    return out if changed else None


class IssueChecker:
    """Follows renames and keeps the integration's issues up to date."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._unsubs: list[CALLBACK_TYPE] = []
        self._watch: CALLBACK_TYPE | None = None
        self._later: CALLBACK_TYPE | None = None
        self._ready = False  # the first check is due (after the start delay)

    @callback
    def start(self) -> None:
        self._unsubs.append(self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_updated))
        if self.hass.state is CoreState.running:
            self._schedule(START_DELAY, ready=True)
        else:
            self._unsubs.append(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, lambda _e: self._schedule(START_DELAY, ready=True)
                )
            )

    @callback
    def stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._unwatch()
        if self._later:
            self._later()
            self._later = None

    # --- triggers -------------------------------------------------------------

    @callback
    def _registry_updated(self, event: Event) -> None:
        data = event.data
        if data["action"] == "update" and (old := data.get("old_entity_id")):
            self._follow_rename(old, data["entity_id"])
        self.changed()

    @callback
    def changed(self) -> None:
        """Entries or entities changed: check again (soon, once the first check was due)."""
        if self._ready:
            self._schedule(RECHECK_DELAY)

    @callback
    def _schedule(self, delay: float, ready: bool = False) -> None:
        if self._later:
            self._later()

        @callback
        def _run(_now) -> None:
            self._later = None
            self._ready = True
            self.check()

        self._later = async_call_later(self.hass, delay, _run)
        if ready:
            self._ready = False

    # --- renames --------------------------------------------------------------

    @callback
    def _follow_rename(self, old: str, new: str) -> None:
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            keys = [CONF_FOLLOW_PLAYER] if _is_hub(entry) else entity_keys()
            if (options := renamed_options(dict(entry.options), keys, old, new)) is not None:
                _LOGGER.info("%s: %s was renamed to %s, the room follows", entry.title, old, new)
                self.hass.config_entries.async_update_entry(entry, options=options)

    # --- the check ------------------------------------------------------------

    @callback
    def check(self) -> None:
        reg = er.async_get(self.hass)
        entries = [e for e in self.hass.config_entries.async_entries(DOMAIN) if e.disabled_by is None]
        wanted: set[str] = set()
        watch: set[str] = set()
        users: dict[str, list[str]] = {}  # switched entity -> rooms

        for entry in entries:
            missing, disabled = [], []
            for key, ids in configured(entry).items():
                for entity_id in ids:
                    reg_entry = reg.async_get(entity_id)
                    if reg_entry is None and self.hass.states.get(entity_id) is None:
                        missing.append(entity_id)
                    elif reg_entry is not None and reg_entry.disabled_by is not None:
                        disabled.append(entity_id)
            watch.update(missing)
            players = PLAYER_KEYS if not _is_hub(entry) else (CONF_FOLLOW_PLAYER,)
            player_ids = {i for k in players for i in _ids(entry.options.get(k))}
            for kind, ids in (("missing_entities", missing), ("disabled_entities", disabled)):
                if ids:
                    severity = ir.IssueSeverity.ERROR if player_ids & set(ids) else ir.IssueSeverity.WARNING
                    wanted.add(self._issue(f"{kind}_{entry.entry_id}", kind, severity, entry, entities=_list(ids)))

            if _is_hub(entry):
                player = entry.options.get(CONF_FOLLOW_PLAYER)
                if player and self.hass.states.get(player) is not None and not is_group(self.hass, player):
                    wanted.add(
                        self._issue(
                            f"follow_player_not_group_{entry.entry_id}", "follow_player_not_group",
                            ir.IssueSeverity.WARNING, entry, entity=player,
                        )
                    )
                continue
            groups = [p for p in player_ids if is_group(self.hass, p)]
            if groups:
                wanted.add(
                    self._issue(
                        f"group_player_{entry.entry_id}", "group_player", ir.IssueSeverity.WARNING, entry,
                        entities=_list(sorted(groups)),
                    )
                )
            for entity_id in {i for ids in configured(entry, SWITCHED_KEYS).values() for i in ids}:
                users.setdefault(entity_id, []).append(entry.title)

        shared = {e: sorted(rooms) for e, rooms in users.items() if len(rooms) > 1}
        if shared:
            lines = "\n".join(f"- `{e}`: {', '.join(rooms)}" for e, rooms in sorted(shared.items()))
            ir.async_create_issue(
                self.hass, DOMAIN, "shared_entities", is_fixable=False, severity=ir.IssueSeverity.WARNING,
                translation_key="shared_entities", translation_placeholders={"entities": lines},
            )
            wanted.add("shared_entities")

        # what no longer applies
        for (domain, issue_id) in list(ir.async_get(self.hass).issues):
            if domain == DOMAIN and issue_id not in wanted:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

        self._unwatch()
        if watch:  # e.g. a YAML entity without unique id that comes later
            self._watch = async_track_state_change_event(self.hass, sorted(watch), lambda _e: self.changed())

    def _issue(self, issue_id: str, key: str, severity: ir.IssueSeverity, entry: ConfigEntry, **placeholders: str) -> str:
        ir.async_create_issue(
            self.hass, DOMAIN, issue_id, is_fixable=False, severity=severity, translation_key=key,
            translation_placeholders={"room": entry.title, **placeholders},
        )
        return issue_id

    @callback
    def _unwatch(self) -> None:
        if self._watch:
            self._watch()
            self._watch = None


def _list(ids: list[str]) -> str:
    return "\n".join(f"- `{i}`" for i in ids)


def get_checker(hass: HomeAssistant) -> IssueChecker | None:
    return hass.data.get(DOMAIN, {}).get("issues")


@callback
def async_setup_issues(hass: HomeAssistant) -> Callable[[], None]:
    checker = IssueChecker(hass)
    hass.data.setdefault(DOMAIN, {})["issues"] = checker
    checker.start()
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, lambda _e: checker.stop())
    return checker.stop
