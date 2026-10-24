"""Scenario events: validation against a network and per-day capacity/demand modifiers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from solmu.core.model import (
    CommodityId,
    DemandEvent,
    EdgeCapacityEvent,
    EdgeId,
    NodeCapacityEvent,
    NodeId,
    Scenario,
    StockTargetEvent,
)

Key = tuple[NodeId, CommodityId]
WILDCARD = "*"


@dataclass
class Modifiers:
    """Multiplicative factors active on one day; absent targets have factor 1."""

    node_factor: dict[NodeId, float] = field(default_factory=dict)
    edge_factor: dict[EdgeId, float] = field(default_factory=dict)
    demand_factor: dict[Key, float] = field(default_factory=dict)
    node_commodity_factor: dict[Key, float] = field(default_factory=dict)
    target_factor: dict[tuple[NodeId, CommodityId | None], float] = field(default_factory=dict)
    region_ids: tuple[NodeId, ...] = ()
    hub_ids: tuple[NodeId, ...] = ()


@dataclass(frozen=True)
class NetworkIds:
    """Ids a scenario may refer to."""

    node_ids: frozenset[NodeId]
    region_ids: frozenset[NodeId]
    edge_ids: frozenset[EdgeId]
    commodity_ids: frozenset[CommodityId]
    hub_ids: frozenset[NodeId] = frozenset()


def _multiply(factors: dict, key: Any, factor: float) -> None:
    factors[key] = factors.get(key, 1.0) * factor


class NodeCapacityHandler:
    """`node_capacity`: scales the supply offered at a port or source node."""

    def problems(self, event: NodeCapacityEvent, ids: NetworkIds) -> list[str]:
        """Unknown target ids."""
        found = [] if event.node_id in ids.node_ids else [f"unknown node id {event.node_id!r}"]
        if event.commodity_id is not None and event.commodity_id not in ids.commodity_ids:
            found.append(f"unknown commodity id {event.commodity_id!r}")
        return found

    def apply(self, event: NodeCapacityEvent, mods: Modifiers) -> None:
        """Multiply the node factor, or the per-commodity node factor if `commodity_id` is set."""
        if event.commodity_id is None:
            _multiply(mods.node_factor, event.node_id, event.factor)
        else:
            _multiply(mods.node_commodity_factor, (event.node_id, event.commodity_id), event.factor)


class EdgeCapacityHandler:
    """`edge_capacity`: scales the capacity of both directions of an edge."""

    def problems(self, event: EdgeCapacityEvent, ids: NetworkIds) -> list[str]:
        """Unknown target ids."""
        return [] if event.edge_id in ids.edge_ids else [f"unknown edge id {event.edge_id!r}"]

    def apply(self, event: EdgeCapacityEvent, mods: Modifiers) -> None:
        """Multiply the edge factor."""
        _multiply(mods.edge_factor, event.edge_id, event.factor)


class DemandHandler:
    """`demand`: scales a region's demand for one commodity."""

    def problems(self, event: DemandEvent, ids: NetworkIds) -> list[str]:
        """Unknown target ids; demand exists only at regions."""
        found = []
        if event.node_id != WILDCARD and event.node_id not in ids.region_ids:
            found.append(f"demand event target {event.node_id!r} is not a region node")
        if event.commodity_id not in ids.commodity_ids:
            found.append(f"unknown commodity id {event.commodity_id!r}")
        return found

    def apply(self, event: DemandEvent, mods: Modifiers) -> None:
        """Multiply the demand factor."""
        targets = mods.region_ids if event.node_id == WILDCARD else [event.node_id]
        for node_id in targets:
            _multiply(mods.demand_factor, (node_id, event.commodity_id), event.factor)


class StockTargetHandler:
    """`stock_target`: scales the order-up-to target of regions and hubs."""

    def problems(self, event: StockTargetEvent, ids: NetworkIds) -> list[str]:
        """Unknown target ids; targets exist only at regions and hubs."""
        found = []
        if event.node_id != WILDCARD and event.node_id not in ids.region_ids | ids.hub_ids:
            found.append(f"stock_target event target {event.node_id!r} is not a region or hub node")
        if event.commodity_id is not None and event.commodity_id not in ids.commodity_ids:
            found.append(f"unknown commodity id {event.commodity_id!r}")
        return found

    def apply(self, event: StockTargetEvent, mods: Modifiers) -> None:
        """Multiply the target factor of the matching nodes."""
        targets = mods.region_ids + mods.hub_ids if event.node_id == WILDCARD else [event.node_id]
        for node_id in targets:
            _multiply(mods.target_factor, (node_id, event.commodity_id), event.factor)


HANDLERS: dict[str, Any] = {
    "node_capacity": NodeCapacityHandler(),
    "edge_capacity": EdgeCapacityHandler(),
    "demand": DemandHandler(),
    "stock_target": StockTargetHandler(),
}


def validate_scenario(scenario: Scenario, ids: NetworkIds) -> None:
    """Raise ValueError naming scenario id, event index and target for every bad event."""
    messages = []
    for i, event in enumerate(scenario.events):
        for problem in HANDLERS[event.type].problems(event, ids):
            messages.append(f"scenario {scenario.id!r}, event {i} ({event.type}): {problem}")
    if messages:
        raise ValueError("; ".join(messages))


def active_modifiers(
    scenario: Scenario,
    day: int,
    region_ids: Iterable[NodeId] = (),
    hub_ids: Iterable[NodeId] = (),
) -> Modifiers:
    """Factors of all events whose inclusive window [day, until_day] contains `day`.

    `region_ids` is what a demand event with node_id `"*"` expands to; a `stock_target`
    event with `"*"` expands to `region_ids` plus `hub_ids`.
    """
    mods = Modifiers(region_ids=tuple(region_ids), hub_ids=tuple(hub_ids))
    for event in scenario.events:
        if event.day <= day <= event.until_day:
            HANDLERS[event.type].apply(event, mods)
    return mods
