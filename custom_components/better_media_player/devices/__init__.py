"""The device layer: power devices, their state and switching them.

Kept apart from the rooms (nothing here imports room/, follow/ or players/), so
it can become an integration of its own later. Owners (rooms) talk to it only
through api.NodeOwner and the group they get from DeviceControl.register.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .api import NodeOwner
from .const import DATA_KEY
from .group import DeviceGroup
from .model import Node, Standby

__all__ = ["DeviceControl", "DeviceGroup", "Node", "NodeOwner", "Standby", "get_control"]


class DeviceControl:
    """All groups of nodes in Home Assistant (one per owner)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.groups: dict[str, DeviceGroup] = {}

    def register(self, key: str, owner: NodeOwner, nodes: list[Node]) -> DeviceGroup:
        """An owner brings its nodes; unregister when it unloads."""
        group = DeviceGroup(self.hass, owner, nodes)
        self.groups[key] = group
        return group

    def unregister(self, key: str, group: DeviceGroup) -> None:
        """The owner unloads (a reloaded owner may already have registered anew)."""
        if self.groups.get(key) is group:
            del self.groups[key]


def get_control(hass: HomeAssistant) -> DeviceControl:
    """The device control of this Home Assistant (created on first use)."""
    control = hass.data.get(DATA_KEY)
    if control is None:
        control = hass.data[DATA_KEY] = DeviceControl(hass)
    return control
