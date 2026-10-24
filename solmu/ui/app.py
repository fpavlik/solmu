"""Streamlit client for the resilience simulator: `streamlit run solmu/ui/app.py`."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from solmu.core.actions import (
    Action,
    ActionContext,
    ActionError,
    AddCapacity,
    PrioritizeCommodity,
    ReleaseReserve,
    validate_actions,
)
from solmu.core.engine import Simulation
from solmu.core.model import Policy, Scenario
from solmu.core.results import RunResult
from solmu.core.state import State
from solmu.ui.charts import region_chart, timeline_chart, unmet_chart, where_chart
from solmu.ui.maps import MapRun, build_map_figure
from solmu.ui.runs import (
    Country,
    list_countries,
    load_country,
    load_policies,
    load_scenarios,
    make_simulation,
)
from solmu.ui.tables import (
    capacity_warnings,
    edge_frame,
    event_rows,
    hub_stock_frame,
    import_row,
    node_frame,
    region_rows,
    region_series,
    releases_on_day,
    shortage_changes,
    stock_breakdown,
    target_frame,
)

FOOTER = "Prototype. Network and demand are approximate, built from open data; see SOURCES.md."
FOOTER_CSS = """
<style>
.block-container { padding-bottom: 4.5rem; }
.solmu-footer {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 1000;
  padding: 0.4rem 1rem; font-size: 0.85rem;
  background: rgba(128, 128, 128, 0.18); color: inherit;
  border-top: 1px solid rgba(128, 128, 128, 0.4); backdrop-filter: blur(6px);
}
</style>
"""
SIMS = "solmu_sims"
RESULTS = "solmu_results"
STATES = "solmu_states"
SIG = "solmu_sig"
RUN_ID = "solmu_run_id"
DAY = "solmu_day"
MSG = "solmu_msg"
QUEUED = "solmu_queued"
FIGS = "solmu_figs"


def _dark() -> bool:
    try:
        return st.context.theme.type == "dark"
    except (AttributeError, RuntimeError):
        return st.get_option("theme.base") == "dark"


def _post(level: str, text: str) -> None:
    st.session_state[MSG].append((level, text))


def _refresh_results() -> None:
    st.session_state[RESULTS] = {k: s.result() for k, s in st.session_state[SIMS].items()}
    st.session_state[RUN_ID] += 1
    shortest = min(r.meta["days_simulated"] for r in st.session_state[RESULTS].values())
    st.session_state[DAY] = max(shortest - 1, 0)


def _on_reset() -> None:
    st.session_state[SIG] = None


def _advance(to_end: bool) -> None:
    """Apply the queued actions to run A, then step every run (one day or to the horizon)."""
    st.session_state[MSG] = []
    actions = [q["action"] for q in st.session_state[QUEUED]]
    sim_a: Simulation = st.session_state[SIMS]["A"]
    if actions:
        try:
            sim_a.apply(actions)
        except ActionError as exc:
            _post("error", _bullets(exc.problems))
            return
    for key, sim in st.session_state[SIMS].items():
        while not sim.done:
            st.session_state[STATES][key].append(sim.step())
            if not to_end:
                break
    st.session_state[QUEUED] = []
    _refresh_results()


def _on_run_to_end() -> None:
    _advance(True)


def _on_step() -> None:
    _advance(False)


def _bullets(problems: list[str]) -> str:
    return "\n".join(f"- {p}" for p in problems)


def _pending_cost() -> float:
    return sum(q["cost"] for q in st.session_state[QUEUED])


def _context(sim: Simulation) -> ActionContext:
    """Facts for validating actions, read from public attributes of the simulation."""
    return ActionContext(
        policy=sim.policy,
        day=sim.day,
        budget=sim.state.budget,
        region_ids=frozenset(n.id for n in sim.network.nodes if n.kind == "region"),
        edge_ids=frozenset(e.id for e in sim.network.edges),
        commodity_ids=frozenset(c.id for c in sim.commodities.commodities),
        strategic_stocks=sim.state.strategic_stocks,
        holder_ids=frozenset(node for node, _ in sim.state.strategic_stocks),
    )


def _on_queue(action: Action, label: str, warnings: list[str]) -> None:
    st.session_state[MSG] = []
    sim_a: Simulation = st.session_state[SIMS]["A"]
    pending = [q["action"] for q in st.session_state[QUEUED]]
    problems = validate_actions([*pending, action], _context(sim_a))
    if problems:
        _post("error", _bullets(problems))
        return
    cost = sim_a.action_cost(action)
    st.session_state[QUEUED].append({"label": label, "action": action, "cost": cost})
    _post("success", f"Queued for the next step: {label}")
    for text in warnings:
        _post("warning", text)


def _on_remove(index: int) -> None:
    st.session_state[MSG] = []
    del st.session_state[QUEUED][index]


def _shift_day(delta: int, last: int) -> None:
    st.session_state[DAY] = min(max(st.session_state[DAY] + delta, 0), last)


def ensure_simulations(country: Country, scenario_id: str, a: str, b: str | None, seed: int):
    """(Re)create the live simulations when the selection changed or Reset was pressed."""
    sig = (scenario_id, a, b, seed, id(country))
    if st.session_state.get(SIG) == sig:
        return
    scenarios, policies = load_scenarios(), load_policies()
    sims = {"A": make_simulation(country, scenarios[scenario_id], policies[a], seed)}
    if b is not None:
        sims["B"] = make_simulation(country, scenarios[scenario_id], policies[b], seed)
    st.session_state[SIG] = sig
    st.session_state[SIMS] = sims
    st.session_state[STATES] = {k: [s.state] for k, s in sims.items()}
    st.session_state[RESULTS] = {}
    st.session_state[RUN_ID] = st.session_state.get(RUN_ID, 0) + 1
    st.session_state[QUEUED] = []
    st.session_state[MSG] = []
    st.session_state[DAY] = 0


def action_controls(country: Country, sim: Simulation, result: RunResult | None) -> None:
    """Forms for the three actions; they apply to run A only."""
    names = {n.id: n.name for n in country.network.nodes}
    regions = [n.id for n in country.network.nodes if n.kind == "region"]
    edges = {
        e.id: f"{e.id} ({names[e.from_node]} - {names[e.to_node]})" for e in country.network.edges
    }
    commodities = {c.id: c.name for c in country.commodities.commodities}
    disabled = sim.done
    day = st.session_state[DAY]
    st.caption("Actions apply to run A only and take effect at the next step.")
    queued = st.session_state[QUEUED]
    remaining = sim.state.budget - _pending_cost()
    st.write(f"Budget remaining: **{remaining:,.0f}** (of {sim.state.budget:,.0f} now)")
    if queued:
        st.write(f"Queued ({len(queued)}), cost {_pending_cost():,.0f}:")
        for i, item in enumerate(queued):
            text, button = st.columns([5, 1])
            text.caption(f"{item['label']} (cost {item['cost']:,.0f})")
            button.button("x", key=f"solmu_rm_{i}", on_click=_on_remove, args=(i,), help="Remove")

    with st.expander("Release reserve"):
        # Wherever the policy keeps strategic stock: regions, hubs or source nodes.
        held_at = {node for (node, _), t in sim.state.strategic_stocks.items() if t > 0}
        holders = [*regions, *sorted(held_at - set(regions))]
        region = st.selectbox(
            "Node", holders, format_func=lambda i: f"{i} {names[i]}", key="solmu_rr_region"
        )
        commodity = st.selectbox(
            "Commodity", list(commodities), format_func=commodities.get, key="solmu_rr_commodity"
        )
        held = float(sim.state.strategic_stocks.get((region, commodity), 0.0))
        already = sum(
            q["action"].tonnes
            for q in queued
            if isinstance(q["action"], ReleaseReserve)
            and (q["action"].node_id, q["action"].commodity_id) == (region, commodity)
        )
        room = max(held - already, 0.0)
        st.caption(f"Max: {room:,.1f} t (strategic stock {held:,.1f} t, {already:,.1f} t queued)")
        key = f"solmu_rr_t_{region}_{commodity}"
        # The value lives in session state only: a widget given both a `value` and a session
        # state entry makes Streamlit log a warning on every rerun.
        if key not in st.session_state:
            st.session_state[key] = min(10.0, room)
        elif st.session_state[key] > room:
            st.session_state[key] = room
        tonnes = st.number_input(
            "Tonnes",
            min_value=0.0,
            max_value=max(room, 0.0),
            step=10.0,
            key=key,
            disabled=room <= 0,
        )
        action = ReleaseReserve(region, commodity, float(tonnes))
        st.button(
            "Release",
            key="solmu_rr_go",
            disabled=disabled or room <= 0 or tonnes <= 0,
            on_click=_on_queue,
            args=(action, f"release {tonnes:,.0f} t {commodity} at {region}", []),
        )

    with st.expander("Add capacity"):
        edge = st.selectbox("Edge", list(edges), format_func=edges.get, key="solmu_ac_edge")
        extra = st.number_input(
            "Extra t/day", min_value=0.0, value=1000.0, step=500.0, key="solmu_ac_extra"
        )
        days = st.number_input("Days", min_value=1, value=7, step=1, key="solmu_ac_days")
        action = AddCapacity(edge, float(extra), int(days))
        cost = sim.action_cost(action)
        st.write(f"Cost: **{cost:,.0f}**, budget remaining after: **{remaining - cost:,.0f}**")
        warnings = capacity_warnings(sim.scenario, regions, result, edge, [day, sim.day])
        for text in warnings:
            st.warning(text)
        st.button(
            "Add capacity",
            key="solmu_ac_go",
            disabled=disabled,
            on_click=_on_queue,
            args=(action, f"+{extra:,.0f} t/day on {edge} for {days} d", warnings),
        )

    with st.expander("Prioritize commodity"):
        commodity = st.selectbox(
            "Commodity",
            list(commodities),
            format_func=commodities.get,
            key="solmu_pc_commodity",
        )
        days = st.number_input("Days", min_value=1, value=7, step=1, key="solmu_pc_days")
        action = PrioritizeCommodity(commodity, int(days))
        st.button(
            "Prioritize",
            key="solmu_pc_go",
            disabled=disabled,
            on_click=_on_queue,
            args=(action, f"prioritize {commodity} for {days} d", []),
        )

    shown = {"error": st.error, "warning": st.warning, "success": st.success}
    for level, text in st.session_state[MSG]:
        shown[level](text)


def header(scenario: Scenario, policies: dict[str, Policy]) -> None:
    """Scenario name and description, then a compact table of the policies compared."""
    st.title("Supply-chain resilience simulator")
    st.markdown(f"**Scenario: {scenario.name}** ({scenario.horizon_days} days)")
    if scenario.description:
        st.caption(scenario.description)
    rows = []
    for label, p in policies.items():
        r = p.release
        rows.append(
            {
                "run": label,
                "policy": p.name,
                "min stock days": " / ".join(f"{c} {d:g}" for c, d in p.min_stock_days.items()),
                "release trigger": r.trigger
                + (
                    f" (drop > {r.disruption_threshold:.0%}, {r.disruption_persistence_days} d)"
                    if r.trigger == "on_disruption"
                    else ""
                ),
                "delay (d)": r.delay_days,
                "max fraction/day": r.max_release_fraction_per_day,
                "budget": f"{p.emergency_budget:,.0f}",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def metrics_panel(
    sim: Simulation,
    result: RunResult,
    day: int,
    nodes: pd.DataFrame,
    names: dict,
    commodity: str,
    commodity_label: str,
) -> None:
    """Shortage metrics for one run on the metrics day plus whole-run cost figures."""
    summary = result.summary
    daily = region_rows(result.daily, nodes)
    today = daily[(daily["day"] == day) & (daily["unmet"] > 0)]
    short_regions = sorted(today["node_id"].unique())
    to_date = daily[daily["day"] <= day]["unmet"].sum()
    c1, c2, c3 = st.columns(3)
    c1.metric(f"Regions in shortage, day {day}", len(short_regions))
    c2.metric(f"Unmet to day {day} (t)", f"{to_date:,.0f}")
    c3.metric("Budget remaining (current)", f"{sim.state.budget:,.0f}")
    st.caption(
        f"Follow the metrics day ({day}): regions in shortage, unmet to date. "
        "Cover the whole run so far: budget, the table below and the cost line."
    )
    if short_regions:
        st.caption(", ".join(f"{r} {names[r]}" for r in short_regions))
    first = pd.DataFrame(
        {
            "commodity": list(summary["first_shortage_day"]),
            "first shortage day": [
                "-" if d is None else str(d) for d in summary["first_shortage_day"].values()
            ],
            "total unmet (t)": [
                f"{summary['total_unmet_by_commodity'][c]:,.0f}"
                for c in summary["first_shortage_day"]
            ],
        }
    )
    st.dataframe(first, hide_index=True, width="stretch")
    b1, b2 = st.columns(2)
    b1.metric(
        f"Peak import backlog, {commodity_label} (t)",
        f"{summary['backlog'].get(commodity, 0.0):,.0f}",
    )
    b2.metric(
        f"Import lost, {commodity_label} (t)",
        f"{summary['import_lost_by_commodity'].get(commodity, 0.0):,.0f}",
    )
    row = import_row(result, day, commodity)
    if row is not None:
        st.metric(f"Import drop, {commodity_label}, day {day}", f"{float(row['drop']):.0%}")
    hubs = hub_stock_frame(nodes, result, day, commodity)
    if not hubs.empty:
        st.caption(f"Hub stock, {commodity_label}, day {day}")
        st.dataframe(
            pd.DataFrame(
                {"hub": hubs["id"] + " " + hubs["name"], "stock (t)": hubs["stock"].round(0)}
            ),
            hide_index=True,
            width="stretch",
        )
    st.caption(
        f"Whole run so far (days 0-{result.meta['days_simulated'] - 1}, not metrics day): "
        f"holding cost {summary['holding_cost']:,.0f}, "
        f"action spend {summary['action_spend']:,.0f}, "
        f"total cost {summary['total_cost']:,.0f}; "
        f"total unmet {summary['total_unmet']:,.0f} t. "
        "Unmet demand is not priced in total cost."
    )


def _list(items: list[str], empty: str) -> None:
    if items:
        st.markdown("\n".join(f"- {i}" for i in items))
    else:
        st.caption(empty)


def day_log_panel(
    sim: Simulation,
    result: RunResult,
    states: list[State],
    nodes: pd.DataFrame,
    day: int,
    commodity: str,
    commodity_label: str,
) -> None:
    """What happened on the metrics day in one run."""
    names = dict(zip(nodes["id"], nodes["name"], strict=True))
    events = event_rows(sim.scenario, sim.network)
    st.markdown(f"**Day {day}**")
    starting = [
        f"{e.kind}: {e.label} x{e.factor:g} (to day {e.last_day})"
        for e in events
        if e.first_day == day
    ]
    ending = [
        f"{e.kind}: {e.label} x{e.factor:g} (from day {e.first_day})"
        for e in events
        if e.last_day == day
    ]
    st.markdown("Events starting")
    _list(starting, "none")
    st.markdown("Events ending (last active day)")
    _list(ending, "none")

    releases = releases_on_day(result, states, nodes, day)
    st.markdown("Strategic stock released (policy or manual)")
    if releases.empty:
        st.caption("none")
    else:
        grouped = releases.groupby("commodity_id").agg(
            regions=("node_id", "count"), tonnes=("tonnes", "sum")
        )
        st.dataframe(grouped.round(1), width="stretch")
        with st.expander("By region"):
            show = releases[["region", "commodity_id", "tonnes"]].round(1)
            st.dataframe(show, hide_index=True, width="stretch")

    applied = [a for a in result.actions_log if a["day"] == day]
    st.markdown("Actions applied")
    _list(
        [f"{a['action']} {a['params']} (cost {a['cost']:,.0f})" for a in applied],
        "none",
    )

    entering, leaving = shortage_changes(result, nodes, day)
    st.markdown("Regions entering shortage")
    _list([f"{names[r]} ({c})" for r, c in entering], "none")
    st.markdown("Regions leaving shortage")
    _list([f"{names[r]} ({c})" for r, c in leaving], "none")

    row = import_row(result, day, commodity)
    st.markdown(f"Imports, {commodity_label}")
    if row is None:
        st.caption("no import data for this day")
    else:
        st.write(
            f"drop {float(row['drop']):.0%}, offer {float(row['offer']):,.0f} t of baseline "
            f"{float(row['baseline']):,.0f} t, backlog {float(row['backlog']):,.0f} t, "
            f"lost today {float(row['lost']):,.0f} t"
        )


def region_card(
    sim: Simulation,
    result: RunResult,
    states: list[State],
    targets: pd.DataFrame,
    nodes: pd.DataFrame,
    region: str,
    day: int,
    commodity: str,
    commodity_label: str,
    key: str,
    dark: bool,
) -> None:
    """Stock, target, reserve, demand, unmet, hub and lead time of one region on the metrics day."""
    names = dict(zip(nodes["id"], nodes["name"], strict=True))
    today = result.daily[(result.daily["node_id"] == region) & (result.daily["day"] == day)]
    tgt = targets[(targets["node_id"] == region) & (targets["day"] == day)]
    target = dict(zip(tgt["commodity_id"], tgt["target"], strict=True))
    lead, catchments = sim.lead_times, sim.catchments
    rows = []
    for r in today.itertuples():
        hub = catchments.get(r.commodity_id, {}).get(region)
        rows.append(
            {
                "commodity": r.commodity_id,
                "stock (t)": round(r.stock, 1),
                "target (t)": round(target.get(r.commodity_id, 0.0), 1),
                "strategic (t)": round(r.strategic_stock, 1),
                "demand (t)": round(r.demand, 1),
                "unmet (t)": round(r.unmet, 1),
                "hub": "direct" if hub is None else f"{hub} {names[hub]}",
                "lead time (d)": lead.get((region, r.commodity_id), 0),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    series = region_series(result, targets, region, commodity)
    st.plotly_chart(
        region_chart(series, f"{region} {names[region]}: {commodity_label}", day, dark),
        key=f"solmu_region_chart_{key}",
        theme=None,
    )


def main() -> None:
    """Page layout: sidebar controls, header, map, timeline and metrics tabs."""
    st.set_page_config(page_title="Supply-chain resilience simulator", layout="wide")
    st.markdown(FOOTER_CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="solmu-footer">{FOOTER}</div>', unsafe_allow_html=True)
    dark = _dark()

    scenarios, policies = load_scenarios(), load_policies()
    with st.sidebar:
        st.header("Setup")
        country_name = st.selectbox("Country", list_countries(), key="solmu_country")
        country = load_country(country_name)
        scenario_id = st.selectbox(
            "Scenario", list(scenarios), format_func=lambda i: scenarios[i].name, key="solmu_scen"
        )
        policy_a = st.selectbox(
            "Policy A", list(policies), format_func=lambda i: policies[i].name, key="solmu_pol_a"
        )
        policy_b = st.selectbox(
            "Policy B (optional, enables comparison)",
            [None, *policies],
            format_func=lambda i: "none" if i is None else policies[i].name,
            key="solmu_pol_b",
        )
        seed = int(st.number_input("Seed", min_value=0, value=0, step=1, key="solmu_seed"))
        commodity_names = {c.id: c.name for c in country.commodities.commodities}
        commodity = st.selectbox(
            "Map commodity",
            list(commodity_names),
            format_func=commodity_names.get,
            key="solmu_commodity",
        )
        ensure_simulations(country, scenario_id, policy_a, policy_b, seed)
        sims: dict[str, Simulation] = st.session_state[SIMS]
        st.button("Run to end", type="primary", on_click=_on_run_to_end, key="solmu_run")
        st.button("Step 1 day", on_click=_on_step, disabled=sims["A"].done, key="solmu_step")
        st.button("Reset", on_click=_on_reset, key="solmu_reset")
        st.caption(f"Simulated day: {sims['A'].day} / {scenarios[scenario_id].horizon_days}")
        st.header("Actions")
        action_controls(country, sims["A"], st.session_state[RESULTS].get("A"))

    chosen = {"A": policies[policy_a], **({"B": policies[policy_b]} if policy_b else {})}
    header(scenarios[scenario_id], chosen)

    results: dict[str, RunResult] = st.session_state[RESULTS]
    if not results:
        st.info("Press **Run to end** or **Step 1 day** in the sidebar to start the simulation.")
        return

    states: dict[str, list[State]] = st.session_state[STATES]
    days = min(r.meta["days_simulated"] for r in results.values())
    nodes, edges = node_frame(country.network), edge_frame(country.network)
    names = dict(zip(nodes["id"], nodes["name"], strict=True))
    labels = {k: f"{k}: {chosen[k].name}" for k in results}
    targets = {k: target_frame(states[k]) for k in results}

    figs = st.session_state.setdefault(FIGS, {})
    fig_key = (st.session_state[RUN_ID], commodity, dark)
    if fig_key not in figs:
        runs = [MapRun(labels[k], r, targets[k]) for k, r in results.items()]
        figs.clear()
        figs[fig_key] = build_map_figure(nodes, edges, runs, commodity, dark)
    st.plotly_chart(
        figs[fig_key],
        key="solmu_map",
        theme=None,
        width="stretch",
        config={
            "scrollZoom": False,
            "doubleClick": "reset",
            "displayModeBar": True,
            "modeBarButtonsToRemove": ["select2d", "lasso2d", "toImage"],
        },
    )
    st.caption(
        "Use the slider and Play/Pause inside the map: it scrubs in the browser and does not "
        "rerun the app. Circles: regions, green to red by unmet/demand of the selected "
        "commodity, sized by population. Diamonds: ports, squares: sources, stars: hubs "
        "(size by stock / target). Lines: edges, width and opacity by utilization, colour by mode."
    )

    scenario = scenarios[scenario_id]
    if not scenario.events:
        st.caption("This scenario has no events.")
    else:
        st.plotly_chart(
            timeline_chart(
                event_rows(scenario, country.network),
                scenario.horizon_days,
                st.session_state[DAY],
                dark,
            ),
            key="solmu_timeline",
            theme=None,
        )

    if days > 1:
        prev, field, nxt = st.columns([1, 4, 1], vertical_alignment="bottom")
        prev.button(
            "◀",
            on_click=_shift_day,
            args=(-1, days - 1),
            key="solmu_prev",
            help="Previous metrics day",
            width="stretch",
        )
        nxt.button(
            "▶",
            on_click=_shift_day,
            args=(1, days - 1),
            key="solmu_next",
            help="Next metrics day",
            width="stretch",
        )
        day = int(
            field.number_input(
                "Metrics day (independent from the map slider)",
                min_value=0,
                max_value=days - 1,
                step=1,
                key=DAY,
            )
        )
    else:
        day = 0
        st.caption("Metrics day 0 (one day simulated so far).")

    commodity_label = commodity_names[commodity]
    tab_metrics, tab_log, tab_region, tab_charts = st.tabs(
        ["Metrics", "Day log", "Region", "Charts"]
    )
    with tab_metrics:
        for col, (key, result) in zip(st.columns(len(results)), results.items(), strict=True):
            with col:
                st.subheader(labels[key])
                metrics_panel(sims[key], result, day, nodes, names, commodity, commodity_label)
    with tab_log:
        for col, (key, result) in zip(st.columns(len(results)), results.items(), strict=True):
            with col:
                st.subheader(labels[key])
                day_log_panel(
                    sims[key], result, states[key], nodes, day, commodity, commodity_label
                )
    with tab_region:
        regions = [n for n in nodes.loc[nodes["kind"] == "region", "id"]]
        region = st.selectbox(
            "Region", regions, format_func=lambda i: f"{i} {names[i]}", key="solmu_region"
        )
        for col, (key, result) in zip(st.columns(len(results)), results.items(), strict=True):
            with col:
                st.subheader(labels[key])
                region_card(
                    sims[key],
                    result,
                    states[key],
                    targets[key],
                    nodes,
                    region,
                    day,
                    commodity,
                    commodity_label,
                    key,
                    dark,
                )
    with tab_charts:
        st.plotly_chart(
            unmet_chart({labels[k]: r for k, r in results.items()}, day, dark),
            key="solmu_unmet_chart",
            theme=None,
        )
        for col, (key, result) in zip(st.columns(len(results)), results.items(), strict=True):
            with col:
                breakdown = stock_breakdown(result, states[key], nodes, commodity)
                st.plotly_chart(
                    where_chart(breakdown, labels[key], commodity_label, day, dark),
                    key=f"solmu_where_{key}",
                    theme=None,
                )


main()
