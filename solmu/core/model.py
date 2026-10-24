"""Typed, validated entities of the simulation model."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NodeId = str
EdgeId = str
CommodityId = str

Id = Annotated[str, Field(min_length=1)]
NonNegative = Annotated[float, Field(ge=0)]
Priority = Annotated[int, Field(gt=0)]

NodeKind = Literal["region", "port", "source", "hub"]
EdgeMode = Literal["road", "rail", "sea"]

REVERSE_SUFFIX = ":rev"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Commodity(_Model):
    """A transported good; lower `priority` is served first."""

    id: Id
    name: str
    unit: str = "t"
    priority: Priority
    perishable_days: Annotated[int, Field(gt=0)] | None = None
    per_capita_kg_per_day: NonNegative = 0.0
    via_hubs: bool = False
    hub_stock_days: NonNegative | None = None
    shortage_cost_per_tonne: NonNegative = 0.0


class Commodities(_Model):
    """Commodity catalogue plus the default retail buffer in days of demand."""

    commodities: list[Commodity]
    default_stock_days: NonNegative = 2.0
    hub_stock_days: NonNegative = 7.0
    import_redirect_delay_days: Annotated[int, Field(ge=0)] = 2
    import_backlog_loss_fraction_per_day: Annotated[float, Field(ge=0, le=1)] = 0.0

    @model_validator(mode="after")
    def _unique_ids(self) -> Commodities:
        ids = [c.id for c in self.commodities]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate commodity ids: {dupes}")
        return self

    def get(self, commodity_id: CommodityId) -> Commodity:
        """Return the commodity with this id or raise KeyError."""
        for c in self.commodities:
            if c.id == commodity_id:
                return c
        raise KeyError(f"unknown commodity id {commodity_id!r}")

    def by_priority(self) -> list[Commodity]:
        """Commodities ordered by priority, ties broken by id."""
        return sorted(self.commodities, key=lambda c: (c.priority, c.id))


class Node(_Model):
    """A location in the network."""

    id: Id
    name: str
    kind: NodeKind
    lat: Annotated[float, Field(ge=-90, le=90)]
    lon: Annotated[float, Field(ge=-180, le=180)]
    population: Annotated[int, Field(ge=0)] | None = None
    country: str = ""
    tags: dict[str, str] = Field(default_factory=dict)


class Edge(_Model):
    """A transport link; bidirectional edges expand into two directed arcs.

    `commodities` restricts the edge to those commodity ids (None = all).
    """

    id: Id
    from_node: Id
    to_node: Id
    mode: EdgeMode
    capacity_per_day: NonNegative
    transit_days: Annotated[int, Field(ge=1)]
    cost_per_tonne: NonNegative
    bidirectional: bool = True
    commodities: frozenset[str] | None = None


class Arc(_Model):
    """A directed arc derived from an edge; `edge_id` is the source edge."""

    id: Id
    edge_id: EdgeId
    from_node: NodeId
    to_node: NodeId
    mode: EdgeMode
    capacity_per_day: float
    transit_days: int
    cost_per_tonne: float
    commodities: frozenset[str] | None = None


class DemandRecord(_Model):
    """Daily demand of a commodity at a node, in tonnes."""

    node_id: Id
    commodity_id: Id
    tonnes_per_day: NonNegative


class SupplyRecord(_Model):
    """Daily supply (import capacity or production) of a commodity at a node.

    `max_tonnes_per_day` is the handling capacity of a port (default: `tonnes_per_day`, no
    headroom); the headroom above the baseline absorbs redirected imports.
    """

    node_id: Id
    commodity_id: Id
    tonnes_per_day: NonNegative
    max_tonnes_per_day: NonNegative | None = None

    @model_validator(mode="after")
    def _max_not_below_baseline(self) -> SupplyRecord:
        if self.max_tonnes_per_day is not None and self.max_tonnes_per_day < self.tonnes_per_day:
            raise ValueError(
                f"max_tonnes_per_day ({self.max_tonnes_per_day}) must be >= "
                f"tonnes_per_day ({self.tonnes_per_day})"
            )
        return self


class Network(_Model):
    """Nodes, edges, demand and supply, with id lookups and arc expansion."""

    nodes: list[Node]
    edges: list[Edge]
    demand: list[DemandRecord] = Field(default_factory=list)
    supply: list[SupplyRecord] = Field(default_factory=list)

    def node(self, node_id: NodeId) -> Node:
        """Return the node with this id or raise KeyError."""
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(f"unknown node id {node_id!r}")

    def edge(self, edge_id: EdgeId) -> Edge:
        """Return the edge with this id or raise KeyError."""
        for e in self.edges:
            if e.id == edge_id:
                return e
        raise KeyError(f"unknown edge id {edge_id!r}")

    def arcs(self) -> list[Arc]:
        """Directed arcs: edge `<id>` forward, plus `<id>:rev` if bidirectional."""
        result: list[Arc] = []
        for e in self.edges:
            fields = {
                "edge_id": e.id,
                "mode": e.mode,
                "capacity_per_day": e.capacity_per_day,
                "transit_days": e.transit_days,
                "cost_per_tonne": e.cost_per_tonne,
                "commodities": e.commodities,
            }
            result.append(Arc(id=e.id, from_node=e.from_node, to_node=e.to_node, **fields))
            if e.bidirectional:
                result.append(
                    Arc(
                        id=e.id + REVERSE_SUFFIX,
                        from_node=e.to_node,
                        to_node=e.from_node,
                        **fields,
                    )
                )
        return result


class ReleaseRule(_Model):
    """When and how fast strategic stock is released.

    `disruption_threshold` and `disruption_persistence_days` apply to `on_disruption` only.
    """

    trigger: Literal["shortage", "manual", "always", "on_disruption"]
    delay_days: Annotated[int, Field(ge=0)] = 0
    max_release_fraction_per_day: Annotated[float, Field(ge=0, le=1)] = 1.0
    disruption_threshold: Annotated[float, Field(ge=0, le=1)] = 0.3
    disruption_persistence_days: Annotated[int, Field(ge=1)] = 1


class StrategicHolder(_Model):
    """Who physically holds a `share` of a commodity's strategic stock.

    `region`: every region, in proportion to its demand (the v0 behaviour). `hub`: the hubs
    that hold the commodity, in proportion to served demand. `node`: one source node;
    `requires_processing` is the part of that stock (crude at a refinery) that can only be
    released while the node is running.
    """

    kind: Literal["region", "hub", "node"]
    share: Annotated[float, Field(gt=0, le=1)]
    node_id: NodeId | None = None
    requires_processing: Annotated[float, Field(ge=0, le=1)] = 0.0
    release: Literal["policy", "automatic"] = "policy"

    @model_validator(mode="after")
    def _node_fields(self) -> StrategicHolder:
        if self.kind == "node" and not self.node_id:
            raise ValueError("strategic holder of kind 'node' needs node_id")
        if self.kind != "node" and (self.node_id or self.requires_processing):
            raise ValueError("node_id and requires_processing apply to kind 'node' only")
        if self.release == "automatic" and (self.kind != "node" or self.requires_processing):
            raise ValueError("release 'automatic' is for kind 'node' without requires_processing")
        return self


class Policy(_Model):
    """A resilience policy profile."""

    id: Id
    name: str
    description: str = ""
    min_stock_days: dict[CommodityId, NonNegative] = Field(default_factory=dict)
    stock_holder: dict[CommodityId, str] = Field(default_factory=dict)
    release: ReleaseRule
    emergency_budget: NonNegative = 0.0
    holding_cost_per_tonne_day: dict[CommodityId, NonNegative] = Field(default_factory=dict)
    target_stock_days: dict[CommodityId, NonNegative] | None = None
    capacity_rental_cost_per_tonne_day: NonNegative = 10.0
    strategic_stock_at: dict[CommodityId, list[StrategicHolder]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _shares_sum_to_one(self) -> Policy:
        for cid, holders in self.strategic_stock_at.items():
            total = sum(h.share for h in holders)
            if abs(total - 1.0) > 1e-9:
                raise ValueError(f"strategic_stock_at[{cid!r}]: shares sum to {total}, not 1")
        return self


class Event(_Model):
    """Base of timed disruptions: multiplies a base value by `factor` on [day, until_day]."""

    day: Annotated[int, Field(ge=0)]
    until_day: Annotated[int, Field(ge=0)]
    factor: NonNegative

    @model_validator(mode="after")
    def _window(self) -> Event:
        if self.until_day < self.day:
            raise ValueError(f"until_day ({self.until_day}) must be >= day ({self.day})")
        return self


class NodeCapacityEvent(Event):
    """Scales the capacity of a node (e.g. a port closure).

    With `commodity_id` only that commodity's throughput is scaled (None = all commodities).
    """

    type: Literal["node_capacity"]
    node_id: Id
    commodity_id: Id | None = None


class EdgeCapacityEvent(Event):
    """Scales the capacity of an edge."""

    type: Literal["edge_capacity"]
    edge_id: Id


class DemandEvent(Event):
    """Scales demand of a commodity at a node (`"*"` = every region)."""

    type: Literal["demand"]
    node_id: Id
    commodity_id: Id


class StockTargetEvent(Event):
    """Scales the order-up-to target of regions and hubs (`"*"` = all of them).

    `commodity_id` None = all commodities. Initial stocks are not changed.
    """

    type: Literal["stock_target"]
    node_id: Id
    commodity_id: Id | None = None


ScenarioEvent = Annotated[
    NodeCapacityEvent | EdgeCapacityEvent | DemandEvent | StockTargetEvent,
    Field(discriminator="type"),
]


class Scenario(_Model):
    """A named set of timed events over a horizon."""

    id: Id
    name: str
    description: str = ""
    horizon_days: Annotated[int, Field(gt=0)]
    demand_noise_fraction: Annotated[float, Field(ge=0, lt=1)] = 0.0
    events: list[ScenarioEvent] = Field(default_factory=list)
