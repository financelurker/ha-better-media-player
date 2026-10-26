"""Media browser and search of the room player.

The room browser bundles the browsers of the room's players:

    <room>
    ├── Queue: <video player>   lifted from its browser root (e.g. Better Kodi)
    ├── Queue: <music player>   current + next item (e.g. Music Assistant)
    ├── <video player>          its browser; HA media sources while it cannot browse
    └── <music player>          its browser, playable items offer play now/next/add/replace

What a player's system can do (queue, browsing while off, its own play next /
add entries) comes from its backend (players/).

Every item that comes from a player is wrapped into an id of our own that names
the source, so browsing further and playing go to the right player:

    better_media_player://<kind>/<source>/<base64url(json [type, id])>

Only what a player supports is offered (BROWSE_MEDIA, SEARCH_MEDIA, MEDIA_ENQUEUE).
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    DOMAIN as MP_DOMAIN,
)
from homeassistant.components.media_player import (
    BrowseError,
    BrowseMedia,
    MediaClass,
    MediaPlayerEntityFeature,
    SearchError,
    SearchMedia,
    SearchMediaQuery,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.translation import async_get_translations

from .players.base import ENQUEUE_MODES
from .const import DOMAIN

if TYPE_CHECKING:
    from .controller import RoomController

_LOGGER = logging.getLogger(__name__)

PREFIX = "better_media_player://"
ROOT_ID = f"{PREFIX}root"
KIND_SOURCE = "source"  # root folder of a player
KIND_ITEM = "item"  # an item of a player (browse further / play)
KIND_MEDIA_SOURCE = "media"  # HA media source item, played on a player
KIND_CHOOSE = "choose"  # play now / next / add / replace folder
KIND_ENQUEUE = "enqueue"  # one of the choices (kind = enqueue-<mode>)
KIND_MUSIC_QUEUE = "mqueue"  # queue folder of a player whose backend gives its queue
KIND_INFO = "info"  # not playable information entry

CHOICES = ENQUEUE_MODES
MAX_CHOICE_ITEMS = 1000


# ------------------------------------------------------------------ id helpers


def _encode(media_type: str | None, media_id: str | None) -> str:
    raw = json.dumps([media_type, media_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(payload: str) -> tuple[str | None, str | None]:
    try:
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        media_type, media_id = json.loads(raw)
    except (binascii.Error, ValueError, TypeError) as err:
        raise BrowseError(f"Unknown item: {payload}") from err
    return media_type, media_id


def make_id(kind: str, source: str, media_type: str | None = None, media_id: str | None = None) -> str:
    return f"{PREFIX}{kind}/{source}/{_encode(media_type, media_id)}"


def is_own_id(media_id: str | None) -> bool:
    return bool(media_id) and media_id.startswith(PREFIX)


def parse_id(media_id: str) -> tuple[str, str, str | None, str | None]:
    """Return kind, source, inner type, inner id."""
    try:
        kind, source, payload = media_id.removeprefix(PREFIX).split("/", 2)
    except ValueError as err:
        raise BrowseError(f"Unknown item: {media_id}") from err
    media_type, inner_id = _decode(payload)
    return kind, source, media_type, inner_id


def browse_media_from(payload: BrowseMedia | dict[str, Any]) -> BrowseMedia:
    """Rebuild a BrowseMedia tree from a browse_media action response."""
    if isinstance(payload, BrowseMedia):
        return payload
    children = [browse_media_from(child) for child in payload.get("children") or []]
    return BrowseMedia(
        media_class=payload.get("media_class") or MediaClass.DIRECTORY,
        media_content_id=payload.get("media_content_id") or "",
        media_content_type=payload.get("media_content_type") or "",
        title=payload.get("title") or "",
        can_play=bool(payload.get("can_play")),
        can_expand=bool(payload.get("can_expand")),
        can_search=bool(payload.get("can_search")),
        children=children or None,
        children_media_class=payload.get("children_media_class"),
        thumbnail=payload.get("thumbnail"),
        not_shown=payload.get("not_shown") or 0,
    )


class RoomBrowser:
    """Media browser and search of one room."""

    def __init__(self, hass: HomeAssistant, ctrl: RoomController, source_name) -> None:
        self.hass = hass
        self.ctrl = ctrl
        self._source_name = source_name  # callable(key) -> display name
        self._choice_items: dict[str, BrowseMedia] = {}

    # --------------------------------------------------------------- support

    def _features(self, key: str) -> int:
        src = self.ctrl.sources.get(key)
        state = self.hass.states.get(src.player) if src else None
        return int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) or 0) if state else 0

    def supports(self, key: str, feature: MediaPlayerEntityFeature) -> bool:
        return bool(self._features(key) & feature)

    def any_supports(self, feature: MediaPlayerEntityFeature) -> bool:
        return any(self.supports(k, feature) for k in self.ctrl.sources)

    async def _texts(self) -> dict[str, str]:
        translations = await async_get_translations(self.hass, self.hass.config.language, "selector", [DOMAIN])
        prefix = f"component.{DOMAIN}.selector.browse.options."
        return {k.removeprefix(prefix): v for k, v in translations.items() if k.startswith(prefix)}

    # ---------------------------------------------------------------- browse

    async def async_browse(self, media_content_type: str | None, media_content_id: str | None) -> BrowseMedia:
        if not media_content_id or media_content_id == ROOT_ID:
            return await self._root()
        if not is_own_id(media_content_id):
            # a plain id (e.g. stored before): ask the active player
            src = self.ctrl.active_source
            if src is None:
                raise BrowseError(f"Unknown item: {media_content_id}")
            return await self._wrap_child_browse(src.key, media_content_type, media_content_id)
        kind, key, inner_type, inner_id = parse_id(media_content_id)
        if key not in self.ctrl.sources:
            raise BrowseError(f"Unknown source: {key}")
        if kind == KIND_SOURCE:
            return await self._source_root(key)
        if kind == KIND_ITEM:
            return await self._wrap_child_browse(key, inner_type, inner_id)
        if kind == KIND_MEDIA_SOURCE:
            return await self._media_source(key, inner_id)
        if kind == KIND_CHOOSE:
            return await self._choices(key, media_content_id, inner_type, inner_id)
        if kind == KIND_MUSIC_QUEUE:
            return await self._music_queue(key)
        raise BrowseError(f"Cannot browse {media_content_id}")

    async def _root(self) -> BrowseMedia:
        texts = await self._texts()
        children: list[BrowseMedia] = []
        # 1. queues, one click closer
        for key, src in self.ctrl.sources.items():
            title = texts.get("queue_of", "Queue: {name}").format(name=self._source_name(key))
            if src.backend.has_queue:
                children.append(
                    BrowseMedia(
                        media_class=MediaClass.PLAYLIST,
                        media_content_id=make_id(KIND_MUSIC_QUEUE, key),
                        media_content_type="playlist",
                        title=title,
                        can_play=False,
                        can_expand=True,
                    )
                )
            elif src.backend.lifts_queue and self.supports(key, MediaPlayerEntityFeature.BROWSE_MEDIA):
                try:
                    root = await self._child_browse(key, None, None)
                except BrowseError:
                    root = None
                if (item := src.backend.queue_entry(root)) is not None:
                    queue = self._wrap(key, item)
                    queue.title = title
                    children.append(queue)
        # 2. the players
        for key in self.ctrl.sources:
            if not self.supports(key, MediaPlayerEntityFeature.BROWSE_MEDIA):
                continue
            children.append(
                BrowseMedia(
                    media_class=MediaClass.DIRECTORY,
                    media_content_id=make_id(KIND_SOURCE, key),
                    media_content_type="directory",
                    title=self._source_name(key),
                    can_play=False,
                    can_expand=True,
                    children_media_class=MediaClass.DIRECTORY,
                )
            )
        return BrowseMedia(
            media_class=MediaClass.DIRECTORY,
            media_content_id=ROOT_ID,
            media_content_type="directory",
            title=self.ctrl.entry.title,
            can_play=False,
            can_expand=True,
            can_search=self.any_supports(MediaPlayerEntityFeature.SEARCH_MEDIA),
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )

    async def _source_root(self, key: str) -> BrowseMedia:
        try:
            result = await self._wrap_child_browse(key, None, None)
        except BrowseError:
            if self.ctrl.sources[key].backend.media_sources is None:
                raise
            # it cannot browse right now (e.g. Kodi off): at least the media sources (e.g. Jellyfin)
            try:
                result = await self._media_source(key, None, title=self._source_name(key))
            except BrowseError:
                result = BrowseMedia(
                    media_class=MediaClass.DIRECTORY,
                    media_content_id=make_id(KIND_SOURCE, key),
                    media_content_type="directory",
                    title=self._source_name(key),
                    can_play=False,
                    can_expand=True,
                )
            texts = await self._texts()
            hint = BrowseMedia(
                media_class=MediaClass.APP,
                media_content_id=make_id(KIND_INFO, key),
                media_content_type="info",
                title=texts.get("player_off", "{name} is off: its library appears once it is on").format(
                    name=self._source_name(key)
                ),
                can_play=False,
                can_expand=False,
            )
            result.children = [hint, *(result.children or [])]
            return result
        result.title = self._source_name(key)
        result.media_content_id = make_id(KIND_SOURCE, key)
        return result

    async def _child_browse(self, key: str, media_type: str | None, media_id: str | None) -> BrowseMedia:
        src = self.ctrl.sources[key]
        state = self.hass.states.get(src.player)
        if state is None:
            raise BrowseError(f"{src.player} is missing")
        if not src.backend.can_browse(state.state):
            # e.g. Kodi needs a connection to browse; Music Assistant can browse its
            # library while the player itself is off
            raise BrowseError(f"{src.player} is {state.state}")
        data: dict[str, Any] = {ATTR_ENTITY_ID: src.player}
        if media_type is not None:
            data["media_content_type"] = media_type
        if media_id is not None:
            data["media_content_id"] = media_id
        try:
            response = await self.hass.services.async_call(
                MP_DOMAIN, "browse_media", data, blocking=True, return_response=True
            )
        except HomeAssistantError as err:
            raise BrowseError(str(err)) from err
        payload = (response or {}).get(src.player)
        if payload is None:
            raise BrowseError(f"{src.player} returned nothing")
        return browse_media_from(payload)

    async def _wrap_child_browse(self, key: str, media_type: str | None, media_id: str | None) -> BrowseMedia:
        if media_source.is_media_source_id(media_id or "") and self.ctrl.sources[key].backend.media_sources:
            try:
                return self._wrap(key, await self._child_browse(key, media_type, media_id), deep=True)
            except BrowseError:
                return await self._media_source(key, media_id)
        return self._wrap(key, await self._child_browse(key, media_type, media_id), deep=True)

    async def _media_source(self, key: str, media_id: str | None, title: str | None = None) -> BrowseMedia:
        """HA media sources (Jellyfin, local media, ...), for playing on a player."""
        try:
            result = await media_source.async_browse_media(
                self.hass, media_id, content_filter=self.ctrl.sources[key].backend.media_sources
            )
        except Exception as err:  # noqa: BLE001 - media_source not set up, unknown id, ...
            raise BrowseError(str(err)) from err
        wrapped = self._wrap_media_source(key, result)
        wrapped.children = [self._wrap_media_source(key, c) for c in result.children or []] or None
        wrapped.not_shown = result.not_shown
        if title:
            wrapped.title = title
            wrapped.media_content_id = make_id(KIND_SOURCE, key)
        return wrapped

    def _wrap_media_source(self, key: str, item: BrowseMedia) -> BrowseMedia:
        return BrowseMedia(
            media_class=item.media_class,
            media_content_id=make_id(KIND_MEDIA_SOURCE, key, item.media_content_type, item.media_content_id),
            media_content_type=item.media_content_type,
            title=item.title,
            can_play=item.can_play,
            can_expand=item.can_expand,
            children_media_class=item.children_media_class,
            thumbnail=item.thumbnail,
        )

    def _wrap(self, key: str, item: BrowseMedia, deep: bool = False) -> BrowseMedia:
        """Our copy of a player's item, with an id that names the source."""
        wrapped = BrowseMedia(
            media_class=item.media_class,
            media_content_id=make_id(KIND_ITEM, key, item.media_content_type, item.media_content_id),
            media_content_type=item.media_content_type,
            title=item.title,
            can_play=item.can_play,
            can_expand=item.can_expand,
            can_search=item.can_search,
            children_media_class=item.children_media_class,
            thumbnail=item.thumbnail,
            not_shown=item.not_shown,
        )
        if deep and item.children:
            wrapped.children = [self._offer_choices(key, self._wrap(key, c)) for c in item.children]
        return wrapped

    def _offer_choices(self, key: str, item: BrowseMedia) -> BrowseMedia:
        """A playable item opens play now / next / add / replace (like Better Kodi).

        Where the room offers them (music players; video players bring their own,
        e.g. Better Kodi) and the player supports enqueueing. The item stays
        playable, so its play button still plays it right away.
        """
        if (
            not self.ctrl.sources[key].backend.room_choices
            or not item.can_play
            or item.can_expand
            or not self.supports(key, MediaPlayerEntityFeature.MEDIA_ENQUEUE)
        ):
            return item
        _kind, _key, inner_type, inner_id = parse_id(item.media_content_id)
        choose_id = make_id(KIND_CHOOSE, key, inner_type, inner_id)
        self._choice_items[choose_id] = item
        while len(self._choice_items) > MAX_CHOICE_ITEMS:
            del self._choice_items[next(iter(self._choice_items))]
        return BrowseMedia(
            media_class=item.media_class,
            media_content_id=choose_id,
            media_content_type=item.media_content_type,
            title=item.title,
            can_play=True,
            can_expand=True,
            thumbnail=item.thumbnail,
            children_media_class=item.media_class,
        )

    async def _choices(self, key: str, choose_id: str, inner_type: str | None, inner_id: str | None) -> BrowseMedia:
        texts = await self._texts()
        item = self._choice_items.get(choose_id)
        media_class = item.media_class if item else MediaClass.MUSIC
        children = [
            BrowseMedia(
                media_class=media_class,
                media_content_id=make_id(f"{KIND_ENQUEUE}-{mode}", key, inner_type, inner_id),
                media_content_type=inner_type or "music",
                title=texts.get(mode, mode),
                can_play=True,
                can_expand=False,
                thumbnail=item.thumbnail if item else None,
            )
            for mode in CHOICES
        ]
        return BrowseMedia(
            media_class=media_class,
            media_content_id=choose_id,
            media_content_type=inner_type or "music",
            title=item.title if item else texts.get("play", "play"),
            can_play=True,
            can_expand=True,
            thumbnail=item.thumbnail if item else None,
            children=children,
            children_media_class=media_class,
        )

    async def _music_queue(self, key: str) -> BrowseMedia:
        """Current and next item (e.g. Music Assistant: all that HA exposes)."""
        texts = await self._texts()
        src = self.ctrl.sources[key]
        children: list[BrowseMedia] = []
        queue = await src.backend.queue(src.player)
        for label, item in (("now_playing", queue.current), ("up_next", queue.next)):
            if item is None:
                continue
            children.append(
                BrowseMedia(
                    media_class=MediaClass.TRACK,
                    media_content_id=make_id(KIND_INFO, key, None, item.item_id),
                    media_content_type="track",
                    title=f"{texts.get(label, label)}: {item.title}",
                    can_play=False,
                    can_expand=False,
                    thumbnail=item.image,
                )
            )
        if not children:
            children.append(
                BrowseMedia(
                    media_class=MediaClass.APP,
                    media_content_id=make_id(KIND_INFO, key),
                    media_content_type="info",
                    title=texts.get("queue_empty", "Nothing queued"),
                    can_play=False,
                    can_expand=False,
                )
            )
        return BrowseMedia(
            media_class=MediaClass.PLAYLIST,
            media_content_id=make_id(KIND_MUSIC_QUEUE, key),
            media_content_type="playlist",
            title=texts.get("queue_of", "Queue: {name}").format(name=self._source_name(key)),
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.TRACK,
        )

    # ---------------------------------------------------------------- search

    async def async_search(self, query: SearchMediaQuery) -> SearchMedia:
        keys = [k for k in self.ctrl.sources if self.supports(k, MediaPlayerEntityFeature.SEARCH_MEDIA)]
        inner_id = query.media_content_id
        if not inner_id or inner_id == ROOT_ID:
            inner_id = None  # the whole room: all players that can search
        elif is_own_id(inner_id):
            try:
                kind, key, _t, inner_id = parse_id(inner_id)
            except BrowseError as err:
                raise SearchError(str(err)) from err
            keys = [k for k in keys if k == key]
            if kind == KIND_SOURCE:
                inner_id = None
        results: list[BrowseMedia] = []
        for key in keys:
            src = self.ctrl.sources[key]
            data: dict[str, Any] = {ATTR_ENTITY_ID: src.player, "search_query": query.search_query}
            if query.media_content_type:
                data["media_content_type"] = query.media_content_type
            if inner_id:
                data["media_content_id"] = inner_id
            if query.media_filter_classes:
                data["media_filter_classes"] = [str(c) for c in query.media_filter_classes]
            try:
                response = await self.hass.services.async_call(
                    MP_DOMAIN, "search_media", data, blocking=True, return_response=True
                )
            except HomeAssistantError as err:
                _LOGGER.debug("search on %s failed: %s", src.player, err)
                continue
            payload = (response or {}).get(src.player)
            items = payload.result if isinstance(payload, SearchMedia) else (payload or {}).get("result") or []
            results += [self._wrap(key, browse_media_from(i)) for i in items]
        return SearchMedia(result=results)

    # ------------------------------------------------------------------ play

    def resolve_play(self, media_id: str) -> tuple[str, str | None, str | None, str | None]:
        """Our id -> (source, inner type, inner id, enqueue mode or None)."""
        kind, key, inner_type, inner_id = parse_id(media_id)
        if key not in self.ctrl.sources:
            raise HomeAssistantError(f"Unknown source: {key}")
        if kind in (KIND_ITEM, KIND_MEDIA_SOURCE, KIND_CHOOSE):
            return key, inner_type, inner_id, None
        if kind.startswith(f"{KIND_ENQUEUE}-"):
            mode = kind.removeprefix(f"{KIND_ENQUEUE}-")
            if mode in CHOICES:
                return key, inner_type, inner_id, mode
        raise HomeAssistantError(f"{media_id} cannot be played")

    def player_mode(self, key: str, inner_id: str | None) -> str | None:
        """Enqueue mode a player encodes in its own ids (e.g. Better Kodi's choices)."""
        src = self.ctrl.sources.get(key)
        return src.backend.enqueue_mode(inner_id) if src is not None else None
