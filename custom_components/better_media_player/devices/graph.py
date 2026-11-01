"""Routes: which nodes a source needs, and on which inputs.

For each signal a source needs, its route follows the connections that carry
the signal, from the source on (a TV app's sound back over ARC to the receiver:
a connection of its own). Every node putting out the signal that the
connections do not reach is fed directly, without an input to choose (as a
room without cabling: the display for the picture, speakers and subwoofer for
the sound).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .model import Edge, Mode, Node, Signal


@dataclass(frozen=True)
class Route:
    """What a source needs: its nodes, and the input wanted on each."""

    nodes: frozenset[str]
    inputs: Mapping[str, tuple[str, Mode]]  # node -> (input, who chooses it)


def route(source: str, needs: Iterable[Signal], nodes: Mapping[str, Node], edges: Iterable[Edge]) -> Route:
    """The route of source for the signals it needs."""
    edges = list(edges)
    reached: set[str] = set()
    inputs: dict[str, tuple[str, Mode]] = {}
    for signal in needs:
        seen = {source}
        todo = [source]
        while todo:
            at = todo.pop(0)
            for edge in edges:
                if edge.source != at or signal not in edge.signals or edge.target not in nodes:
                    continue
                if edge.input is not None:
                    inputs.setdefault(edge.target, (edge.input, edge.mode))
                if edge.target not in seen:
                    seen.add(edge.target)
                    todo.append(edge.target)
        seen.discard(source)
        reached |= seen
        # what the connections do not reach is fed directly
        reached |= {k for k, n in nodes.items() if signal in n.gives and k != source}
    return Route(frozenset(reached), inputs)


def cycles(edges: Iterable[Edge]) -> list[tuple[Signal, list[str]]]:
    """Connections that run in a circle for a signal (with the nodes of the circle)."""
    edges = list(edges)
    found: list[tuple[Signal, list[str]]] = []
    for signal in Signal:
        succ: dict[str, list[str]] = {}
        for e in edges:
            if signal in e.signals:
                succ.setdefault(e.source, []).append(e.target)
        state: dict[str, int] = {}
        stack: list[str] = []

        def visit(n: str) -> bool:
            state[n] = 1
            stack.append(n)
            for m in succ.get(n, []):
                if state.get(m) == 1:
                    found.append((signal, stack[stack.index(m):] + [m]))
                    return True
                if m not in state and visit(m):
                    return True
            stack.pop()
            state[n] = 2
            return False

        for n in list(succ):
            if n not in state and visit(n):
                break
    return found
