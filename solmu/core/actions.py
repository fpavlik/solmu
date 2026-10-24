"""Interventions applied between ticks: reserve release, rented capacity, priority change."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Protocol

from solmu.core.model import CommodityId, EdgeId, NodeId, Policy
from solmu.core.state import CapacityBoost, Key, PriorityBoost


class ActionError(ValueError):
    """Raised when actions fail validation; `problems` lists every issue found."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class ActionContext:
    """Read-only facts an action needs to price and validate itself."""

    policy: Policy
    day: int
    budget: float
    region_ids: frozenset[NodeId]
    edge_ids: frozenset[EdgeId]
    commodity_ids: frozenset[CommodityId]
    strategic_stocks: Mapping[Key, float]
    holder_ids: frozenset[NodeId] = frozenset()


class WorkingState(Protocol):
    """The mutable tick state an action may change (satisfied by the engine's tick)."""

    day: int
    stocks: dict[Key, float]
    strategic: dict[Key, float]
    capacity_boosts: list[CapacityBoost]
    priority_boosts: list[PriorityBoost]
    holder_kind: Mapping[Key, str]
    release_offer: dict[Key, float]


class Action(Protocol):
    """An intervention: priced against the emergency budget, validated, then applied."""

    def cost(self, context: ActionContext) -> float:
        """Budget units this action costs."""

    def validate(self, context: ActionContext) -> list[str]:
        """Problems that make the action invalid (empty list if fine)."""

    def apply(self, working: WorkingState) -> None:
        """Change the working state of the tick in which the action takes effect."""


@dataclass(frozen=True)
class ReleaseReserve:
    """Move `tonnes` of strategic stock of a commodity held at a node into use.

    At a region or hub the tonnes join its usable stock; at a source node they are offered to
    the network that day (what is not shipped returns to the reserve). Cost 0; limited by the
    released part of the strategic stock on hand (`requires_processing` stock is not released
    by hand).
    """

    node_id: NodeId
    commodity_id: CommodityId
    tonnes: float

    def cost(self, context: ActionContext) -> float:
        """Releasing already-held stock is free."""
        return 0.0

    def validate(self, context: ActionContext) -> list[str]:
        """Holder node and commodity must exist, tonnes positive and within strategic stock."""
        problems = []
        if self.node_id not in context.region_ids | context.holder_ids:
            problems.append(f"ReleaseReserve: unknown holder node id {self.node_id!r}")
        if self.commodity_id not in context.commodity_ids:
            problems.append(f"ReleaseReserve: unknown commodity id {self.commodity_id!r}")
        if self.tonnes <= 0:
            problems.append(f"ReleaseReserve: tonnes must be > 0, got {self.tonnes}")
        if problems:
            return problems
        held = context.strategic_stocks.get((self.node_id, self.commodity_id))
        if held is None:
            problems.append(
                f"ReleaseReserve: no strategic stock of {self.commodity_id!r} at {self.node_id!r}"
            )
        elif self.tonnes > held + 1e-9:
            problems.append(
                f"ReleaseReserve: {self.tonnes} t of {self.commodity_id!r} at {self.node_id!r} "
                f"exceeds strategic stock {held:.3f} t"
            )
        return problems

    def apply(self, working: WorkingState) -> None:
        """Strategic stock decreases; the tonnes go to the holder's stock or its day's offer."""
        key = (self.node_id, self.commodity_id)
        working.strategic[key] -= self.tonnes
        if working.holder_kind.get(key, "region") == "node":
            working.release_offer[key] = working.release_offer.get(key, 0.0) + self.tonnes
        else:
            working.stocks[key] += self.tonnes


@dataclass(frozen=True)
class AddCapacity:
    """Rent extra capacity on an edge (per direction) for `days` days from the next tick.

    Cost = extra_tonnes_per_day x days x policy.capacity_rental_cost_per_tonne_day.
    """

    edge_id: EdgeId
    extra_tonnes_per_day: float
    days: int

    def cost(self, context: ActionContext) -> float:
        """Rental price of the extra tonne-days."""
        rate = context.policy.capacity_rental_cost_per_tonne_day
        return self.extra_tonnes_per_day * self.days * rate

    def validate(self, context: ActionContext) -> list[str]:
        """Edge must exist; extra capacity and days positive."""
        problems = []
        if self.edge_id not in context.edge_ids:
            problems.append(f"AddCapacity: unknown edge id {self.edge_id!r}")
        if self.extra_tonnes_per_day <= 0:
            problems.append(
                f"AddCapacity: extra_tonnes_per_day must be > 0, got {self.extra_tonnes_per_day}"
            )
        if self.days < 1:
            problems.append(f"AddCapacity: days must be >= 1, got {self.days}")
        return problems

    def apply(self, working: WorkingState) -> None:
        """Register a boost active on days [day, day + days - 1] of the applying tick."""
        working.capacity_boosts.append(
            CapacityBoost(
                self.edge_id, self.extra_tonnes_per_day, working.day, working.day + self.days - 1
            )
        )


@dataclass(frozen=True)
class PrioritizeCommodity:
    """Serve a commodity first in the solver for `days` days. Cost 0."""

    commodity_id: CommodityId
    days: int

    def cost(self, context: ActionContext) -> float:
        """Changing the order is free."""
        return 0.0

    def validate(self, context: ActionContext) -> list[str]:
        """Commodity must exist; days positive."""
        problems = []
        if self.commodity_id not in context.commodity_ids:
            problems.append(f"PrioritizeCommodity: unknown commodity id {self.commodity_id!r}")
        if self.days < 1:
            problems.append(f"PrioritizeCommodity: days must be >= 1, got {self.days}")
        return problems

    def apply(self, working: WorkingState) -> None:
        """Register a priority boost active on days [day, day + days - 1]."""
        working.priority_boosts.append(
            PriorityBoost(self.commodity_id, working.day + self.days - 1)
        )


def validate_actions(actions: Sequence[Action], context: ActionContext) -> list[str]:
    """All problems of a batch: per-action issues, total reserve release, total cost vs budget."""
    problems: list[str] = []
    released: dict[Key, float] = {}
    total_cost = 0.0
    for i, action in enumerate(actions):
        found = action.validate(context)
        problems.extend(f"action {i}: {p}" for p in found)
        if found:
            continue
        total_cost += action.cost(context)
        if isinstance(action, ReleaseReserve):
            key = (action.node_id, action.commodity_id)
            released[key] = released.get(key, 0.0) + action.tonnes
    for key, tonnes in released.items():
        held = context.strategic_stocks.get(key, 0.0)
        if tonnes > held + 1e-9:
            problems.append(
                f"total ReleaseReserve of {tonnes:.3f} t for {key} exceeds strategic stock "
                f"{held:.3f} t"
            )
    if total_cost > context.budget + 1e-9:
        problems.append(f"total cost {total_cost:.2f} exceeds budget {context.budget:.2f}")
    return problems


def action_record(action: Action, day: int, cost: float) -> dict:
    """Log entry for an applied action."""
    return {"day": day, "action": type(action).__name__, "cost": cost, "params": asdict(action)}  # type: ignore[call-overload]
