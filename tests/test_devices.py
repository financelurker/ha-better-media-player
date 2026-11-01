"""The device layer: kept apart from the rest, one group of nodes per room."""

from __future__ import annotations

import ast
from pathlib import Path

from custom_components.better_media_player.devices import get_control

from .conftest import setup_room

DEVICES = Path(__file__).resolve().parent.parent / "custom_components" / "better_media_player" / "devices"


def test_the_device_layer_imports_nothing_of_the_integration():
    """devices/ can become an integration of its own: it imports only itself
    (and Home Assistant), nothing of room/, follow/, players/ or the integration's root."""
    outside = []
    for path in sorted(DEVICES.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level > 1:
                outside.append(f"{path.name}: {'.' * node.level}{node.module or ''}")
            if isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").startswith("custom_components"):
                outside.append(f"{path.name}: {node.module}")
            if isinstance(node, ast.Import):
                outside += [f"{path.name}: {a.name}" for a in node.names if a.name.startswith("custom_components")]
    assert outside == []


async def test_a_room_registers_its_roles_and_leaves_on_unload(hass, home, freezer):
    entry = await setup_room(hass)
    control = get_control(hass)
    group = control.groups[entry.entry_id]
    assert group is entry.runtime_data.power.devices
    assert set(group.nodes) == set(entry.runtime_data.power.roles)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.entry_id not in control.groups


async def test_an_old_group_leaving_does_not_drop_the_new_one(hass, home, freezer):
    """A room set up again before the old one finished unloading keeps its new group."""
    entry = await setup_room(hass)
    control = get_control(hass)
    power = entry.runtime_data.power
    old = power.devices
    new = control.register(entry.entry_id, power, list(power.roles.values()))
    control.unregister(entry.entry_id, old)
    assert control.groups[entry.entry_id] is new
