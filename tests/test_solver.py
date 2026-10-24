"""Solver tests on small hand-checkable graphs."""

import pytest

from solmu.core.model import Commodity
from solmu.core.solver import (
    EffectiveArc,
    EffectiveGraph,
    GreedySolver,
    MinCostFlowSolver,
    decompose_flows,
    scale_cost,
    scale_tonnes,
    unscale_tonnes,
)

COMMODITY = Commodity(id="x", name="X", priority=1)
SOLVERS = [GreedySolver, MinCostFlowSolver]


def arc(id: str, a: str, b: str, cap: float, cost: float = 1.0, transit: int = 1) -> EffectiveArc:
    return EffectiveArc(id, id, a, b, cap, cost, transit)


def diamond() -> EffectiveGraph:
    """A -> B -> D is cheap (cost 2) but holds 5 t; A -> C -> D is dear (cost 6)."""
    return EffectiveGraph(
        (
            arc("ab", "A", "B", 5, 1),
            arc("bd", "B", "D", 5, 1),
            arc("ac", "A", "C", 10, 3),
            arc("cd", "C", "D", 10, 3),
        )
    )


@pytest.mark.parametrize("solver", SOLVERS)
def test_cheap_route_preferred(solver) -> None:
    flows = solver().allocate(diamond(), {"A": 8.0}, {"D": 4.0}, COMMODITY)
    assert flows == {"ab": pytest.approx(4.0), "bd": pytest.approx(4.0)}


@pytest.mark.parametrize("solver", SOLVERS)
def test_spills_to_expensive_route_when_cheap_is_saturated(solver) -> None:
    flows = solver().allocate(diamond(), {"A": 8.0}, {"D": 8.0}, COMMODITY)
    assert flows["ab"] == pytest.approx(5.0)
    assert flows["bd"] == pytest.approx(5.0)
    assert flows["ac"] == pytest.approx(3.0)
    assert flows["cd"] == pytest.approx(3.0)


@pytest.mark.parametrize("solver", SOLVERS)
def test_flow_limited_by_supply_and_by_total_capacity(solver) -> None:
    assert sum(solver().allocate(diamond(), {"A": 2.0}, {"D": 8.0}, COMMODITY).values()) == (
        pytest.approx(4.0)  # 2 t over two arcs
    )
    flows = solver().allocate(diamond(), {"A": 100.0}, {"D": 100.0}, COMMODITY)
    assert flows["bd"] + flows["cd"] == pytest.approx(15.0)


@pytest.mark.parametrize("solver", SOLVERS)
def test_residual_capacity_shared_across_commodities(solver) -> None:
    graph = EffectiveGraph((arc("ad", "A", "D", 5.0),))
    first = solver().allocate(graph, {"A": 10.0}, {"D": 4.0}, COMMODITY)
    assert first == {"ad": pytest.approx(4.0)}
    second = solver().allocate(graph.reduced(first), {"A": 10.0}, {"D": 4.0}, COMMODITY)
    assert second == {"ad": pytest.approx(1.0)}
    third = solver().allocate(
        graph.reduced(first).reduced(second), {"A": 10.0}, {"D": 4.0}, COMMODITY
    )
    assert third == {}


@pytest.mark.parametrize("solver", SOLVERS)
def test_empty_and_infeasible_cases(solver) -> None:
    g = diamond()
    assert solver().allocate(g, {}, {"D": 5.0}, COMMODITY) == {}
    assert solver().allocate(g, {"A": 5.0}, {}, COMMODITY) == {}
    assert solver().allocate(g, {"D": 5.0}, {"A": 5.0}, COMMODITY) == {}  # no arc D -> A
    closed = EffectiveGraph((arc("ad", "A", "D", 0.0),))
    assert solver().allocate(closed, {"A": 5.0}, {"D": 5.0}, COMMODITY) == {}


@pytest.mark.parametrize("solver", SOLVERS)
def test_node_with_supply_and_need_is_netted(solver) -> None:
    graph = EffectiveGraph((arc("ab", "A", "B", 10.0), arc("ba", "B", "A", 10.0)))
    # B supplies 3 and needs 5: nets to a need of 2, so only 2 t move A -> B.
    flows = solver().allocate(graph, {"A": 10.0, "B": 3.0}, {"B": 5.0}, COMMODITY)
    assert flows == {"ab": pytest.approx(2.0)}


@pytest.mark.parametrize("solver", SOLVERS)
def test_parallel_arcs_use_cheaper_first(solver) -> None:
    graph = EffectiveGraph((arc("slow", "A", "B", 5.0, 5.0), arc("fast", "A", "B", 5.0, 1.0)))
    flows = solver().allocate(graph, {"A": 7.0}, {"B": 7.0}, COMMODITY)
    assert flows == {"fast": pytest.approx(5.0), "slow": pytest.approx(2.0)}


def test_min_cost_beats_greedy_when_greedy_blocks_a_cheaper_pairing() -> None:
    graph = EffectiveGraph(
        (
            arc("s1n1", "S1", "N1", 1.0, 1.0),
            arc("s1n2", "S1", "N2", 1.0, 2.0),
            arc("s2n1", "S2", "N1", 1.0, 2.0),
            arc("s2n2", "S2", "N2", 1.0, 10.0),
        )
    )
    args = ({"S1": 1.0, "S2": 1.0}, {"N1": 1.0, "N2": 1.0}, COMMODITY)

    def cost(flows: dict[str, float]) -> float:
        return sum(a.cost * flows.get(a.id, 0.0) for a in graph.arcs)

    assert cost(GreedySolver().allocate(graph, *args)) == pytest.approx(11.0)
    assert cost(MinCostFlowSolver().allocate(graph, *args)) == pytest.approx(4.0)


def test_scaling_round_trip_and_rounding() -> None:
    assert scale_tonnes(12.3) == 123
    assert unscale_tonnes(scale_tonnes(12.3)) == pytest.approx(12.3)
    assert scale_tonnes(0.29) == 2  # capacities round down: never exceed the true value
    assert scale_tonnes(0.09) == 0
    assert scale_cost(0.6 * 2) == 120
    for tonnes in (0.1, 0.7, 3.0, 1234.5):
        assert unscale_tonnes(scale_tonnes(tonnes)) == pytest.approx(tonnes)


def test_decompose_flows_into_paths() -> None:
    graph = diamond()
    flows = {"ab": 5.0, "bd": 5.0, "ac": 3.0, "cd": 3.0}
    paths = decompose_flows(graph, flows)
    assert sum(p.tonnes for p in paths) == pytest.approx(8.0)
    assert {(p.origin, p.destination) for p in paths} == {("A", "D")}
    assert sorted(p.arcs for p in paths) == [("ab", "bd"), ("ac", "cd")]
