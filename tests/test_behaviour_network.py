"""Hubs, import redirection, demand events and edge restrictions as stories."""

import pytest

from tests.helpers import (
    fast_slow_sim,
    first_unmet_days,
    hub_star_sim,
    port_pair_sim,
    star_sim,
    unmet_by_node,
)


def test_hub_holds_back_one_day_of_served_demand_while_regions_run_short() -> None:
    # Hub H serves r1..r3 (3 x 10 t/day). Target (7 + 1) x 30 = 240 t, start 240 t. The port
    # closes on day 3; the last import lands on day 4. From then the hub ships 30 t/day until
    # only the safety stock of one day of served demand (30 t) is left, and never ships it.
    sim = hub_star_sim()
    result = sim.run()
    daily = result.daily
    hub = daily[daily["node_id"] == "H"].set_index("day")["stock"]
    assert sim.state.stocks[("H", "medicine")] == pytest.approx(30.0)
    assert list(hub.iloc[:9]) == pytest.approx([210, 180, 180, 180, 150, 120, 90, 60, 30])
    assert hub.iloc[8:].min() == pytest.approx(30.0)
    first = first_unmet_days(result)
    assert first["r1"] < first["r2"] < first["r3"]
    # The regions are short on days when 30 t, enough for a day of everybody's demand, sit
    # in the hub: the safety stock is a floor of the allocation, not a reserve for crises.
    assert hub.loc[first["r1"]] == pytest.approx(30.0)
    assert sim.state.unmet[("r1", "medicine")] > 0


def test_regions_in_a_catchment_run_short_in_order_of_their_lead_time() -> None:
    # Lead times 1, 2, 3 days; stocks (2 + L) x 10 = 30, 40, 50 t. Longer lead time means a
    # bigger pipeline buffer, so the far region lasts longest: shortages on days 12, 13, 14.
    sim = hub_star_sim()
    assert [sim.lead_times[(f"r{i}", "medicine")] for i in (1, 2, 3)] == [1, 2, 3]
    assert first_unmet_days(sim.run()) == {"r1": 12, "r2": 13, "r3": 14}


def test_no_closure_no_shortage_with_a_hub() -> None:
    result = hub_star_sim(close_port_day=None).run()
    assert result.summary["total_unmet"] == 0.0
    assert result.summary["first_shortage_day"] == {"medicine": None}


def test_port_headroom_lowers_unmet_strictly() -> None:
    # Demand 20 t/day, ports A and B 10 t/day each, stock 60 t. A is closed on days 2-11.
    # Without headroom (max = baseline) only B ships 10 t/day: stock 40, 30, 20, 10, 0 on days
    # 2-6, short by 10 t on days 7-12 = 60 t. With max 15 t at B, the redirected 5 t/day flows
    # from day 4 (delay 2): stock 0 on day 8, short by 5 t on days 9-12 = 20 t.
    flat = port_pair_sim(max_b=10.0).run()
    headroom = port_pair_sim(max_b=15.0).run()
    assert flat.summary["total_unmet"] == pytest.approx(60.0)
    assert headroom.summary["total_unmet"] == pytest.approx(20.0)
    assert port_pair_sim(max_b=20.0).run().summary["total_unmet"] == pytest.approx(0.0)
    assert flat.summary["first_shortage_day"] == {"x": 7}
    assert headroom.summary["first_shortage_day"] == {"x": 9}


def test_backlog_loss_fraction_destroys_cargo_but_unmet_is_set_by_spare_capacity() -> None:
    # The spare capacity at B (5 t/day) is below the available backlog on every day, so how much
    # is redirected does not depend on what is lost: unmet is equal, `import_lost` is not.
    keep = port_pair_sim(max_b=15.0, loss=0.0).run()
    lose = port_pair_sim(max_b=15.0, loss=0.5).run()
    assert keep.summary["import_lost_by_commodity"] == {"x": 0.0}
    assert lose.summary["import_lost_by_commodity"]["x"] > 0
    assert lose.summary["total_unmet"] == pytest.approx(keep.summary["total_unmet"])


def test_loss_lowers_the_waiting_backlog() -> None:
    keep = port_pair_sim(max_b=15.0, loss=0.0).run()
    lose = port_pair_sim(max_b=15.0, loss=0.5).run()
    assert lose.summary["backlog"]["x"] < keep.summary["backlog"]["x"]


def test_longer_redirect_delay_never_reduces_unmet() -> None:
    # Each extra day of delay delays the +5 t/day by a day: +5 t unmet (10 + 5 x delay).
    totals = [port_pair_sim(max_b=15.0, delay=d).run().summary["total_unmet"] for d in (0, 1, 4, 8)]
    assert totals == pytest.approx([10.0, 15.0, 30.0, 50.0])
    assert totals == sorted(totals)


WILDCARD = [{"day": 3, "type": "demand", "node_id": "*", "commodity_id": "x", "factor": 1.5,
             "until_day": 12}]  # fmt: skip
SINGLE = [{"day": 3, "type": "demand", "node_id": "r1", "commodity_id": "x", "factor": 1.5,
           "until_day": 12}]  # fmt: skip


def test_wildcard_demand_event_hits_every_region_a_single_one_only_its_own() -> None:
    # Each edge carries at most 10 t/day. In r1 steady usable stock is 20 t (10 t in transit);
    # demand 15 t/day on days 3-12 drains it by 5 t/day: 15, 10, 5, 0 on days 3-6, then short by
    # 5 t/day on days 7-12 = 30 t. Longer edges give the same by symmetry (edge caps bind).
    assert star_sim().run().summary["total_unmet"] == 0.0
    wide = star_sim(WILDCARD).run()
    narrow = star_sim(SINGLE).run()
    assert unmet_by_node(wide) == {
        "r1": pytest.approx(30.0),
        "r2": pytest.approx(30.0),
        "r3": pytest.approx(30.0),
    }
    assert unmet_by_node(narrow) == {
        "r1": pytest.approx(30.0),
        "r2": 0.0,
        "r3": 0.0,
    }
    assert first_unmet_days(wide) == {"r1": 7, "r2": 7, "r3": 7}
    assert first_unmet_days(narrow) == {"r1": 7}
    assert wide.summary["total_unmet"] == pytest.approx(3 * narrow.summary["total_unmet"])
    # Population-days: r1 100, r2 200, r3 300 people for 6 short days each vs r1 alone.
    assert wide.summary["population_days_in_shortage"] == pytest.approx(6 * 600)
    assert narrow.summary["population_days_in_shortage"] == pytest.approx(6 * 100)


def test_edge_restricted_to_fuel_sends_food_the_long_way_with_a_longer_lead_time() -> None:
    restricted = fast_slow_sim({"fuel"})
    open_edge = fast_slow_sim(None)
    # Fuel keeps the 1-day edge; food (not allowed there) takes the 3-day edge.
    assert restricted.lead_times == {("R", "fuel"): 1, ("R", "food"): 3}
    assert open_edge.lead_times == {("R", "fuel"): 1, ("R", "food"): 1}
    # Food's buffer grows with its lead time: (2 + 3) x 20 = 100 t instead of (2 + 1) x 20 = 60 t.
    assert restricted.state.stocks[("R", "food")] == 100.0
    assert open_edge.state.stocks[("R", "food")] == 60.0

    edges = restricted.run().edges_daily
    flow = {e: list(edges[edges["edge_id"] == e]["flow"]) for e in ("fast", "slow")}
    assert flow["fast"] == pytest.approx([10.0] * 8)
    assert flow["slow"] == pytest.approx([20.0] * 8)
    edges = open_edge.run().edges_daily
    assert list(edges[edges["edge_id"] == "fast"]["flow"]) == pytest.approx([30.0] * 8)
    assert list(edges[edges["edge_id"] == "slow"]["flow"]) == pytest.approx([0.0] * 8)
