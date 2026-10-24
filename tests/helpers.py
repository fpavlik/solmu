"""Shared synthetic chain network: source -> hub -> region, one commodity `x`.

Demand 10 t/day, supply 30 t/day, default_stock_days 2, transit 1 + 2 days, so the lead
time is 3 days, the target stock is (2 + 3) x 10 = 50 t and the initial usable stock 50 t.
"""

from __future__ import annotations

from solmu.core.engine import Simulation
from solmu.core.model import (
    Commodities,
    Commodity,
    DemandRecord,
    Edge,
    Network,
    Node,
    Policy,
    ReleaseRule,
    Scenario,
    SupplyRecord,
)

REG = ("reg", "x")


def chain_network(demand: float = 10.0, supply: float = 30.0) -> Network:
    return Network(
        nodes=[
            Node(id="src", name="Source", kind="source", lat=0, lon=0),
            Node(id="hub", name="Hub", kind="hub", lat=0, lon=1),
            Node(id="reg", name="Region", kind="region", lat=0, lon=2, population=1000),
        ],
        edges=[
            Edge(id="e1", from_node="src", to_node="hub", mode="road", capacity_per_day=100,
                 transit_days=1, cost_per_tonne=1.0, bidirectional=False),
            Edge(id="e2", from_node="hub", to_node="reg", mode="rail", capacity_per_day=100,
                 transit_days=2, cost_per_tonne=1.0, bidirectional=False),
        ],
        demand=[DemandRecord(node_id="reg", commodity_id="x", tonnes_per_day=demand)],
        supply=[SupplyRecord(node_id="src", commodity_id="x", tonnes_per_day=supply)],
    )  # fmt: skip


def chain_policy(
    min_stock_days: float = 0.0,
    trigger: str = "manual",
    delay_days: int = 0,
    max_fraction: float = 1.0,
    budget: float = 1000.0,
) -> Policy:
    return Policy(
        id="p",
        name="P",
        min_stock_days={"x": min_stock_days},
        release=ReleaseRule(
            trigger=trigger, delay_days=delay_days, max_release_fraction_per_day=max_fraction
        ),
        emergency_budget=budget,
        holding_cost_per_tonne_day={"x": 0.5},
    )


def chain_sim(
    events: list[dict] | None = None,
    horizon: int = 12,
    policy: Policy | None = None,
    seed: int = 0,
    noise: float = 0.0,
) -> Simulation:
    commodities = Commodities(
        commodities=[Commodity(id="x", name="X", priority=1)], default_stock_days=2
    )
    scenario = Scenario.model_validate(
        {
            "id": "s",
            "name": "S",
            "horizon_days": horizon,
            "demand_noise_fraction": noise,
            "events": events or [],
        }
    )
    return Simulation(chain_network(), scenario, policy or chain_policy(), commodities, seed=seed)


MANUAL = ReleaseRule(trigger="manual")


def manual_policy(budget: float = 100.0, min_stock_days: dict[str, float] | None = None) -> Policy:
    """Policy that never releases automatically; optional strategic stock and budget."""
    return Policy(
        id="p",
        name="P",
        release=MANUAL,
        min_stock_days=min_stock_days or {},
        emergency_budget=budget,
    )


def make_scenario(events: list[dict] | None, horizon: int) -> Scenario:
    return Scenario.model_validate(
        {"id": "s", "name": "S", "horizon_days": horizon, "events": events or []}
    )


def run_plan(sim: Simulation, plan: dict[int, list] | None = None):
    """Run to the horizon, applying `plan[day]` (a list of actions) at the start of that day."""
    plan = plan or {}
    while not sim.done:
        if sim.day in plan:
            sim.apply(plan[sim.day])
        sim.step()
    return sim.result()


def first_unmet_days(result) -> dict[str, int]:
    """First day with unmet demand per node id."""
    daily = result.daily
    return daily[daily["unmet"] > 0].groupby("node_id")["day"].min().to_dict()


def unmet_by_node(result) -> dict[str, float]:
    return {n: float(v) for n, v in result.daily.groupby("node_id")["unmet"].sum().items()}


def bottleneck_chain_sim(
    e2_capacity: float = 4.0,
    events: list[dict] | None = None,
    budget: float = 1000.0,
    horizon: int = 14,
    min_stock_days: float = 0.0,
) -> Simulation:
    """The chain network with the region edge `e2` (default 4 t/day vs demand 10) as bottleneck."""
    network = chain_network()
    network = network.model_copy(
        update={
            "edges": [
                e.model_copy(update={"capacity_per_day": e2_capacity}) if e.id == "e2" else e
                for e in network.edges
            ]
        }
    )
    commodities = Commodities(
        commodities=[Commodity(id="x", name="X", priority=1)], default_stock_days=2
    )
    policy = chain_policy(min_stock_days=min_stock_days, budget=budget)
    return Simulation(network, make_scenario(events, horizon), policy, commodities)


def shared_edge_sim(horizon: int = 12) -> Simulation:
    """Commodities x (priority 1) and y (priority 2), 10 t/day each, one 10 t/day edge."""
    network = Network(
        nodes=[
            Node(id="src", name="S", kind="source", lat=0, lon=0),
            Node(id="reg", name="R", kind="region", lat=0, lon=1, population=1),
        ],
        edges=[
            Edge(id="e", from_node="src", to_node="reg", mode="road", capacity_per_day=10,
                 transit_days=1, cost_per_tonne=1.0, bidirectional=False)
        ],
        demand=[DemandRecord(node_id="reg", commodity_id=c, tonnes_per_day=10) for c in "xy"],
        supply=[SupplyRecord(node_id="src", commodity_id=c, tonnes_per_day=100) for c in "xy"],
    )  # fmt: skip
    commodities = Commodities(
        commodities=[
            Commodity(id="x", name="X", priority=1),
            Commodity(id="y", name="Y", priority=2),
        ],
        default_stock_days=2,
    )
    return Simulation(network, make_scenario(None, horizon), manual_policy(), commodities)


def port_pair_sim(
    max_b: float = 10.0,
    delay: int = 2,
    loss: float = 0.0,
    horizon: int = 20,
    closure: tuple[int, int] = (2, 11),
) -> Simulation:
    """Ports A and B (10 t/day each) feed region R (20 t/day); port A closed on `closure`.

    B's handling cap is `max_b`; lead time 1 day, so R holds (2 + 1) x 20 = 60 t at the start.
    """
    network = Network(
        nodes=[
            Node(id="A", name="A", kind="port", lat=0, lon=0),
            Node(id="B", name="B", kind="port", lat=1, lon=0),
            Node(id="R", name="R", kind="region", lat=0, lon=1, population=1000),
        ],
        edges=[
            Edge(id=f"e{p}", from_node=p, to_node="R", mode="road", capacity_per_day=1000,
                 transit_days=1, cost_per_tonne=1.0, bidirectional=False)
            for p in "AB"
        ],
        demand=[DemandRecord(node_id="R", commodity_id="x", tonnes_per_day=20)],
        supply=[
            SupplyRecord(node_id=p, commodity_id="x", tonnes_per_day=10, max_tonnes_per_day=m)
            for p, m in (("A", 10.0), ("B", max_b))
        ],
    )  # fmt: skip
    commodities = Commodities(
        commodities=[Commodity(id="x", name="X", priority=1)],
        default_stock_days=2,
        import_redirect_delay_days=delay,
        import_backlog_loss_fraction_per_day=loss,
    )
    events = [
        {"day": closure[0], "type": "node_capacity", "node_id": "A", "factor": 0.0,
         "until_day": closure[1]}
    ]  # fmt: skip
    return Simulation(network, make_scenario(events, horizon), manual_policy(), commodities)


def star_sim(events: list[dict] | None = None, horizon: int = 20) -> Simulation:
    """Source feeding regions r1, r2, r3 (demand 10 t/day, edge capacity 10, 1/2/3 days)."""
    nodes = [Node(id="src", name="S", kind="source", lat=0, lon=0)] + [
        Node(id=f"r{i}", name="R", kind="region", lat=i, lon=1, population=100 * i)
        for i in (1, 2, 3)
    ]
    edges = [
        Edge(id=f"e{i}", from_node="src", to_node=f"r{i}", mode="road", capacity_per_day=10,
             transit_days=i, cost_per_tonne=1.0, bidirectional=False)
        for i in (1, 2, 3)
    ]  # fmt: skip
    network = Network(
        nodes=nodes,
        edges=edges,
        demand=[
            DemandRecord(node_id=f"r{i}", commodity_id="x", tonnes_per_day=10) for i in (1, 2, 3)
        ],
        supply=[SupplyRecord(node_id="src", commodity_id="x", tonnes_per_day=100)],
    )
    commodities = Commodities(
        commodities=[Commodity(id="x", name="X", priority=1)], default_stock_days=2
    )
    return Simulation(network, make_scenario(events, horizon), manual_policy(), commodities)


def hub_star_sim(close_port_day: int | None = 3, horizon: int = 24) -> Simulation:
    """Port P -> hub H (holds `medicine`) -> regions r1..r3 at 1, 2, 3 days, 10 t/day each."""
    nodes = [
        Node(id="P", name="P", kind="port", lat=0, lon=0),
        Node(id="H", name="H", kind="hub", lat=0, lon=1, tags={"holds": "medicine"}),
        *[
            Node(id=f"r{i}", name="R", kind="region", lat=i, lon=2, population=10)
            for i in (1, 2, 3)
        ],
    ]
    edges = [
        Edge(id="PH", from_node="P", to_node="H", mode="road", capacity_per_day=1000,
             transit_days=1, cost_per_tonne=1.0),
        *[
            Edge(id=f"H{i}", from_node="H", to_node=f"r{i}", mode="road",
                 capacity_per_day=1000, transit_days=i, cost_per_tonne=1.0)
            for i in (1, 2, 3)
        ],
    ]  # fmt: skip
    network = Network(
        nodes=nodes,
        edges=edges,
        demand=[
            DemandRecord(node_id=f"r{i}", commodity_id="medicine", tonnes_per_day=10)
            for i in (1, 2, 3)
        ],
        supply=[SupplyRecord(node_id="P", commodity_id="medicine", tonnes_per_day=100)],
    )
    commodities = Commodities(
        commodities=[Commodity(id="medicine", name="M", priority=1, via_hubs=True)],
        default_stock_days=2,
    )
    events = []
    if close_port_day is not None:
        events.append({"day": close_port_day, "type": "node_capacity", "node_id": "P",
                       "factor": 0.0, "until_day": 99})  # fmt: skip
    return Simulation(network, make_scenario(events, horizon), manual_policy(), commodities)


def fast_slow_sim(fast_only_for: set[str] | None, horizon: int = 8) -> Simulation:
    """Source S to region R by a 1-day edge `fast` (restricted to `fast_only_for`) or 3-day `slow`.

    Fuel 10 t/day and food 20 t/day; both edges have capacity 1000 and the same cost per tonne.
    """
    network = Network(
        nodes=[
            Node(id="S", name="S", kind="source", lat=0, lon=0),
            Node(id="R", name="R", kind="region", lat=0, lon=1, population=10),
        ],
        edges=[
            Edge(id="fast", from_node="S", to_node="R", mode="rail", capacity_per_day=1000,
                 transit_days=1, cost_per_tonne=1.0, bidirectional=False,
                 commodities=None if fast_only_for is None else frozenset(fast_only_for)),
            Edge(id="slow", from_node="S", to_node="R", mode="road", capacity_per_day=1000,
                 transit_days=3, cost_per_tonne=1.0, bidirectional=False),
        ],
        demand=[
            DemandRecord(node_id="R", commodity_id="fuel", tonnes_per_day=10),
            DemandRecord(node_id="R", commodity_id="food", tonnes_per_day=20),
        ],
        supply=[
            SupplyRecord(node_id="S", commodity_id=c, tonnes_per_day=100) for c in ("fuel", "food")
        ],
    )  # fmt: skip
    commodities = Commodities(
        commodities=[
            Commodity(id="fuel", name="Fuel", priority=1),
            Commodity(id="food", name="Food", priority=2),
        ],
        default_stock_days=2,
    )
    return Simulation(network, make_scenario(None, horizon), manual_policy(), commodities)
