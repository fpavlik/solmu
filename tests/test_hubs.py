"""Hub stock, catchments and two-tier allocation on synthetic networks.

Chain: port P (medicine 40 t/day) -> hub H (holds medicine) -> region A (10 t/day, 1 day)
and region B (20 t/day, 2 days). Regions keep 2 days of cover, hubs 7 days, so
L_hub = 1, hub target = (7 + 1) x 30 = 240 t, A target = (2 + 1) x 10 = 30 t and
B target = (2 + 2) x 20 = 80 t. Hub safety stock = 1 day of served demand = 30 t.
"""

from pathlib import Path

import pytest

from solmu.core.engine import Simulation
from solmu.core.loader import (
    load_commodities,
    load_network,
)
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

FIXTURES = Path(__file__).parent / "fixtures" / "hubs"
POLICY = Policy(id="p", name="P", release=ReleaseRule(trigger="manual"))


def _edge(eid: str, a: str, b: str, days: int) -> Edge:
    return Edge(id=eid, from_node=a, to_node=b, mode="road", capacity_per_day=1000,
                transit_days=days, cost_per_tonne=1.0)  # fmt: skip


def _network(tags: dict[str, str]) -> Network:
    return Network(
        nodes=[
            Node(id="P", name="P", kind="port", lat=0, lon=0),
            Node(id="H", name="H", kind="hub", lat=0, lon=1, tags=tags),
            Node(id="A", name="A", kind="region", lat=0, lon=2, population=100),
            Node(id="B", name="B", kind="region", lat=0, lon=3, population=200),
        ],
        edges=[_edge("PH", "P", "H", 1), _edge("HA", "H", "A", 1), _edge("HB", "H", "B", 2)],
        demand=[
            DemandRecord(node_id="A", commodity_id="medicine", tonnes_per_day=10),
            DemandRecord(node_id="B", commodity_id="medicine", tonnes_per_day=20),
        ],
        supply=[SupplyRecord(node_id="P", commodity_id="medicine", tonnes_per_day=40)],
    )


def _sim(via_hubs: bool, tags: dict[str, str], events: list[dict] | None = None, horizon=20):
    commodities = Commodities(
        commodities=[Commodity(id="medicine", name="M", priority=1, via_hubs=via_hubs)],
        default_stock_days=2,
    )
    scenario = Scenario.model_validate(
        {"id": "s", "name": "S", "horizon_days": horizon, "events": events or []}
    )
    return Simulation(_network(tags), scenario, POLICY, commodities)


HOLDS = {"holds": "medicine"}
CLOSE_PORT = [{"day": 5, "type": "node_capacity", "node_id": "P", "factor": 0.0, "until_day": 99}]


def test_catchment_and_initial_stock_equal_target() -> None:
    sim = _sim(True, HOLDS)
    assert sim.catchments == {"medicine": {"A": "H", "B": "H"}}
    assert sim.lead_times[("A", "medicine")] == 1
    assert sim.lead_times[("B", "medicine")] == 2
    assert sim.state.stocks[("H", "medicine")] == 240.0
    assert sim.state.target_stocks[("H", "medicine")] == 240.0
    assert sim.state.stocks[("A", "medicine")] == 30.0
    assert sim.state.stocks[("B", "medicine")] == 80.0


def test_fixture_catchments_two_hubs_and_loader_fields() -> None:
    network = load_network(FIXTURES)
    commodities = load_commodities(FIXTURES / "commodities.json")
    assert [s.max_tonnes_per_day for s in network.supply] == [60, None, None]
    scenario = Scenario(id="s", name="S", horizon_days=3)
    sim = Simulation(network, scenario, POLICY, commodities)
    assert sim.catchments == {"medicine": {"r1": "h1", "r2": "h2"}}
    # hub_stock_days 5 (commodity override), L_hub 1: h1 serves 10 t, h2 serves 20 t.
    assert sim.state.stocks[("h1", "medicine")] == 60.0
    assert sim.state.stocks[("h2", "medicine")] == 120.0
    assert ("h1", "food") not in sim.state.stocks  # food is not via_hubs: h1 stays pass-through


def test_steady_state_hub_stock_and_rows() -> None:
    result = _sim(True, HOLDS, horizon=8).run()
    daily = result.daily
    hub = daily[daily["node_id"] == "H"].set_index("day")
    # Hub ships 30 t/day to the regions and is refilled the next day: 240 -30 = 210 on day 0,
    # then 180 at the end of every later day.
    assert list(hub["stock"].iloc[:4]) == pytest.approx([210, 180, 180, 180])
    assert (hub[["demand", "consumed", "unmet"]] == 0).all().all()
    assert result.summary["total_unmet"] == 0.0
    assert result.summary["population_days_in_shortage"] == 0.0


def test_port_closure_regions_served_from_hub_until_it_hits_safety_stock() -> None:
    sim = _sim(True, HOLDS, CLOSE_PORT, horizon=20)
    result = sim.run()
    daily = result.daily
    hub = daily[daily["node_id"] == "H"].set_index("day")["stock"]
    # Last import (dispatched day 4) lands day 5: 180 + 30 = 210, then 30 t leave per day:
    # end of day 5..10 = 180, 150, 120, 90, 60, 30. At 30 t (= safety) nothing more ships.
    assert list(hub.loc[5:12]) == pytest.approx([180, 150, 120, 90, 60, 30, 30, 30])
    # Regions: last shipments left on day 10 (A lands day 11, B day 12). A holds 20 t at the
    # end of day 11 and uses 10, 10, then is short from day 14; B holds 40 t at the end of
    # day 12 and is short from day 15 (longer lead time, larger pipeline buffer).
    assert result.summary["first_shortage_day"] == {"medicine": 14}
    unmet = daily[daily["unmet"] > 0].groupby("node_id")["day"].min()
    assert unmet.to_dict() == {"A": 14, "B": 15}


def test_no_holds_tag_or_not_via_hubs_equals_pass_through() -> None:
    passthrough = _sim(False, {}, CLOSE_PORT, horizon=15).run()
    tagged_but_direct = _sim(False, HOLDS, CLOSE_PORT, horizon=15).run()
    assert set(passthrough.daily["node_id"]) == {"A", "B"}
    assert passthrough.daily.equals(tagged_but_direct.daily)
    assert passthrough.edges_daily.equals(tagged_but_direct.edges_daily)


def test_via_hubs_without_a_holding_hub_is_rejected() -> None:
    with pytest.raises(ValueError, match="via_hubs"):
        _sim(True, {})


def test_demand_record_on_hub_is_rejected() -> None:
    network = _network(HOLDS)
    bad = network.model_copy(
        update={"demand": [*network.demand, DemandRecord(node_id="H", commodity_id="medicine",
                                                         tonnes_per_day=1)]}
    )  # fmt: skip
    commodities = Commodities(commodities=[Commodity(id="medicine", name="M", priority=1)])
    with pytest.raises(ValueError, match="only region nodes"):
        Simulation(bad, Scenario(id="s", name="S", horizon_days=2), POLICY, commodities)
