from pathlib import Path

from streamlit.testing.v1 import AppTest

from solmu.core.loader import load_network
from solmu.core.model import EdgeCapacityEvent, Scenario
from solmu.ui import runs

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "solmu" / "ui" / "app.py")
FOOTER = "Prototype. Network and demand are approximate, built from open data; see SOURCES.md."


def _button(at: AppTest, key: str):
    return next(b for b in at.button if b.key == key)


def _footer_present(at: AppTest) -> bool:
    return any(FOOTER in m.value for m in at.markdown)


def _map_figure(at: AppTest):
    return next(iter(at.session_state["solmu_figs"].values()))


def test_app_loads_with_footer() -> None:
    at = AppTest.from_file(APP).run(timeout=60)
    assert not at.exception
    assert _footer_present(at)


def test_run_to_end_then_comparison() -> None:
    at = AppTest.from_file(APP).run(timeout=60)
    _button(at, "solmu_run").click().run(timeout=60)
    assert not at.exception
    assert _footer_present(at)
    assert len(at.session_state["solmu_results"]) == 1
    at.selectbox(key="solmu_pol_b").select("no_stockpiling").run(timeout=60)
    _button(at, "solmu_run").click().run(timeout=60)
    assert not at.exception
    assert set(at.session_state["solmu_results"]) == {"A", "B"}
    fig = _map_figure(at)
    assert len(fig.frames) == at.session_state["solmu_results"]["A"].meta["days_simulated"]
    assert {t.geo for t in fig.data} == {"geo", "geo2"}


def test_map_figure_has_one_frame_per_day() -> None:
    at = AppTest.from_file(APP).run(timeout=60)
    _button(at, "solmu_run").click().run(timeout=60)
    assert not at.exception
    meta = at.session_state["solmu_results"]["A"].meta
    days = meta["days_simulated"]
    fig = _map_figure(at)
    assert days == meta["horizon_days"]  # "Run" goes to the end of whichever scenario is selected
    assert len(fig.frames) == days
    assert len(fig.layout.sliders[0].steps) == days
    assert fig.layout.updatemenus[0].buttons[0].label.startswith("Play")


def test_step_one_day_and_queue_action() -> None:
    at = AppTest.from_file(APP).run(timeout=60)
    _button(at, "solmu_step").click().run(timeout=60)
    assert not at.exception
    assert at.session_state["solmu_results"]["A"].meta["days_simulated"] == 1
    _button(at, "solmu_ac_go").click().run(timeout=60)
    assert not at.exception
    assert at.session_state["solmu_queued"]
    _button(at, "solmu_rm_0").click().run(timeout=60)
    assert not at.exception
    assert not at.session_state["solmu_queued"]


def test_stress_scenario_runs_with_hub_rows() -> None:
    at = AppTest.from_file(APP).run(timeout=60)
    at.selectbox(key="solmu_scen").select("gulf_of_finland_ice").run(timeout=60)
    at.selectbox(key="solmu_commodity").select("medicine").run(timeout=60)
    _button(at, "solmu_run").click().run(timeout=60)
    assert not at.exception
    result = at.session_state["solmu_results"]["A"]
    assert result.summary["backlog"]["medicine"] > 0
    assert any("Hub stock" in c.value for c in at.caption)


def test_add_capacity_on_closed_edge_warns(monkeypatch) -> None:
    edge = load_network(runs.DATA_DIR / "finland").edges[0].id
    closed = Scenario(
        id="closed_edge",
        name="Closed edge",
        horizon_days=10,
        events=[
            EdgeCapacityEvent(type="edge_capacity", edge_id=edge, day=0, until_day=9, factor=0.0)
        ],
    )
    monkeypatch.setattr(runs, "load_scenarios", lambda: {"closed_edge": closed})
    at = AppTest.from_file(APP).run(timeout=60)
    assert not at.exception
    at.selectbox(key="solmu_ac_edge").select(edge).run(timeout=60)
    assert not at.exception
    assert any("is closed by an event" in w.value for w in at.warning)
    _button(at, "solmu_ac_go").click().run(timeout=60)
    assert not at.exception
    assert any("is closed by an event" in w.value for w in at.warning)
    assert at.session_state["solmu_queued"]
