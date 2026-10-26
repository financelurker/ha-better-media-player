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

import hashlib
import logging
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, CoreState, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import selector as sel
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .players import is_group
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
RENAME_DELAY = 0.5  # s: a device's entities are renamed one after the other
NOT_KNOWN = (STATE_UNAVAILABLE, STATE_UNKNOWN)
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
        self._started: CALLBACK_TYPE | None = None
        self._watch: CALLBACK_TYPE | None = None
        self._later: CALLBACK_TYPE | None = None
        self._rename_later: CALLBACK_TYPE | None = None
        self._renamed: dict[str, dict] = {}  # entry id -> its options with the new ids
        self._ready = False  # the first check is due (after the start delay)
        self._stopped = False

    @callback
    def start(self) -> None:
        self._unsubs.append(self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_updated))
        if self.hass.state is CoreState.running:
            self._schedule(START_DELAY)
            return

        @callback
        def _started(_event: Event) -> None:
            self._started = None  # (a fired once-listener is gone: no unsub any more)
            self._schedule(START_DELAY)

        self._started = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started)

    @callback
    def stop(self) -> None:
        self._stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._started:
            self._started()
            self._started = None
        self._unwatch()
        for timer in (self._later, self._rename_later):
            if timer:
                timer()
        self._later = self._rename_later = None

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
    def _schedule(self, delay: float) -> None:
        if self._stopped:
            return
        if self._later:
            self._later()

        @callback
        def _run(_now) -> None:
            self._later = None
            self._ready = True
            self.check()

        self._later = async_call_later(self.hass, delay, _run)

    # --- renames --------------------------------------------------------------

    @callback
    def _follow_rename(self, old: str, new: str) -> None:
        """Several renames at once (e.g. a device's entities) reload an entry once."""
        if self._stopped:
            return
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            keys = [CONF_FOLLOW_PLAYER] if _is_hub(entry) else entity_keys()
            current = self._renamed.get(entry.entry_id, dict(entry.options))
            if (options := renamed_options(current, keys, old, new)) is not None:
                _LOGGER.info("%s: %s was renamed to %s, the room follows", entry.title, old, new)
                self._renamed[entry.entry_id] = options
        if self._renamed and self._rename_later is None:
            self._rename_later = async_call_later(self.hass, RENAME_DELAY, self._apply_renames)

    @callback
    def _apply_renames(self, _now) -> None:
        self._rename_later = None
        renamed, self._renamed = self._renamed, {}
        for entry_id, options in renamed.items():
            if (entry := self.hass.config_entries.async_get_entry(entry_id)) is not None:
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
            hub = _is_hub(entry)
            missing, disabled = [], []
            for key, ids in configured(entry).items():
                for entity_id in ids:
                    reg_entry = reg.async_get(entity_id)
                    if reg_entry is None and self.hass.states.get(entity_id) is None:
                        missing.append(entity_id)
                    elif reg_entry is not None and reg_entry.disabled_by is not None:
                        disabled.append(entity_id)
            watch.update(missing)
            player_keys = (CONF_FOLLOW_PLAYER,) if hub else PLAYER_KEYS
            player_ids = sorted({i for k in player_keys for i in _ids(entry.options.get(k))})
            for kind, ids in (("missing", missing), ("disabled", disabled)):
                if not ids:
                    continue
                if hub:  # (only the follow-me player)
                    wanted.add(self._issue(f"follow_player_{kind}", ids, ir.IssueSeverity.ERROR, entry, entity=ids[0]))
                    continue
                severity = ir.IssueSeverity.ERROR if set(player_ids) & set(ids) else ir.IssueSeverity.WARNING
                wanted.add(self._issue(f"{kind}_entities", ids, severity, entry, entities=_list(ids)))

            # group players: only from a state that tells (Music Assistant may still be loading)
            known = []
            for player in player_ids:
                state = self.hass.states.get(player)
                if state is None:
                    continue
                if state.state in NOT_KNOWN:
                    watch.add(player)
                else:
                    known.append(player)
            if hub:
                if known and not is_group(self.hass, known[0]):
                    wanted.add(self._issue("follow_player_not_group", known, ir.IssueSeverity.WARNING, entry, entity=known[0]))
                continue
            if groups := [p for p in known if is_group(self.hass, p)]:
                wanted.add(self._issue("group_player", groups, ir.IssueSeverity.WARNING, entry, entities=_list(groups)))
            for entity_id in {i for ids in configured(entry, SWITCHED_KEYS).values() for i in ids}:
                users.setdefault(entity_id, []).append(entry.title)

        shared = {e: sorted(rooms) for e, rooms in users.items() if len(rooms) > 1}
        if shared:
            lines = "\n".join(f"- `{e}`: {', '.join(rooms)}" for e, rooms in sorted(shared.items()))
            issue_id = f"shared_entities_{_digest(sorted(shared))}"
            ir.async_create_issue(
                self.hass, DOMAIN, issue_id, is_fixable=False, severity=ir.IssueSeverity.WARNING,
                translation_key="shared_entities", translation_placeholders={"entities": lines},
            )
            wanted.add(issue_id)

        # what no longer applies (all of this integration's issues come from here)
        for (domain, issue_id) in list(ir.async_get(self.hass).issues):
            if domain == DOMAIN and issue_id not in wanted:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

        self._unwatch()
        if watch:  # e.g. a YAML entity without unique id that comes later, a player that becomes available
            self._watch = async_track_state_change_event(self.hass, sorted(watch), lambda _e: self.changed())

    def _issue(self, key: str, ids: list[str], severity: ir.IssueSeverity, entry: ConfigEntry, **placeholders: str) -> str:
        """The issue's id names the entities: one dismissed does not hide others that come later."""
        issue_id = f"{key}_{entry.entry_id}_{_digest(ids)}"
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


def _digest(ids: list[str]) -> str:
    return hashlib.sha1("\n".join(sorted(ids)).encode()).hexdigest()[:8]


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
