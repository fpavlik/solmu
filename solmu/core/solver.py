"""Flow allocation: solver interface, greedy solver and min-cost-flow reference solver."""

from __future__ import annotations

import heapq
import math
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import networkx as nx

from solmu.core.model import Commodity, NodeId

ArcId = str
EPS = 1e-9

TONNE_SCALE = 10
COST_SCALE = 100


@dataclass(frozen=True)
class EffectiveArc:
    """A directed arc with its residual capacity (t/day) for the current day."""

    id: ArcId
    edge_id: str
    from_node: NodeId
    to_node: NodeId
    capacity: float
    cost_per_tonne: float
    transit_days: int

    @property
    def cost(self) -> float:
        """Solver cost of moving one tonne over this arc."""
        return self.cost_per_tonne * self.transit_days


@dataclass(frozen=True)
class EffectiveGraph:
    """Arcs with effective residual capacities; `reduced` returns a new graph."""

    arcs: tuple[EffectiveArc, ...]

    def reduced(self, flows: Mapping[ArcId, float]) -> EffectiveGraph:
        """New graph with the given arc flows subtracted from capacities."""
        return EffectiveGraph(
            tuple(
                EffectiveArc(
                    a.id,
                    a.edge_id,
                    a.from_node,
                    a.to_node,
                    max(0.0, a.capacity - flows.get(a.id, 0.0)),
                    a.cost_per_tonne,
                    a.transit_days,
                )
                for a in self.arcs
            )
        )


@dataclass(frozen=True)
class PathFlow:
    """Tonnes moved from `origin` to `destination` along `arcs` (arc ids, in order)."""

    origin: NodeId
    destination: NodeId
    arcs: tuple[ArcId, ...]
    tonnes: float


class FlowSolver(Protocol):
    """Allocates supply to needs for one commodity; returns tonnes per arc id."""

    def allocate(
        self,
        graph: EffectiveGraph,
        supplies: Mapping[NodeId, float],
        needs: Mapping[NodeId, float],
        commodity: Commodity,
    ) -> dict[ArcId, float]: ...


def net_supplies_needs(
    supplies: Mapping[NodeId, float], needs: Mapping[NodeId, float]
) -> tuple[dict[NodeId, float], dict[NodeId, float], dict[NodeId, float]]:
    """Cancel supply against need at the same node.

    Returns (net supplies, net needs, locally matched tonnes per node).
    """
    net_s: dict[NodeId, float] = {}
    net_n: dict[NodeId, float] = {}
    local: dict[NodeId, float] = {}
    for node in sorted(set(supplies) | set(needs)):
        s, n = supplies.get(node, 0.0), needs.get(node, 0.0)
        matched = min(s, n)
        if matched > EPS:
            local[node] = matched
        if s - matched > EPS:
            net_s[node] = s - matched
        if n - matched > EPS:
            net_n[node] = n - matched
    return net_s, net_n, local


class GreedySolver:
    """Serves needs one by one from the cheapest reachable supply along shortest paths."""

    def allocate(
        self,
        graph: EffectiveGraph,
        supplies: Mapping[NodeId, float],
        needs: Mapping[NodeId, float],
        commodity: Commodity,
    ) -> dict[ArcId, float]:
        """Return arc flows; needs are served in node-id order."""
        remaining_supply, remaining_need, _ = net_supplies_needs(supplies, needs)
        residual = {a.id: a.capacity for a in graph.arcs}
        incoming: dict[NodeId, list[EffectiveArc]] = {}
        for arc in graph.arcs:
            incoming.setdefault(arc.to_node, []).append(arc)
        flows: dict[ArcId, float] = {}
        for node in sorted(remaining_need):
            while remaining_need[node] > EPS:
                found = _nearest_supplier(incoming, residual, remaining_supply, node)
                if found is None:
                    break
                origin, path = found
                tonnes = min(
                    remaining_need[node],
                    remaining_supply[origin],
                    min(residual[a.id] for a in path),
                )
                for arc in path:
                    residual[arc.id] -= tonnes
                    flows[arc.id] = flows.get(arc.id, 0.0) + tonnes
                remaining_supply[origin] -= tonnes
                remaining_need[node] -= tonnes
        return flows


def _nearest_supplier(
    incoming: Mapping[NodeId, list[EffectiveArc]],
    residual: Mapping[ArcId, float],
    supply: Mapping[NodeId, float],
    need_node: NodeId,
) -> tuple[NodeId, list[EffectiveArc]] | None:
    """Dijkstra backwards from the need node over arcs with residual capacity."""
    dist = {need_node: 0.0}
    via: dict[NodeId, EffectiveArc] = {}
    heap = [(0.0, need_node)]
    done: set[NodeId] = set()
    while heap:
        d, node = heapq.heappop(heap)
        if node in done:
            continue
        done.add(node)
        if node != need_node and supply.get(node, 0.0) > EPS:
            path: list[EffectiveArc] = []
            cur = node
            while cur != need_node:
                arc = via[cur]
                path.append(arc)
                cur = arc.to_node
            return node, path
        for arc in incoming.get(node, ()):
            if residual[arc.id] <= EPS:
                continue
            nd = d + arc.cost
            if arc.from_node not in dist or nd < dist[arc.from_node] - 1e-12:
                dist[arc.from_node] = nd
                via[arc.from_node] = arc
                heapq.heappush(heap, (nd, arc.from_node))
    return None


class MinCostFlowSolver:
    """Max flow of minimum cost per commodity via networkx on a scaled integer network.

    Tonnes are scaled by `TONNE_SCALE` and costs by `COST_SCALE`; capacities, supplies
    and needs are floored so the solution never exceeds the true residuals.
    """

    _SOURCE = ("super", "source")
    _SINK = ("super", "sink")

    def allocate(
        self,
        graph: EffectiveGraph,
        supplies: Mapping[NodeId, float],
        needs: Mapping[NodeId, float],
        commodity: Commodity,
    ) -> dict[ArcId, float]:
        """Return arc flows in tonnes; empty if nothing can move."""
        net_s, net_n, _ = net_supplies_needs(supplies, needs)
        g = nx.DiGraph()
        for node, tonnes in net_s.items():
            if scale_tonnes(tonnes) > 0:
                g.add_edge(self._SOURCE, node, capacity=scale_tonnes(tonnes), weight=0)
        for node, tonnes in net_n.items():
            if scale_tonnes(tonnes) > 0:
                g.add_edge(node, self._SINK, capacity=scale_tonnes(tonnes), weight=0)
        if self._SOURCE not in g or self._SINK not in g:
            return {}
        # Each arc goes through its own midpoint so parallel arcs stay distinct.
        for arc in graph.arcs:
            cap = scale_tonnes(arc.capacity)
            if cap <= 0:
                continue
            mid = ("arc", arc.id)
            g.add_edge(arc.from_node, mid, capacity=cap, weight=scale_cost(arc.cost))
            g.add_edge(mid, arc.to_node, capacity=cap, weight=0)
        flow = nx.max_flow_min_cost(g, self._SOURCE, self._SINK)
        result: dict[ArcId, float] = {}
        for arc in graph.arcs:
            units = flow.get(("arc", arc.id), {}).get(arc.to_node, 0)
            if units > 0:
                result[arc.id] = unscale_tonnes(units)
        return result


def scale_tonnes(tonnes: float) -> int:
    """Tonnes to integer solver units, rounding down."""
    return math.floor(tonnes * TONNE_SCALE + 1e-6)


def unscale_tonnes(units: int) -> float:
    """Integer solver units back to tonnes."""
    return units / TONNE_SCALE


def scale_cost(cost: float) -> int:
    """Cost to integer solver units, rounding to nearest."""
    return round(cost * COST_SCALE)


def decompose_flows(graph: EffectiveGraph, flows: Mapping[ArcId, float]) -> list[PathFlow]:
    """Split arc flows into origin-to-destination paths.

    Origins and destinations are the nodes with net outflow/inflow. Flow that only
    circulates (zero-cost cycles) is dropped.
    """
    arcs = {a.id: a for a in graph.arcs}
    left = {i: f for i, f in flows.items() if f > EPS}
    net: dict[NodeId, float] = {}
    for arc_id, tonnes in left.items():
        arc = arcs[arc_id]
        net[arc.from_node] = net.get(arc.from_node, 0.0) + tonnes
        net[arc.to_node] = net.get(arc.to_node, 0.0) - tonnes
    origins = {n: v for n, v in net.items() if v > EPS}
    sinks = {n: -v for n, v in net.items() if v < -EPS}
    out_arcs: dict[NodeId, list[EffectiveArc]] = {}
    for arc_id in sorted(left):
        out_arcs.setdefault(arcs[arc_id].from_node, []).append(arcs[arc_id])
    paths: list[PathFlow] = []
    for origin in sorted(origins):
        while origins[origin] > EPS:
            found = _find_path(out_arcs, left, origin, sinks)
            if found is None:
                break
            path, dest = found
            tonnes = min(origins[origin], sinks[dest], *(left[a.id] for a in path))
            for arc in path:
                left[arc.id] -= tonnes
            origins[origin] -= tonnes
            sinks[dest] -= tonnes
            paths.append(PathFlow(origin, dest, tuple(a.id for a in path), tonnes))
    return paths


def _find_path(
    out_arcs: Mapping[NodeId, list[EffectiveArc]],
    left: Mapping[ArcId, float],
    origin: NodeId,
    sinks: Mapping[NodeId, float],
) -> tuple[list[EffectiveArc], NodeId] | None:
    parent: dict[NodeId, EffectiveArc] = {}
    seen = {origin}
    queue = deque([origin])
    while queue:
        node = queue.popleft()
        if node != origin and sinks.get(node, 0.0) > EPS:
            path: list[EffectiveArc] = []
            cur = node
            while cur != origin:
                arc = parent[cur]
                path.append(arc)
                cur = arc.from_node
            path.reverse()
            return path, node
        for arc in out_arcs.get(node, ()):
            if left[arc.id] > EPS and arc.to_node not in seen:
                seen.add(arc.to_node)
                parent[arc.to_node] = arc
                queue.append(arc.to_node)
    return None
