"""Immutable simulation state snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TypeVar

from solmu.core.model import CommodityId, EdgeId, NodeId

Key = tuple[NodeId, CommodityId]
K = TypeVar("K")
V = TypeVar("V")


def freeze(mapping: Mapping[K, V]) -> Mapping[K, V]:
    """Read-only copy of a mapping; later changes to the source do not show through."""
    return MappingProxyType(dict(mapping))


@dataclass(frozen=True)
class Shipment:
    """Tonnes of one commodity travelling along `path` (arc ids) to `to_node`."""

    commodity_id: CommodityId
    from_node: NodeId
    to_node: NodeId
    path: tuple[str, ...]
    tonnes: float
    dispatch_day: int
    arrival_day: int


@dataclass(frozen=True)
class CapacityBoost:
    """Extra tonnes/day on an edge (per direction) for days `first_day..until_day` inclusive."""

    edge_id: EdgeId
    extra_tonnes_per_day: float
    first_day: int
    until_day: int


@dataclass(frozen=True)
class PriorityBoost:
    """A commodity moved to the front of the solver order through `until_day` inclusive."""

    commodity_id: CommodityId
    until_day: int


@dataclass(frozen=True)
class PendingImport:
    """Diverted import tonnes of a commodity that become available to other ports on `day`."""

    commodity_id: CommodityId
    day: int
    tonnes: float


@dataclass(frozen=True)
class State:
    """Snapshot after `day` completed ticks.

    `stocks` and `strategic_stocks` are end-of-day tonnes; `target_stocks` is the order-up-to
    level (target days x effective demand) used on the last simulated day, in tonnes.
    `release_started` maps (region, commodity) to the day the shortage release clock started.
    `unmet`, `effective_*` and `edge_flows` describe the last simulated day (day - 1);
    they are neutral on the initial state.
    `stocks` also holds hub stock for the commodities a hub handles. `import_backlog` is the
    diverted import tonnes still waiting per commodity (`import_ready` available now plus
    `import_pending` still in the redirect delay); `import_lost` is the cumulative tonnes lost
    from the backlog.
    `import_drop` is the fraction by which the national import offer (ports and sources after
    node factors, before redirection) fell short of baseline on the last simulated day; the
    `on_disruption` release keeps `disruption_streak` (consecutive days above threshold) and
    `disruption_started` (day the shared per-commodity countdown began) per commodity.
    `strategic_stocks` is keyed by whichever node holds the stock (region, hub or source node,
    see `Policy.strategic_stock_at`); `strategic_unprocessed` is the part held at a source
    node that needs the node to run before it can be released (crude at a refinery);
    `automatic_stocks` is the part at a source node that is drawn as needed without a release
    decision (companies' obligatory stock in their operating inventory).
    """

    day: int
    stocks: Mapping[Key, float]
    strategic_stocks: Mapping[Key, float]
    in_transit: tuple[Shipment, ...]
    unmet: Mapping[Key, float]
    effective_node_factor: Mapping[NodeId, float]
    effective_edge_factor: Mapping[EdgeId, float]
    effective_demand: Mapping[Key, float]
    edge_flows: Mapping[EdgeId, float]
    budget: float
    cumulative_unmet: Mapping[CommodityId, float]
    holding_cost: float
    action_spend: float
    target_stocks: Mapping[Key, float]
    release_started: Mapping[Key, int]
    capacity_boosts: tuple[CapacityBoost, ...]
    priority_boosts: tuple[PriorityBoost, ...]
    import_backlog: Mapping[CommodityId, float] = field(default_factory=lambda: freeze({}))
    import_ready: Mapping[CommodityId, float] = field(default_factory=lambda: freeze({}))
    import_pending: tuple[PendingImport, ...] = ()
    import_lost: Mapping[CommodityId, float] = field(default_factory=lambda: freeze({}))
    import_drop: Mapping[CommodityId, float] = field(default_factory=lambda: freeze({}))
    disruption_streak: Mapping[CommodityId, int] = field(default_factory=lambda: freeze({}))
    disruption_started: Mapping[CommodityId, int] = field(default_factory=lambda: freeze({}))
    strategic_unprocessed: Mapping[Key, float] = field(default_factory=lambda: freeze({}))
    automatic_stocks: Mapping[Key, float] = field(default_factory=lambda: freeze({}))
