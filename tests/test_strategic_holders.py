"""Where strategic stock is held: regions (default), hubs, or a source node with crude.

Hub chain: port P (x, 30 t/day) -> hub H (holds x, 1 day) -> region R (10 t/day, 2 days),
via_hubs. L_hub = 1, hub target (7 + 1) x 10 = 80 t, safety 10 t; R target (2 + 2) x 10 = 40 t.
Node chain (tests/helpers): source -> hub (pass-through) -> region, 10 t/day, transit 3 days,
region target and initial stock 50 t.
"""

import pytest

from solmu.core.actions import ActionError, ReleaseReserve
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
    StrategicHolder,
    SupplyRecord,
)
from tests.helpers import chain_network, make_scenario

HUB = StrategicHolder(kind="hub", share=1.0)
MANUAL = ReleaseRule(trigger="manual")


def _hub_network() -> Network:
    return Network(
        nodes=[
            Node(id="P", name="P", kind="port", lat=0, lon=0),
            Node(id="H", name="H", kind="hub", lat=0, lon=1, tags={"holds": "x"}),
            Node(id="R", name="R", kind="region", lat=0, lon=2, population=100),
        ],
        edges=[
            Edge(id="PH", from_node="P", to_node="H", mode="road", capacity_per_day=1000,
                 transit_days=1, cost_per_tonne=1.0),
            Edge(id="HR", from_node="H", to_node="R", mode="road", capacity_per_day=1000,
                 transit_days=2, cost_per_tonne=1.0),
        ],
        demand=[DemandRecord(node_id="R", commodity_id="x", tonnes_per_day=10)],
        supply=[SupplyRecord(node_id="P", commodity_id="x", tonnes_per_day=30)],
    )  # fmt: skip


def _policy(holders: list[StrategicHolder], rule: ReleaseRule = MANUAL) -> Policy:
    return Policy(
        id="p",
        name="P",
        min_stock_days={"x": 10},
        release=rule,
        emergency_budget=1000,
        holding_cost_per_tonne_day={"x": 0.5},
        strategic_stock_at={"x": holders},
    )


def _hub_sim(policy: Policy) -> Simulation:
    commodities = Commodities(
        commodities=[Commodity(id="x", name="X", priority=1, via_hubs=True)], default_stock_days=2
    )
    return Simulation(_hub_network(), make_scenario([], 10), policy, commodities)


def test_hub_holder_sizes_stock_by_served_demand_and_charges_holding() -> None:
    sim = _hub_sim(_policy([HUB]))
    # 10 days x 10 t/day served = 100 t at the hub, nothing at the region.
    assert sim.state.strategic_stocks[("H", "x")] == 100.0
    assert sim.state.strategic_stocks[("R", "x")] == 0.0
    sim.step()
    assert sim.state.holding_cost == pytest.approx(100.0 * 0.5)
    hub_rows = sim.result().daily.query("node_id == 'H'")
    assert hub_rows["strategic_stock"].iloc[0] == 100.0


def test_split_between_hub_and_region() -> None:
    sim = _hub_sim(
        _policy([StrategicHolder(kind="hub", share=0.7), StrategicHolder(kind="region", share=0.3)])
    )
    assert sim.state.strategic_stocks[("H", "x")] == pytest.approx(70.0)
    assert sim.state.strategic_stocks[("R", "x")] == pytest.approx(30.0)


def test_release_at_hub_raises_hub_stock_by_the_released_tonnes() -> None:
    plain = _hub_sim(_policy([HUB]))
    plain.step()
    acted = _hub_sim(_policy([HUB]))
    acted.apply([ReleaseReserve("H", "x", 30.0)])
    acted.step()
    # The hub holds 80 t and ships only R's 10 t need either way, so the 30 t stay at the hub.
    assert acted.state.stocks[("H", "x")] - plain.state.stocks[("H", "x")] == pytest.approx(30.0)
    assert acted.state.strategic_stocks[("H", "x")] == pytest.approx(70.0)


def test_release_where_nothing_is_held_is_rejected() -> None:
    sim = _hub_sim(_policy([HUB]))
    # Regions always carry a strategic key; with a hub holder theirs is 0 t.
    with pytest.raises(ActionError, match="exceeds strategic stock 0.000"):
        sim.apply([ReleaseReserve("R", "x", 1.0)])
    with pytest.raises(ActionError, match="unknown holder"):
        sim.apply([ReleaseReserve("P", "x", 1.0)])


def _node_sim(requires_processing: float | None, horizon: int = 12) -> Simulation:
    holders = (
        []
        if requires_processing is None
        else [
            StrategicHolder(
                kind="node", node_id="src", share=1.0, requires_processing=requires_processing
            )
        ]
    )
    policy = Policy(
        id="p",
        name="P",
        min_stock_days={"x": 10 if holders else 0},
        release=ReleaseRule(trigger="always", max_release_fraction_per_day=1.0),
        emergency_budget=1000,
        holding_cost_per_tonne_day={"x": 0.5},
        strategic_stock_at={"x": holders} if holders else {},
    )
    commodities = Commodities(
        commodities=[Commodity(id="x", name="X", priority=1)], default_stock_days=2
    )
    off = [
        {
            "day": 0,
            "type": "node_capacity",
            "node_id": "src",
            "factor": 0.0,
            "until_day": horizon - 1,
        }
    ]
    return Simulation(chain_network(), make_scenario(off, horizon), policy, commodities)


def test_source_node_reserve_feeds_the_network_while_the_source_is_down() -> None:
    # Source off for the whole run; region eats 10/day from 50 t, shipments take 3 days.
    # No reserve: short from day 5, 7 days x 10 t = 70 t.
    assert _node_sim(None).run().summary["total_unmet"] == pytest.approx(70.0)
    # 100 t reserve, half crude (frozen while the source is down): 50 t of product ship 10 t
    # a day on days 0-4 and land on days 3-7, so the region is short only on days 10-11: 20 t.
    half = _node_sim(0.5)
    half.step()
    assert half.state.strategic_stocks[("src", "x")] == pytest.approx(40.0)
    assert half.state.strategic_unprocessed[("src", "x")] == pytest.approx(50.0)
    assert half.run().summary["total_unmet"] == pytest.approx(20.0)
    # All product: 10 t a day on days 0-9 land on days 3-12, no shortage inside 12 days.
    assert _node_sim(0.0).run().summary["total_unmet"] == pytest.approx(0.0)


def test_unshipped_reserve_returns_and_crude_is_charged_holding() -> None:
    sim = _node_sim(0.5)
    sim.step()
    # Day 0: 50 t product offered, 10 t shipped, 40 t back; crude 50 t untouched. Holding on
    # end-of-day 40 + 50 = 90 t at 0.5.
    assert sim.state.holding_cost == pytest.approx(45.0)


def _mixed_sim(auto_share: float, horizon: int = 12) -> Simulation:
    """Source off all run; `auto_share` of a 100 t reserve is companies' stock, the rest state
    stock under a manual (never releasing) rule."""
    holders = []
    if auto_share > 0:
        holders.append(
            StrategicHolder(kind="node", node_id="src", share=auto_share, release="automatic")
        )
    if auto_share < 1:
        holders.append(StrategicHolder(kind="node", node_id="src", share=1 - auto_share))
    policy = Policy(
        id="p",
        name="P",
        min_stock_days={"x": 10},
        release=MANUAL,
        emergency_budget=1000,
        holding_cost_per_tonne_day={"x": 0.5},
        strategic_stock_at={"x": holders},
    )
    commodities = Commodities(
        commodities=[Commodity(id="x", name="X", priority=1)], default_stock_days=2
    )
    off = [
        {
            "day": 0,
            "type": "node_capacity",
            "node_id": "src",
            "factor": 0.0,
            "until_day": horizon - 1,
        }
    ]
    return Simulation(chain_network(), make_scenario(off, horizon), policy, commodities)


def test_automatic_stock_is_drawn_without_a_release_decision() -> None:
    # Same arithmetic as the product cases above: 100 t drawn 10 t/day -> no shortage;
    # 50 t automatic + 50 t state stock never released -> short on days 10-11, 20 t.
    full = _mixed_sim(1.0)
    full.step()
    assert full.state.automatic_stocks[("src", "x")] == pytest.approx(90.0)
    assert full.state.strategic_stocks[("src", "x")] == pytest.approx(0.0)
    assert full.state.holding_cost == pytest.approx(90.0 * 0.5)
    assert full.run().summary["total_unmet"] == pytest.approx(0.0)
    half = _mixed_sim(0.5)
    assert half.run().summary["total_unmet"] == pytest.approx(20.0)
    assert half.state.strategic_stocks[("src", "x")] == pytest.approx(50.0)


def test_shortage_trigger_at_a_hub_follows_the_regions() -> None:
    # Port closed all run. R holds 40 t, the hub 80 t (safety 10): the hub ships 10/day on days
    # 0-6 and then sits at its safety stock; R eats its 40 t plus the 70 t that landed and is
    # first short on day 11. Day 12: the previous day was short and R is short now, so the hub
    # releases 50% of its 100 t (50 t) and ships R's whole need of 40 t (lands day 14); day 13
    # releases 25 t more. Day 14: the 40 t landed, nobody is short, releases stop.
    closed = [{"day": 0, "type": "node_capacity", "node_id": "P", "factor": 0.0, "until_day": 19}]
    rule = ReleaseRule(trigger="shortage", delay_days=0, max_release_fraction_per_day=0.5)
    commodities = Commodities(
        commodities=[Commodity(id="x", name="X", priority=1, via_hubs=True)], default_stock_days=2
    )
    sim = Simulation(_hub_network(), make_scenario(closed, 16), _policy([HUB], rule), commodities)
    result = sim.run()
    daily = result.daily
    hub = daily[daily["node_id"] == "H"].set_index("day")
    region = daily[daily["node_id"] == "R"].set_index("day")
    assert result.summary["first_shortage_day"] == {"x": 11}
    assert list(region["unmet"].iloc[10:15]) == pytest.approx([0, 10, 10, 10, 0])
    assert list(hub["strategic_stock"].iloc[10:15]) == pytest.approx([100, 100, 50, 25, 25])
    assert hub["stock"].iloc[12] == pytest.approx(20.0)  # 10 safety + 50 released - 40 shipped
    assert region["stock"].iloc[14] == pytest.approx(30.0)  # 40 landed, 10 eaten


def test_manual_release_at_a_source_node_offers_the_tonnes_once() -> None:
    # Source off, 100 t state stock under a manual rule. Releasing 30 t on day 0 offers 30 t;
    # the region needs 10 t, so 10 ship (land day 3) and 20 t return: 90 t left. One extra day
    # of supply turns the 70 t of unmet into 60 t.
    sim = _mixed_sim(0.0)
    with pytest.raises(ActionError, match="exceeds strategic stock"):
        sim.apply([ReleaseReserve("src", "x", 100.5)])
    sim.apply([ReleaseReserve("src", "x", 30.0)])
    sim.step()
    assert sim.state.strategic_stocks[("src", "x")] == pytest.approx(90.0)
    assert sim.run().summary["total_unmet"] == pytest.approx(60.0)


def test_reserve_at_a_running_source_is_drawn_only_beyond_production() -> None:
    # Source at 20% (6 t/day), 100 t of state stock on offer in full every day (`always`, 1.0).
    # The region needs 10 t/day: 6 t come from production, 4 t from the reserve: 96, 92, 88 ...
    holders = [StrategicHolder(kind="node", node_id="src", share=1.0)]
    policy = Policy(
        id="p",
        name="P",
        min_stock_days={"x": 10},
        release=ReleaseRule(trigger="always", max_release_fraction_per_day=1.0),
        emergency_budget=1000,
        holding_cost_per_tonne_day={"x": 0.5},
        strategic_stock_at={"x": holders},
    )
    commodities = Commodities(
        commodities=[Commodity(id="x", name="X", priority=1)], default_stock_days=2
    )
    slow = [{"day": 0, "type": "node_capacity", "node_id": "src", "factor": 0.2, "until_day": 11}]
    sim = Simulation(chain_network(), make_scenario(slow, 12), policy, commodities)
    held = [sim.step().strategic_stocks[("src", "x")] for _ in range(3)]
    assert held == pytest.approx([96.0, 92.0, 88.0])
    assert sim.run().summary["total_unmet"] == 0.0


def test_policy_and_engine_validation() -> None:
    with pytest.raises(ValueError, match="automatic"):
        StrategicHolder(kind="hub", share=1.0, release="automatic")
    with pytest.raises(ValueError, match="automatic"):
        StrategicHolder(
            kind="node", node_id="src", share=1.0, release="automatic", requires_processing=0.5
        )
    with pytest.raises(ValueError, match="shares sum"):
        _policy([StrategicHolder(kind="hub", share=0.5)])
    with pytest.raises(ValueError, match="node_id"):
        StrategicHolder(kind="node", share=1.0)
    with pytest.raises(ValueError, match="kind 'node' only"):
        StrategicHolder(kind="region", share=1.0, requires_processing=0.5)
    commodities = Commodities(commodities=[Commodity(id="x", name="X", priority=1)])
    with pytest.raises(ValueError, match="via_hubs"):
        Simulation(_hub_network(), make_scenario([], 5), _policy([HUB]), commodities)
    port_holder = [StrategicHolder(kind="node", node_id="P", share=1.0)]
    with pytest.raises(ValueError, match="not a source node"):
        Simulation(_hub_network(), make_scenario([], 5), _policy(port_holder), commodities)
