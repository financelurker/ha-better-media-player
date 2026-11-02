"""The configuration panel in Home Assistant's sidebar (admins only).

The panel is a web component (frontend/, Lit shipped with the integration: it
works without internet). It reads and writes through the websocket commands in
ws.py, where rooms are also set up and changed. It is registered only where Home Assistant's frontend runs; without it
(tests) there is nothing to show, and the integration works the same.
"""

from __future__ import annotations

from pathlib import Path

from homeassistant.core import HomeAssistant

from ..const import DOMAIN, PANEL_URL
from .ws import async_setup_ws

PANEL_TITLE = "Better Media Player"
PANEL_ICON = "mdi:speaker-multiple"
COMPONENT = "bmp-panel"
STATIC = f"/{DOMAIN}_panel"  # + /<version>/<file>: a new version is loaded fresh
FRONTEND = Path(__file__).parent.parent / "frontend"


async def async_setup_panel(hass: HomeAssistant, version: str) -> None:
    async_setup_ws(hass)
    if "frontend" not in hass.config.components or hass.http is None:
        return
    from homeassistant.components import panel_custom  # noqa: PLC0415 (needs the frontend)
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    base = f"{STATIC}/{version}"
    # a development copy (version 0.0.0) is not cached, so a change shows on reload
    await hass.http.async_register_static_paths([StaticPathConfig(base, str(FRONTEND), version != "0.0.0")])
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name=COMPONENT,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url=f"{base}/{COMPONENT}.js",
        require_admin=True,
        config_panel_domain=DOMAIN,  # "Configure" of an entry opens the panel (?config_entry=<id>)
    )
