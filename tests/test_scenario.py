import pytest

from solmu.core.engine import Simulation
from solmu.core.model import (
    Commodities,
    Commodity,
    DemandRecord,
    Edge,
    Network,
    Node,
    Scenario,
    SupplyRecord,
)
from solmu.core.scenario import active_modifiers
from tests.helpers import chain_sim, hub_star_sim, manual_policy


def _scenario(events: list[dict]) -> Scenario:
    return Scenario.model_validate({"id": "s", "name": "S", "horizon_days": 20, "events": events})


def test_edge_capacity_zero_stops_flow_for_window_and_restores_after() -> None:
    events = [{"day": 5, "type": "edge_capacity", "edge_id": "e2", "factor": 0.0, "until_day": 9}]
    result = chain_sim(events, horizon=20).run()
    e2 = result.edges_daily[result.edges_daily["edge_id"] == "e2"].set_index("day")
    assert list(e2.loc[5:9, "flow"]) == [0.0] * 5
    assert list(e2.loc[5:9, "capacity"]) == [0.0] * 5
    assert (e2.loc[[4, 10], "flow"] > 0).all()
    assert (e2.loc[[4, 10], "capacity"] == 100.0).all()
    assert e2.loc[10, "flow"] > 10.0  # catch-up of the backlog once the edge is back


def test_node_capacity_zero_on_only_supply_node_causes_shortage_after_stock_runs_out() -> None:
    events = [{"day": 2, "type": "node_capacity", "node_id": "src", "factor": 0.0, "until_day": 99}]
    sim = chain_sim(events, horizon=12)
    result = sim.run()
    # Shipments of days 0 and 1 (10 t each) arrive on days 3 and 4. Stock at end of day:
    # d0 40, d1 30, d2 20, d3 20+10-10=20, d4 20, d5 10, d6 0; day 7 onward demand 10 is unmet.
    daily = result.daily.set_index("day")
    assert list(daily["stock"].iloc[:7]) == pytest.approx([40, 30, 20, 20, 20, 10, 0])
    assert list(daily["unmet"]) == pytest.approx([0] * 7 + [10] * 5)
    assert result.summary["first_shortage_day"] == {"x": 7}
    assert result.summary["total_unmet"] == pytest.approx(50.0)
    assert sim.state.effective_node_factor["src"] == 0.0


def test_demand_factor_raises_consumption_inside_window_only() -> None:
    events = [
        {
            "day": 3,
            "type": "demand",
            "node_id": "reg",
            "commodity_id": "x",
            "factor": 1.5,
            "until_day": 6,
        }
    ]
    result = chain_sim(events, horizon=10).run()
    daily = result.daily.set_index("day")
    assert list(daily["demand"]) == pytest.approx([10] * 3 + [15] * 4 + [10] * 3)
    assert list(daily["consumed"]) == pytest.approx([10] * 3 + [15] * 4 + [10] * 3)


def test_overlapping_events_multiply_and_windows_are_inclusive() -> None:
    scenario = _scenario(
        [
            {"day": 2, "type": "node_capacity", "node_id": "n", "factor": 0.5, "until_day": 6},
            {"day": 4, "type": "node_capacity", "node_id": "n", "factor": 0.5, "until_day": 8},
            {"day": 4, "type": "edge_capacity", "edge_id": "e", "factor": 0.2, "until_day": 4},
            {"day": 4, "type": "demand", "node_id": "r", "commodity_id": "x", "factor": 2.0,
             "until_day": 5},
            {"day": 5, "type": "demand", "node_id": "r", "commodity_id": "x", "factor": 3.0,
             "until_day": 5},
        ]
    )  # fmt: skip
    assert active_modifiers(scenario, 1).node_factor == {}
    assert active_modifiers(scenario, 2).node_factor == {"n": 0.5}
    assert active_modifiers(scenario, 4).node_factor == {"n": 0.25}
    assert active_modifiers(scenario, 6).node_factor == {"n": 0.25}
    assert active_modifiers(scenario, 7).node_factor == {"n": 0.5}
    assert active_modifiers(scenario, 9).node_factor == {}
    assert active_modifiers(scenario, 4).edge_factor == {"e": 0.2}
    assert active_modifiers(scenario, 5).edge_factor == {}
    assert active_modifiers(scenario, 4).demand_factor == {("r", "x"): 2.0}
    assert active_modifiers(scenario, 5).demand_factor == {("r", "x"): 6.0}


def test_overlapping_events_in_engine_scale_supply_state() -> None:
    events = [
        {"day": 0, "type": "node_capacity", "node_id": "src", "factor": 0.5, "until_day": 9},
        {"day": 0, "type": "node_capacity", "node_id": "src", "factor": 0.5, "until_day": 9},
    ]
    sim = chain_sim(events, horizon=3)
    assert sim.step().effective_node_factor["src"] == 0.25


@pytest.mark.parametrize(
    ("event", "message"),
    [
        ({"type": "node_capacity", "node_id": "nope", "factor": 0.0}, "unknown node id 'nope'"),
        ({"type": "edge_capacity", "edge_id": "zz", "factor": 0.0}, "unknown edge id 'zz'"),
        (
            {"type": "demand", "node_id": "reg", "commodity_id": "gold", "factor": 2.0},
            "unknown commodity id 'gold'",
        ),
        (
            {"type": "demand", "node_id": "hub", "commodity_id": "x", "factor": 2.0},
            "not a region",
        ),
    ],
)
def test_unknown_event_targets_are_rejected_at_construction(event: dict, message: str) -> None:
    events = [
        {"day": 0, "type": "node_capacity", "node_id": "src", "factor": 1.0, "until_day": 1},
        {"day": 0, "until_day": 1, **event},
    ]
    with pytest.raises(ValueError) as exc:
        chain_sim(events)
    assert "scenario 's'" in str(exc.value)
    assert "event 1" in str(exc.value)
    assert message in str(exc.value)


def test_wildcard_demand_event_expands_to_all_regions() -> None:
    event = {"day": 1, "type": "demand", "node_id": "*", "commodity_id": "x", "factor": 2.0,
             "until_day": 3}  # fmt: skip
    scenario = _scenario([event])
    mods = active_modifiers(scenario, 2, ["r1", "r2"])
    assert mods.demand_factor == {("r1", "x"): 2.0, ("r2", "x"): 2.0}
    assert active_modifiers(scenario, 0, ["r1", "r2"]).demand_factor == {}
    result = chain_sim([event], horizon=5).run()
    assert list(result.daily["demand"]) == pytest.approx([10, 20, 20, 20, 10])


def _scenario_h(events: list[dict], horizon: int) -> Scenario:
    return Scenario.model_validate(
        {"id": "s", "name": "S", "horizon_days": horizon, "events": events}
    )


def _port_sim(events: list[dict], horizon: int = 8) -> Simulation:
    """Port P (x and y, 10 t/day each, no headroom) feeds region R (10 t/day each) by one edge."""
    network = Network(
        nodes=[
            Node(id="P", name="P", kind="port", lat=0, lon=0),
            Node(id="R", name="R", kind="region", lat=0, lon=1, population=100),
        ],
        edges=[
            Edge(id="e", from_node="P", to_node="R", mode="road", capacity_per_day=1000,
                 transit_days=1, cost_per_tonne=1.0, bidirectional=False)
        ],
        demand=[DemandRecord(node_id="R", commodity_id=c, tonnes_per_day=10) for c in "xy"],
        supply=[SupplyRecord(node_id="P", commodity_id=c, tonnes_per_day=10) for c in "xy"],
    )  # fmt: skip
    commodities = Commodities(
        commodities=[
            Commodity(id="x", name="X", priority=1),
            Commodity(id="y", name="Y", priority=2),
        ],
        default_stock_days=2,
    )
    return Simulation(network, _scenario_h(events, horizon), manual_policy(), commodities)


def test_node_capacity_with_commodity_id_scales_only_that_commodity() -> None:
    events = [
        {"day": 1, "type": "node_capacity", "node_id": "P", "commodity_id": "x", "factor": 0.0,
         "until_day": 3},
    ]  # fmt: skip
    sim = _port_sim(events)
    result = sim.run()
    imports = result.imports_daily.set_index(["day", "commodity_id"])
    # Offer = baseline 10 x factor: x is 0 on days 1-3 and 10 otherwise; y is never touched.
    assert [imports.loc[(d, "x"), "offer"] for d in range(5)] == [10, 0, 0, 0, 10]
    assert [imports.loc[(d, "y"), "offer"] for d in range(5)] == [10] * 5
    assert [imports.loc[(d, "x"), "drop"] for d in range(5)] == [0, 1, 1, 1, 0]
    assert (result.imports_daily.query("commodity_id == 'y'")["drop"] == 0).all()
    # No headroom, so x's 10 t/day shortfall only queues: backlog 10, 20, 30 on days 1-3.
    assert [imports.loc[(d, "x"), "backlog"] for d in range(1, 4)] == [10, 20, 30]
    assert result.summary["total_unmet_by_commodity"]["y"] == 0.0
    assert sim.state.effective_node_factor["P"] == 1.0  # node-wide factor is untouched


def test_node_wide_and_per_commodity_node_factors_multiply() -> None:
    events = [
        {"day": 0, "type": "node_capacity", "node_id": "P", "factor": 0.5, "until_day": 9},
        {"day": 0, "type": "node_capacity", "node_id": "P", "commodity_id": "x", "factor": 0.5,
         "until_day": 9},
    ]  # fmt: skip
    mods = active_modifiers(_scenario_h(events, 10), 0)
    assert mods.node_factor == {"P": 0.5}
    assert mods.node_commodity_factor == {("P", "x"): 0.5}
    imports = _port_sim(events).run().imports_daily.set_index(["day", "commodity_id"])
    assert imports.loc[(0, "x"), "offer"] == 2.5  # 10 x 0.5 x 0.5
    assert imports.loc[(0, "y"), "offer"] == 5.0  # 10 x 0.5


def test_node_capacity_unknown_commodity_is_rejected() -> None:
    events = [{"day": 0, "type": "node_capacity", "node_id": "P", "commodity_id": "z",
               "factor": 0.0, "until_day": 1}]  # fmt: skip
    with pytest.raises(ValueError, match="unknown commodity id 'z'"):
        _port_sim(events)


def test_stock_target_raises_inflow_and_stock_during_window_and_returns_after() -> None:
    events = [{"day": 5, "type": "stock_target", "node_id": "reg", "commodity_id": "x",
               "factor": 2.0, "until_day": 9}]  # fmt: skip
    sim = chain_sim(events, horizon=20)
    initial_stock = sim.state.stocks[("reg", "x")]
    states = [sim.step() for _ in range(20)]
    result = sim.result()
    # Baseline: target (2 + 3) x 10 = 50, end-of-day stock 20 with 30 t in transit, 10 t/day sent.
    # Window: target 100, source supply capped at 30 t/day. Need = 100 - stock - in transit:
    # d5 100-20-20 = 60 -> 30; d6 100-20-40 = 40 -> 30; d7 100-20-60 = 20; d8 100-40-50 = 10;
    # d9 100-60-30 = 10; from d10 the target is 50 again and stock 70 > 50, so nothing is sent.
    sent = result.edges_daily[result.edges_daily["edge_id"] == "e1"].set_index("day")["flow"]
    assert list(sent.iloc[:15]) == pytest.approx([10] * 5 + [30, 30, 20, 10, 10] + [0] * 5)
    stock = result.daily.set_index("day")["stock"]
    # Arrivals 3 days after dispatch: d8 +30, d9 +30, d10 +20, d11 +10, d12 +10, consumption 10.
    assert list(stock.iloc[4:14]) == pytest.approx([20, 20, 20, 20, 40, 60, 70, 70, 70, 60])
    assert stock.iloc[19] == pytest.approx(20)  # back to the baseline level
    targets = [s.target_stocks[("reg", "x")] for s in states]
    assert targets[4] == 50 and targets[5:10] == [100.0] * 5 and targets[10] == 50
    assert initial_stock == 50.0  # initial stock is not scaled


def test_stock_target_wildcard_scales_regions_and_hubs() -> None:
    base = hub_star_sim(close_port_day=None, horizon=6)
    events = [{"day": 0, "type": "stock_target", "node_id": "*", "commodity_id": "medicine",
               "factor": 2.0, "until_day": 2}]  # fmt: skip
    sim = Simulation(base.network, _scenario_h(events, 6), base.policy, base.commodities)
    first = sim.step()
    # Hub target (7 + 1) x 30 = 240 -> 480; r1 (2 + 1) x 10 = 30 -> 60 (r2 80, r3 100).
    assert first.target_stocks[("H", "medicine")] == 480.0
    assert [first.target_stocks[(f"r{i}", "medicine")] for i in (1, 2, 3)] == [60, 80, 100]
    # Region needs 40 + 50 + 60 = 150 <= hub surplus 240 - 30 -> hub stock 90.
    assert first.stocks[("H", "medicine")] == pytest.approx(90.0)
    # Hub need 480 - 240 = 240, port supplies 100 t/day: arrives on day 1 (baseline: nothing).
    result = sim.run()
    hub = result.daily[result.daily["node_id"] == "H"].set_index("day")
    reg1 = result.daily[result.daily["node_id"] == "r1"].set_index("day")
    assert hub.loc[1, "inbound"] == pytest.approx(100.0)
    assert reg1.loc[1, "inbound"] == pytest.approx(40.0)  # r1 stock 20 -> 20 + 40 - 10 = 50
    assert reg1.loc[1, "stock"] == pytest.approx(50.0)
    plain = hub_star_sim(close_port_day=None, horizon=6).run().daily
    assert plain[(plain["node_id"] == "H") & (plain["day"] == 1)]["inbound"].iloc[0] == 0.0
    after = Simulation(base.network, _scenario_h(events, 6), base.policy, base.commodities)
    for _ in range(4):
        state = after.step()
    assert state.target_stocks[("H", "medicine")] == 240.0
    assert state.target_stocks[("r1", "medicine")] == 30.0


def test_stock_target_without_commodity_applies_to_all_and_validates_target() -> None:
    events = [{"day": 0, "type": "stock_target", "node_id": "*", "factor": 3.0, "until_day": 0}]
    mods = active_modifiers(_scenario_h(events, 3), 0, ["r1"], ["h1"])
    assert mods.target_factor == {("r1", None): 3.0, ("h1", None): 3.0}
    bad = [{"day": 0, "type": "stock_target", "node_id": "P", "factor": 2.0, "until_day": 1}]
    with pytest.raises(ValueError, match="not a region or hub node"):
        _port_sim(bad)
    bad_c = [{"day": 0, "type": "stock_target", "node_id": "R", "commodity_id": "z",
              "factor": 2.0, "until_day": 1}]  # fmt: skip
    with pytest.raises(ValueError, match="unknown commodity id 'z'"):
        _port_sim(bad_c)
