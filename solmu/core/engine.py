"""The simulation loop: one `Simulation` steps a network through a scenario day by day."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import pandas as pd

from solmu import __version__
from solmu.core.actions import (
    Action,
    ActionContext,
    ActionError,
    action_record,
    validate_actions,
)
from solmu.core.model import (
    REVERSE_SUFFIX,
    Commodities,
    Commodity,
    CommodityId,
    EdgeId,
    Network,
    NodeId,
    Policy,
    Scenario,
    StrategicHolder,
)
from solmu.core.policy import disruption_step, release_step
from solmu.core.redirect import redirect_step
from solmu.core.results import (
    DAILY_COLUMNS,
    EDGES_COLUMNS,
    IMPORTS_COLUMNS,
    RunResult,
    summarize,
)
from solmu.core.scenario import NetworkIds, active_modifiers, validate_scenario
from solmu.core.solver import (
    ArcId,
    EffectiveArc,
    EffectiveGraph,
    FlowSolver,
    MinCostFlowSolver,
    PathFlow,
    decompose_flows,
    net_supplies_needs,
)
from solmu.core.state import (
    CapacityBoost,
    Key,
    PendingImport,
    PriorityBoost,
    Shipment,
    State,
    freeze,
)

UNMET_EPS = 1e-9  # tonnes; shortfalls below this are floating-point dust, not shortage


@dataclass
class _Tick:
    """Mutable working copy of the state while one day is being simulated."""

    day: int
    stocks: dict[Key, float]
    strategic: dict[Key, float]
    in_transit: list[Shipment]
    node_factor: dict[NodeId, float]
    edge_factor: dict[EdgeId, float]
    demand: dict[Key, float]
    budget: float
    action_spend: float
    release_started: dict[Key, int]
    capacity_boosts: list[CapacityBoost]
    priority_boosts: list[PriorityBoost]
    prev_unmet: dict[Key, float]
    import_ready: dict[CommodityId, float]
    import_pending: list[PendingImport]
    import_lost: dict[CommodityId, float]
    disruption_streak: dict[CommodityId, int]
    disruption_started: dict[CommodityId, int]
    strategic_crude: dict[Key, float]
    strategic_auto: dict[Key, float]
    holder_kind: Mapping[Key, str]
    release_offer: dict[Key, float] = field(default_factory=dict)
    auto_offer: dict[Key, float] = field(default_factory=dict)
    import_drop: dict[CommodityId, float] = field(default_factory=dict)
    import_offer: dict[CommodityId, float] = field(default_factory=dict)
    import_day_lost: dict[CommodityId, float] = field(default_factory=dict)
    disruption_releasing: dict[CommodityId, bool] = field(default_factory=dict)
    node_commodity_factor: dict[Key, float] = field(default_factory=dict)
    target_factor: dict[tuple[NodeId, CommodityId | None], float] = field(default_factory=dict)
    target: dict[Key, float] = field(default_factory=dict)
    unmet: dict[Key, float] = field(default_factory=dict)
    consumed: dict[Key, float] = field(default_factory=dict)
    inbound: dict[Key, float] = field(default_factory=dict)
    outbound: dict[Key, float] = field(default_factory=dict)
    plans: list[tuple[CommodityId, PathFlow]] = field(default_factory=list)
    local: dict[Key, float] = field(default_factory=dict)
    arc_flows: dict[ArcId, float] = field(default_factory=dict)


def _lead_time(
    graph: nx.DiGraph, suppliers: dict[NodeId, float], region: NodeId, demand: float
) -> int:
    """Transit days from the nearest suppliers that together offer at least `demand`.

    Suppliers are taken in order of shortest-path transit days to `region` until their
    summed supply covers the region's demand; the lead time is the distance of the last one
    needed (the plain nearest supplier when it alone is large enough). 0 if unreachable.
    """
    dist = nx.single_source_dijkstra_path_length(graph.reverse(copy=False), region, weight="days")
    reachable = sorted((dist[n], n, t) for n, t in suppliers.items() if n in dist)
    covered = 0.0
    for days, _, tonnes in reachable:
        covered += tonnes
        if covered >= demand:
            return days
    return reachable[-1][0] if reachable else 0


class Simulation:
    """Daily-tick simulation of one network under one scenario and policy."""

    def __init__(
        self,
        network: Network,
        scenario: Scenario,
        policy: Policy,
        commodities: Commodities,
        seed: int = 0,
        solver: FlowSolver | None = None,
    ) -> None:
        self.network = network
        self.scenario = scenario
        self.policy = policy
        self.commodities = commodities
        self.seed = seed
        self.solver: FlowSolver = solver if solver is not None else MinCostFlowSolver()
        self._arcs = network.arcs()
        self._arc_commodities = {a.id: a.commodities for a in self._arcs}
        self._regions = [n.id for n in network.nodes if n.kind == "region"]
        self._hubs = [n.id for n in network.nodes if n.kind == "hub"]
        self._commodity_ids = [c.id for c in commodities.commodities]
        self._base_demand = self._index_records("demand", network.demand, regions_only=True)
        self._supply = self._index_records("supply", network.supply, regions_only=False)
        self._supply_max = {
            (r.node_id, r.commodity_id): r.max_tonnes_per_day
            for r in network.supply
            if r.max_tonnes_per_day is not None
        }
        for cid in [
            *policy.min_stock_days,
            *policy.holding_cost_per_tonne_day,
            *(policy.target_stock_days or {}),
            *policy.strategic_stock_at,
        ]:
            self._check_commodity(cid, f"policy {policy.id!r}")
        validate_scenario(
            scenario,
            NetworkIds(
                node_ids=frozenset(n.id for n in network.nodes),
                region_ids=frozenset(self._regions),
                edge_ids=frozenset(e.id for e in network.edges),
                commodity_ids=frozenset(self._commodity_ids),
                hub_ids=frozenset(n.id for n in network.nodes if n.kind == "hub"),
            ),
        )
        self._node_kind = {n.id: n.kind for n in network.nodes}
        self._via_hubs = [c.id for c in commodities.by_priority() if c.via_hubs]
        self._hub_holds = self._index_hub_holds()
        self._check_edge_commodities()
        self._graphs = {c: self._base_graphs(c) for c in self._commodity_ids}
        self._catchments = self._compute_catchments()
        self._served = self._compute_served()
        self._lead_time = self._compute_lead_times()
        self._hub_lead = self._compute_hub_lead_times()
        self._target_days = self._compute_target_days()
        self._hub_target = self._compute_hub_targets()
        self._holders = self._index_holders()
        self.reset()

    @property
    def lead_times(self) -> dict[Key, int]:
        """Shortest supply-to-region transit days per (region, commodity); 0 if unreachable."""
        return dict(self._lead_time)

    @property
    def catchments(self) -> dict[CommodityId, dict[NodeId, NodeId]]:
        """Hub serving each region, per via-hub commodity (unreachable regions are absent)."""
        return {c: dict(m) for c, m in self._catchments.items()}

    def _index_hub_holds(self) -> dict[CommodityId, list[NodeId]]:
        holds: dict[CommodityId, list[NodeId]] = {c: [] for c in self._via_hubs}
        for node in self.network.nodes:
            raw = node.tags.get("holds", "")
            if node.kind != "hub" or not raw:
                continue
            for cid in filter(None, (p.strip() for p in raw.split("|"))):
                self._check_commodity(cid, f"node {node.id!r}, tag 'holds'")
                if cid in holds:
                    holds[cid].append(node.id)
        for cid, hubs in holds.items():
            if not hubs:
                raise ValueError(f"commodity {cid!r} is via_hubs but no hub node holds it")
        return holds

    def _check_edge_commodities(self) -> None:
        for edge in self.network.edges:
            for cid in sorted(edge.commodities or ()):
                self._check_commodity(cid, f"edge {edge.id!r}, field 'commodities'")

    def _index_holders(self) -> dict[CommodityId, list[StrategicHolder]]:
        """Who holds each commodity's strategic stock; all at regions unless the policy says."""
        where = f"policy {self.policy.id!r}, strategic_stock_at"
        holders: dict[CommodityId, list[StrategicHolder]] = {}
        for cid in self._commodity_ids:
            spec = self.policy.strategic_stock_at.get(cid)
            if spec is None:
                holders[cid] = [StrategicHolder(kind="region", share=1.0)]
                continue
            for h in spec:
                if h.kind == "hub" and cid not in self._hub_holds:
                    raise ValueError(
                        f"{where}[{cid!r}]: 'hub' needs a via_hubs commodity with hubs"
                    )
                if h.kind == "node" and self._node_kind.get(h.node_id or "") != "source":
                    raise ValueError(f"{where}[{cid!r}]: node {h.node_id!r} is not a source node")
            holders[cid] = list(spec)
        return holders

    def _initial_strategic(self) -> tuple[dict[Key, float], dict[Key, float], dict[Key, float]]:
        """Strategic stock per holder key: (releasable, needs-processing, automatic) tonnes."""
        product: dict[Key, float] = {
            (n, c): 0.0 for n in self._regions for c in self._commodity_ids
        }
        crude: dict[Key, float] = {}
        auto: dict[Key, float] = {}
        for cid, holders in self._holders.items():
            days = self.policy.min_stock_days.get(cid, 0.0)
            national = sum(self._base_demand.get((n, cid), 0.0) for n in self._regions)
            for h in holders:
                if h.kind == "region":
                    for n in self._regions:
                        product[(n, cid)] += h.share * days * self._base_demand.get((n, cid), 0.0)
                elif h.kind == "hub":
                    for hub in self._hub_holds[cid]:
                        key = (hub, cid)
                        product[key] = product.get(key, 0.0) + h.share * days * self._served[key]
                else:
                    key = (h.node_id or "", cid)
                    total = h.share * days * national
                    if h.release == "automatic":
                        auto[key] = auto.get(key, 0.0) + total
                        product.setdefault(key, 0.0)
                        continue
                    product[key] = product.get(key, 0.0) + total * (1 - h.requires_processing)
                    crude[key] = crude.get(key, 0.0) + total * h.requires_processing
        return product, crude, auto

    def _base_graphs(self, cid: CommodityId) -> tuple[nx.DiGraph, nx.DiGraph]:
        """Transit-days and cost graphs of the arcs that may carry `cid` (base capacities)."""
        days = nx.DiGraph()
        cost = nx.DiGraph()
        days.add_nodes_from(n.id for n in self.network.nodes)
        cost.add_nodes_from(n.id for n in self.network.nodes)
        for arc in self._arcs:
            if arc.commodities is not None and cid not in arc.commodities:
                continue
            a, b = arc.from_node, arc.to_node
            if not days.has_edge(a, b) or arc.transit_days < days[a][b]["days"]:
                days.add_edge(a, b, days=arc.transit_days)
            weight = arc.cost_per_tonne * arc.transit_days
            if not cost.has_edge(a, b) or weight < cost[a][b]["cost"]:
                cost.add_edge(a, b, cost=weight)
        return days, cost

    def _compute_catchments(self) -> dict[CommodityId, dict[NodeId, NodeId]]:
        catchments: dict[CommodityId, dict[NodeId, NodeId]] = {}
        for cid, hubs in self._hub_holds.items():
            cost_graph = self._graphs[cid][1]
            dist = {
                h: nx.single_source_dijkstra_path_length(cost_graph, h, weight="cost")
                for h in hubs
                if h in cost_graph
            }
            mapping: dict[NodeId, NodeId] = {}
            for region in self._regions:
                options = sorted((d[region], h) for h, d in dist.items() if region in d)
                if options:
                    mapping[region] = options[0][1]
            catchments[cid] = mapping
        return catchments

    def _compute_served(self) -> dict[Key, float]:
        served: dict[Key, float] = {
            (h, cid): 0.0 for cid, hubs in self._hub_holds.items() for h in hubs
        }
        for cid, mapping in self._catchments.items():
            for region, hub in mapping.items():
                served[(hub, cid)] += self._base_demand.get((region, cid), 0.0)
        return served

    def _compute_lead_times(self) -> dict[Key, int]:
        lead: dict[Key, int] = {}
        for cid in self._commodity_ids:
            graph = self._graphs[cid][0]
            suppliers = {n: t for (n, c), t in self._supply.items() if c == cid and t > 0}
            for region in self._regions:
                here = suppliers
                if cid in self._via_hubs:
                    hub = self._catchments[cid].get(region)
                    here = {hub: float("inf")} if hub else {}
                lead[(region, cid)] = _lead_time(
                    graph, here, region, self._base_demand.get((region, cid), 0.0)
                )
        return lead

    def _compute_hub_lead_times(self) -> dict[Key, int]:
        lead: dict[Key, int] = {}
        for (hub, cid), served in self._served.items():
            graph = self._graphs[cid][0]
            suppliers = {n: t for (n, c), t in self._supply.items() if c == cid and t > 0}
            lead[(hub, cid)] = _lead_time(graph, suppliers, hub, served)
        return lead

    def _compute_hub_targets(self) -> dict[Key, float]:
        targets: dict[Key, float] = {}
        for (hub, cid), served in self._served.items():
            own = self.commodities.get(cid).hub_stock_days
            days = self.commodities.hub_stock_days if own is None else own
            targets[(hub, cid)] = (days + self._hub_lead[(hub, cid)]) * served
        return targets

    def _compute_target_days(self) -> dict[Key, float]:
        cover = self.policy.target_stock_days or {}
        default = self.commodities.default_stock_days
        return {k: cover.get(k[1], default) + lt for k, lt in self._lead_time.items()}

    def _check_commodity(self, commodity_id: str, where: str) -> None:
        if commodity_id not in self._commodity_ids:
            raise ValueError(f"{where}: unknown commodity id {commodity_id!r}")

    def _index_records(self, what: str, records: list, regions_only: bool) -> dict[Key, float]:
        node_kinds = {n.id: n.kind for n in self.network.nodes}
        index: dict[Key, float] = {}
        for rec in records:
            self._check_commodity(rec.commodity_id, f"{what} record for node {rec.node_id!r}")
            if regions_only and node_kinds.get(rec.node_id) != "region":
                raise ValueError(
                    f"{what} record for node {rec.node_id!r}: only region nodes hold stock "
                    "and have demand"
                )
            index[(rec.node_id, rec.commodity_id)] = rec.tonnes_per_day
        return index

    def reset(self) -> State:
        """Restore the initial state (day 0) and reseed the random generator."""
        self.rng = np.random.default_rng(self.seed)
        self._queued: list[Action] = []
        self._daily_rows: list[tuple] = []
        self._edge_rows: list[tuple] = []
        self._import_rows: list[tuple] = []
        self._actions_log: list[dict] = []
        keys = [(n, c) for n in self._regions for c in self._commodity_ids]
        target = {k: self._target_days[k] * self._base_demand.get(k, 0.0) for k in keys}
        stocks = {**target, **self._hub_target}
        strategic, crude, auto = self._initial_strategic()
        kinds = {"region": "region", "hub": "hub", "source": "node"}
        self._holder_kind = {k: kinds[self._node_kind[k[0]]] for k in {*strategic, *crude, *auto}}
        self._state = State(
            day=0,
            stocks=freeze(stocks),
            strategic_stocks=freeze(strategic),
            in_transit=(),
            unmet=freeze({k: 0.0 for k in keys}),
            effective_node_factor=freeze({n.id: 1.0 for n in self.network.nodes}),
            effective_edge_factor=freeze({e.id: 1.0 for e in self.network.edges}),
            effective_demand=freeze({k: self._base_demand.get(k, 0.0) for k in keys}),
            edge_flows=freeze({e.id: 0.0 for e in self.network.edges}),
            budget=self.policy.emergency_budget,
            cumulative_unmet=freeze({c: 0.0 for c in self._commodity_ids}),
            holding_cost=0.0,
            action_spend=0.0,
            target_stocks=freeze(stocks),
            release_started=freeze({}),
            capacity_boosts=(),
            priority_boosts=(),
            import_backlog=freeze({c: 0.0 for c in self._commodity_ids}),
            import_ready=freeze({c: 0.0 for c in self._commodity_ids}),
            import_lost=freeze({c: 0.0 for c in self._commodity_ids}),
            import_drop=freeze({c: 0.0 for c in self._commodity_ids}),
            strategic_unprocessed=freeze(crude),
            automatic_stocks=freeze(auto),
        )
        self._backlog_peak = {c: 0.0 for c in self._commodity_ids}
        return self._state

    def apply(self, actions: list[Action]) -> None:
        """Validate all actions, then queue them for the start of the next `step()`.

        Raises `ActionError` listing every problem; nothing is queued in that case.
        """
        problems = validate_actions([*self._queued, *actions], self._action_context())
        if problems:
            raise ActionError(problems)
        self._queued.extend(actions)

    def action_cost(self, action: Action) -> float:
        """Budget units `action` would cost if applied now (to show a price before `apply`)."""
        return action.cost(self._action_context())

    def _action_context(self) -> ActionContext:
        return self._context_for(self.day, self._state.budget, self._state.strategic_stocks)

    def _context_for(
        self, day: int, budget: float, strategic: Mapping[Key, float]
    ) -> ActionContext:
        return ActionContext(
            policy=self.policy,
            day=day,
            budget=budget,
            region_ids=frozenset(self._regions),
            edge_ids=frozenset(e.id for e in self.network.edges),
            commodity_ids=frozenset(self._commodity_ids),
            strategic_stocks=strategic,
            holder_ids=frozenset(k[0] for k in strategic),
        )

    @property
    def day(self) -> int:
        """Number of days simulated so far; the next `step()` simulates this day."""
        return self._state.day

    @property
    def done(self) -> bool:
        """True once the scenario horizon has been simulated."""
        return self.day >= self.scenario.horizon_days

    @property
    def state(self) -> State:
        """Current immutable snapshot."""
        return self._state

    def step(self) -> State:
        """Simulate one day and return the new state."""
        if self.done:
            raise RuntimeError(f"simulation is done (horizon {self.scenario.horizon_days} days)")
        tick = self._begin_tick()
        self._apply_events(tick)
        self._import_drop(tick)
        self._apply_actions(tick)
        self._arrivals(tick)
        self._policy_step(tick)
        self._consume(tick)
        self._allocate(tick)
        self._dispatch(tick)
        self._state = self._snapshot(tick)
        return self._state

    def run(self, until: int | None = None) -> RunResult:
        """Step until `until` days have been simulated (default: the horizon)."""
        target = self.scenario.horizon_days if until is None else until
        if target > self.scenario.horizon_days:
            raise ValueError(f"until={target} exceeds horizon {self.scenario.horizon_days}")
        while self.day < target:
            self.step()
        return self.result()

    def result(self) -> RunResult:
        """Result tables and summary for the days simulated so far."""
        daily = pd.DataFrame(self._daily_rows, columns=DAILY_COLUMNS)
        edges = pd.DataFrame(self._edge_rows, columns=EDGES_COLUMNS)
        populations = {n.id: n.population or 0 for n in self.network.nodes}
        summary = summarize(
            daily,
            edges,
            populations,
            self._state.holding_cost,
            self._state.action_spend,
            self._backlog_peak,
            self._state.import_lost,
            {c.id: c.shortage_cost_per_tonne for c in self.commodities.commodities},
        )
        meta = {
            "scenario_id": self.scenario.id,
            "policy_id": self.policy.id,
            "seed": self.seed,
            "horizon_days": self.scenario.horizon_days,
            "days_simulated": self.day,
            "solver": type(self.solver).__name__,
            "code_version": __version__,
        }
        imports = pd.DataFrame(self._import_rows, columns=IMPORTS_COLUMNS)
        return RunResult(meta, daily, edges, summary, list(self._actions_log), imports)

    def _begin_tick(self) -> _Tick:
        s = self._state
        return _Tick(
            day=s.day,
            stocks=dict(s.stocks),
            strategic=dict(s.strategic_stocks),
            in_transit=list(s.in_transit),
            node_factor=dict(s.effective_node_factor),
            edge_factor=dict(s.effective_edge_factor),
            demand=dict(s.effective_demand),
            budget=s.budget,
            action_spend=s.action_spend,
            release_started=dict(s.release_started),
            capacity_boosts=[b for b in s.capacity_boosts if b.until_day >= s.day],
            priority_boosts=[b for b in s.priority_boosts if b.until_day >= s.day],
            prev_unmet=dict(s.unmet),
            import_ready=dict(s.import_ready),
            import_pending=list(s.import_pending),
            import_lost=dict(s.import_lost),
            disruption_streak=dict(s.disruption_streak),
            disruption_started=dict(s.disruption_started),
            strategic_crude=dict(s.strategic_unprocessed),
            strategic_auto=dict(s.automatic_stocks),
            holder_kind=self._holder_kind,
        )

    def _apply_events(self, tick: _Tick) -> None:
        mods = active_modifiers(self.scenario, tick.day, self._regions, self._hubs)
        tick.node_commodity_factor = mods.node_commodity_factor
        tick.target_factor = mods.target_factor
        tick.node_factor = {n.id: mods.node_factor.get(n.id, 1.0) for n in self.network.nodes}
        tick.edge_factor = {e.id: mods.edge_factor.get(e.id, 1.0) for e in self.network.edges}
        noise = self.scenario.demand_noise_fraction
        demand = {}
        for key in sorted(tick.demand):
            value = self._base_demand.get(key, 0.0) * mods.demand_factor.get(key, 1.0)
            if noise > 0:
                value *= 1.0 + self.rng.uniform(-noise, noise)
            demand[key] = value
        tick.demand = demand

    def _import_drop(self, tick: _Tick) -> None:
        """National import drop per commodity and, for `on_disruption`, the release clock."""
        rule = self.policy.release
        for cid in self._commodity_ids:
            baseline = sum(t for (_, c), t in self._supply.items() if c == cid)
            offer = sum(self._direct_offer(tick, n, cid) for (n, c) in self._supply if c == cid)
            drop = max(0.0, 1.0 - offer / baseline) if baseline > 0 else 0.0
            tick.import_drop[cid] = drop
            tick.import_offer[cid] = offer
            if rule.trigger != "on_disruption":
                continue
            unmet = any(v > 0 for (_, c), v in tick.prev_unmet.items() if c == cid)
            streak, started, releasing = disruption_step(
                rule,
                tick.day,
                drop,
                tick.disruption_streak.get(cid, 0),
                tick.disruption_started.get(cid),
                unmet,
            )
            tick.disruption_streak[cid] = streak
            if started is not None:
                tick.disruption_started[cid] = started
            tick.disruption_releasing[cid] = releasing

    def _direct_offer(self, tick: _Tick, node: NodeId, cid: CommodityId) -> float:
        """Offer of a port or source after its node factor, before redirection."""
        baseline = self._supply[(node, cid)]
        factor = self._node_factor(tick, node, cid)
        if self._node_kind[node] == "port":
            return min(baseline * factor, (self._supply_max.get((node, cid)) or baseline) * factor)
        return baseline * factor

    @staticmethod
    def _node_factor(tick: _Tick, node: NodeId, cid: CommodityId) -> float:
        """Node factor times the per-commodity factor of `node_capacity` events."""
        return tick.node_factor[node] * tick.node_commodity_factor.get((node, cid), 1.0)

    @staticmethod
    def _target_factor(tick: _Tick, key: Key) -> float:
        """Product of the `stock_target` factors of the node, for the commodity and for all."""
        node, cid = key
        return tick.target_factor.get((node, cid), 1.0) * tick.target_factor.get((node, None), 1.0)

    def _apply_actions(self, tick: _Tick) -> None:
        context = self._context_for(tick.day, tick.budget, tick.strategic)
        for action in self._queued:
            cost = action.cost(context)
            action.apply(tick)
            tick.budget -= cost
            tick.action_spend += cost
            self._actions_log.append(action_record(action, tick.day, cost))
        self._queued = []

    def _arrivals(self, tick: _Tick) -> None:
        still_moving: list[Shipment] = []
        for ship in tick.in_transit:
            if ship.arrival_day <= tick.day:
                key = (ship.to_node, ship.commodity_id)
                tick.stocks[key] += ship.tonnes
                tick.inbound[key] = tick.inbound.get(key, 0.0) + ship.tonnes
            else:
                still_moving.append(ship)
        tick.in_transit = still_moving

    def _policy_step(self, tick: _Tick) -> None:
        # Companies' obligatory stock is their own inventory: all of it is on offer every day,
        # no decision involved; what is not shipped stays (see _return_unshipped_reserve).
        for key, held in tick.strategic_auto.items():
            if held > 0:
                tick.auto_offer[key] = held
                tick.release_offer[key] = tick.release_offer.get(key, 0.0) + held
                tick.strategic_auto[key] = 0.0
        available, stocks, demand, unmet = self._release_views(tick)
        released = release_step(
            self.policy.release,
            tick.day,
            available,
            stocks,
            demand,
            unmet,
            tick.release_started,
            tick.disruption_releasing,
        )
        for key, tonnes in released.items():
            self._release(tick, key, tonnes)

    def _release_views(
        self, tick: _Tick
    ) -> tuple[dict[Key, float], dict[Key, float], dict[Key, float], dict[Key, float]]:
        """What `release_step` sees per holder key: releasable tonnes, and for holders without
        demand of their own (hubs, source nodes) a stand-in for "the regions are short".

        A source node's `requires_processing` stock counts as releasable only while the node runs.
        """
        available: dict[Key, float] = {}
        stocks = dict(tick.stocks)
        demand = dict(tick.demand)
        unmet = dict(tick.prev_unmet)
        short_now = {
            c: any(tick.stocks[(n, c)] < tick.demand[(n, c)] for n in self._regions)
            for c in self._commodity_ids
        }
        prev_short = {
            c: sum(v for (_, cc), v in tick.prev_unmet.items() if cc == c)
            for c in self._commodity_ids
        }
        for key, held in tick.strategic.items():
            kind = tick.holder_kind.get(key, "region")
            if kind == "region":
                available[key] = held
                continue
            node, cid = key
            crude = tick.strategic_crude.get(key, 0.0)
            running = self._node_factor(tick, node, cid) > 0
            available[key] = held + (crude if kind == "node" and running else 0.0)
            unmet[key] = prev_short[cid]
            demand[key] = 1.0
            stocks[key] = 0.0 if short_now[cid] else 1.0
        return available, stocks, demand, unmet

    @staticmethod
    def _release(tick: _Tick, key: Key, tonnes: float) -> None:
        """Take `tonnes` from the holder (releasable stock first, then processed crude) and put
        them into the holder's usable stock, or into today's offer at a source node."""
        from_product = min(tonnes, tick.strategic[key])
        tick.strategic[key] -= from_product
        rest = tonnes - from_product
        if rest > 0:
            tick.strategic_crude[key] = tick.strategic_crude.get(key, 0.0) - rest
        if tick.holder_kind.get(key, "region") == "node":
            tick.release_offer[key] = tick.release_offer.get(key, 0.0) + tonnes
        else:
            tick.stocks[key] += tonnes

    def _consume(self, tick: _Tick) -> None:
        for key, demand in tick.demand.items():
            consumed = min(demand, tick.stocks[key])
            if demand - consumed < UNMET_EPS:
                consumed = demand
            tick.stocks[key] = max(0.0, tick.stocks[key] - consumed)
            tick.consumed[key] = consumed
            tick.unmet[key] = demand - consumed

    def _allocate(self, tick: _Tick) -> None:
        graph = self._effective_graph(tick)
        in_transit = self._in_transit_by_key(tick)
        for key, days in self._target_days.items():
            tick.target[key] = days * tick.demand[key] * self._target_factor(tick, key)
        for key, hub_target in self._hub_target.items():
            tick.target[key] = hub_target * self._target_factor(tick, key)
        for commodity in self._solver_order(tick):
            cid = commodity.id
            supplies = self._offers(tick, cid)
            if cid in self._via_hubs:
                hub_needs = self._needs(tick, in_transit, cid, self._hub_holds[cid])
                graph = self._solve(tick, graph, commodity, supplies, hub_needs)
                supplies = {
                    h: tick.stocks[(h, cid)] - self._served[(h, cid)]
                    for h in self._hub_holds[cid]
                    if tick.stocks[(h, cid)] - self._served[(h, cid)] > 0
                }
            needs = self._needs(tick, in_transit, cid, self._regions)
            graph = self._solve(tick, graph, commodity, supplies, needs)

    @staticmethod
    def _needs(
        tick: _Tick, in_transit: dict[Key, float], cid: CommodityId, nodes: list[NodeId]
    ) -> dict[NodeId, float]:
        needs = {}
        for n in nodes:
            key = (n, cid)
            gap = tick.target[key] - tick.stocks[key] - in_transit.get(key, 0.0)
            if gap > 0:
                needs[n] = gap
        return needs

    def _solve(
        self,
        tick: _Tick,
        graph: EffectiveGraph,
        commodity: Commodity,
        supplies: dict[NodeId, float],
        needs: dict[NodeId, float],
    ) -> EffectiveGraph:
        net_supplies, net_needs, local = net_supplies_needs(supplies, needs)
        for node, tonnes in local.items():
            tick.local[(node, commodity.id)] = tonnes
        allowed = EffectiveGraph(
            tuple(
                a
                for a in graph.arcs
                if self._arc_commodities[a.id] is None
                or commodity.id in self._arc_commodities[a.id]
            )
        )
        flows = self.solver.allocate(allowed, net_supplies, net_needs, commodity)
        for path in decompose_flows(allowed, flows):
            tick.plans.append((commodity.id, path))
        return graph.reduced(flows)

    def _offers(self, tick: _Tick, cid: CommodityId) -> dict[NodeId, float]:
        """Tonnes offered today per supply node; ports add redirected imports."""
        offers: dict[NodeId, float] = {}
        ports: dict[NodeId, tuple[float, float, float]] = {}
        for (n, c), baseline in self._supply.items():
            if c != cid:
                continue
            factor = self._node_factor(tick, n, cid)
            if self._node_kind[n] == "port":
                cap = (self._supply_max.get((n, c)) or baseline) * factor
                ports[n] = (baseline, min(baseline * factor, cap), cap)
            else:
                offers[n] = baseline * factor
        pending = [(p.day, p.tonnes) for p in tick.import_pending if p.commodity_id == cid]
        others = [p for p in tick.import_pending if p.commodity_id != cid]
        port_offers, ready, pending, lost = redirect_step(
            tick.day,
            self.commodities.import_redirect_delay_days,
            {n: v[0] for n, v in ports.items()},
            {n: v[1] for n, v in ports.items()},
            {n: v[2] for n, v in ports.items()},
            tick.import_ready.get(cid, 0.0),
            pending,
            self.commodities.import_backlog_loss_fraction_per_day,
        )
        tick.import_ready[cid] = ready
        tick.import_lost[cid] = tick.import_lost.get(cid, 0.0) + lost
        tick.import_day_lost[cid] = lost
        tick.import_pending = others + [PendingImport(cid, d, t) for d, t in pending]
        offers.update(port_offers)
        for (n, c), tonnes in tick.release_offer.items():
            if c == cid:
                offers[n] = offers.get(n, 0.0) + tonnes
        return {n: t for n, t in offers.items() if t > 0}

    def _solver_order(self, tick: _Tick) -> list[Commodity]:
        boosted = {b.commodity_id for b in tick.priority_boosts}
        ordered = self.commodities.by_priority()
        return [c for c in ordered if c.id in boosted] + [c for c in ordered if c.id not in boosted]

    def _edge_capacity(self, tick: _Tick, edge_id: EdgeId, base: float) -> float:
        extra = sum(b.extra_tonnes_per_day for b in tick.capacity_boosts if b.edge_id == edge_id)
        return (base + extra) * tick.edge_factor[edge_id]

    def _dispatch(self, tick: _Tick) -> None:
        arcs = {a.id: a for a in self._arcs}
        for key, tonnes in tick.local.items():
            tick.stocks[key] += tonnes
            tick.inbound[key] = tick.inbound.get(key, 0.0) + tonnes
        for cid, plan in tick.plans:
            transit = sum(arcs[a].transit_days for a in plan.arcs)
            tick.in_transit.append(
                Shipment(
                    commodity_id=cid,
                    from_node=plan.origin,
                    to_node=plan.destination,
                    path=plan.arcs,
                    tonnes=plan.tonnes,
                    dispatch_day=tick.day,
                    arrival_day=tick.day + transit,
                )
            )
            okey = (plan.origin, cid)
            if okey in self._hub_target:
                tick.stocks[okey] = max(0.0, tick.stocks[okey] - plan.tonnes)
            tick.outbound[okey] = tick.outbound.get(okey, 0.0) + plan.tonnes
            for arc_id in plan.arcs:
                tick.arc_flows[arc_id] = tick.arc_flows.get(arc_id, 0.0) + plan.tonnes
        self._return_unshipped_reserve(tick)

    def _return_unshipped_reserve(self, tick: _Tick) -> None:
        """Reserve offered at a source node and not shipped today goes back to the reserve.

        The node's own production is counted as shipped first, so the reserve is drawn only for
        what production could not cover. Returned tonnes are releasable (processed) stock.
        """
        for (n, c), offered in tick.release_offer.items():
            production = self._supply.get((n, c), 0.0) * self._node_factor(tick, n, c)
            used = min(offered, max(0.0, tick.outbound.get((n, c), 0.0) - production))
            # Companies' stock is drawn before the state's: it is the market's own inventory.
            auto = tick.auto_offer.get((n, c), 0.0)
            from_auto = min(used, auto)
            tick.strategic_auto[(n, c)] = tick.strategic_auto.get((n, c), 0.0) + auto - from_auto
            tick.strategic[(n, c)] += (offered - auto) - (used - from_auto)
        tick.release_offer = {}
        tick.auto_offer = {}

    def _snapshot(self, tick: _Tick) -> State:
        prev = self._state
        cumulative = dict(prev.cumulative_unmet)
        holding = prev.holding_cost
        for (_, cid), tonnes in tick.unmet.items():
            cumulative[cid] += tonnes
        held = [
            *tick.strategic.items(),
            *tick.strategic_crude.items(),
            *tick.strategic_auto.items(),
        ]
        for (_, cid), tonnes in held:
            holding += tonnes * self.policy.holding_cost_per_tonne_day.get(cid, 0.0)
        for n in self._regions:
            for cid in self._commodity_ids:
                k = (n, cid)
                self._daily_rows.append(
                    (
                        tick.day,
                        n,
                        cid,
                        tick.stocks[k],
                        tick.strategic[k],
                        tick.demand[k],
                        tick.consumed[k],
                        tick.unmet[k],
                        tick.inbound.get(k, 0.0),
                        tick.outbound.get(k, 0.0),
                    )
                )
        for hub, cid in self._hub_target:
            k = (hub, cid)
            self._daily_rows.append(
                (
                    tick.day,
                    hub,
                    cid,
                    tick.stocks[k],
                    tick.strategic.get(k, 0.0),
                    0.0,
                    0.0,
                    0.0,
                    tick.inbound.get(k, 0.0),
                    tick.outbound.get(k, 0.0),
                )
            )
        backlog = {c: tick.import_ready.get(c, 0.0) for c in self._commodity_ids}
        for pend in tick.import_pending:
            backlog[pend.commodity_id] += pend.tonnes
        for c, tonnes in backlog.items():
            self._backlog_peak[c] = max(self._backlog_peak[c], tonnes)
            baseline = sum(t for (_, sc), t in self._supply.items() if sc == c)
            self._import_rows.append(
                (
                    tick.day,
                    c,
                    tick.import_offer[c],
                    baseline,
                    tick.import_drop[c],
                    tonnes,
                    tick.import_day_lost.get(c, 0.0),
                )
            )
        edge_flows: dict[EdgeId, float] = {}
        for edge in self.network.edges:
            ids = [edge.id, edge.id + REVERSE_SUFFIX]
            flow = max(tick.arc_flows.get(i, 0.0) for i in ids)
            capacity = self._edge_capacity(tick, edge.id, edge.capacity_per_day)
            edge_flows[edge.id] = flow
            util = flow / capacity if capacity > 0 else 0.0
            self._edge_rows.append((tick.day, edge.id, flow, capacity, util))
        return State(
            day=tick.day + 1,
            stocks=freeze(tick.stocks),
            strategic_stocks=freeze(tick.strategic),
            in_transit=tuple(tick.in_transit),
            unmet=freeze(tick.unmet),
            effective_node_factor=freeze(tick.node_factor),
            effective_edge_factor=freeze(tick.edge_factor),
            effective_demand=freeze(tick.demand),
            edge_flows=freeze(edge_flows),
            budget=tick.budget,
            cumulative_unmet=freeze(cumulative),
            holding_cost=holding,
            action_spend=tick.action_spend,
            target_stocks=freeze(tick.target),
            release_started=freeze(tick.release_started),
            capacity_boosts=tuple(tick.capacity_boosts),
            priority_boosts=tuple(tick.priority_boosts),
            import_backlog=freeze(backlog),
            import_ready=freeze(tick.import_ready),
            import_pending=tuple(tick.import_pending),
            import_lost=freeze(tick.import_lost),
            import_drop=freeze(tick.import_drop),
            disruption_streak=freeze(tick.disruption_streak),
            disruption_started=freeze(tick.disruption_started),
            strategic_unprocessed=freeze(tick.strategic_crude),
            automatic_stocks=freeze(tick.strategic_auto),
        )

    def _effective_graph(self, tick: _Tick) -> EffectiveGraph:
        return EffectiveGraph(
            tuple(
                EffectiveArc(
                    a.id,
                    a.edge_id,
                    a.from_node,
                    a.to_node,
                    self._edge_capacity(tick, a.edge_id, a.capacity_per_day),
                    a.cost_per_tonne,
                    a.transit_days,
                )
                for a in self._arcs
            )
        )

    @staticmethod
    def _in_transit_by_key(tick: _Tick) -> dict[Key, float]:
        totals: dict[Key, float] = {}
        for ship in tick.in_transit:
            key = (ship.to_node, ship.commodity_id)
            totals[key] = totals.get(key, 0.0) + ship.tonnes
        return totals
