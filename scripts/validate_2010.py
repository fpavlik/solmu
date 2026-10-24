"""Historical validation against the March 2010 stevedore strike (spec 13.10).

Runs `port_strike_2010` under `finland_statutory` and `no_stockpiling`, prints the validation
ledger with live numbers and verdicts, per-commodity metrics and the day-by-day port offers.
It is a report, not a gate: the exit code is always 0.

Usage: python scripts/validate_2010.py [--data-dir solmu/data] [--country finland] [--sensitivity]
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from solmu.core.engine import Simulation
from solmu.core.loader import load_commodities, load_network, load_policy, load_scenario
from solmu.core.model import Commodities, Network, Scenario
from solmu.core.results import RunResult
from solmu.core.scenario import active_modifiers

SCENARIO = "port_strike_2010"
BASELINE = "baseline"
POLICIES = ["finland_statutory", "no_stockpiling"]
REFERENCE_POLICY = "finland_statutory"
STRIKE = (4, 19)
STOCKUP_END_DAY = 3
FERRY_PORTS = ["port-helsinki", "port-turku", "port-naantali", "port-hanko"]
PRIVATE_SOURCE = "src-porvoo-refinery"
ESSENTIALS = ["medicine", "food", "fuel"]
SOURCE_A55 = (
    "Yliskyla-Peuralahti, Spies, Kamara & Tapaninen, Univ. of Turku Centre for Maritime "
    "Studies, report A55 (STOCA)"
)
OBSERVED_TRADE_HALT = 0.80
TRADE_HALT_BAND = (0.70, 0.90)
FOOD_UNMET_TOLERANCE = 0.01
STOCKUP_MIN_RATIO = 1.05
MATCH, MISMATCH, NOT_MODELLED = "MATCH", "MISMATCH", "NOT MODELLED"
MEDICINE_HUB_MIN_SHARE = 0.25
WEEKS_DAYS = 14
CLEARED_FRACTION = 0.01
LONG_HORIZON = 70
EPS = 1e-6


@dataclass
class Run:
    """One simulation run with the tonnes shipped from supply nodes per day."""

    result: RunResult
    shipped: pd.DataFrame
    initial_stocks: dict
    regions: set
    hubs: set


@dataclass
class Row:
    """One ledger line: observation, model measurable, value and verdict per policy."""

    ident: str
    fact: str
    source: str
    measure: str
    values: dict[str, str] = field(default_factory=dict)
    verdicts: dict[str, str] = field(default_factory=dict)


class Case:
    """Loaded data plus a cache of runs, so each (scenario, policy) is simulated once."""

    def __init__(self, data_dir: Path, country: str) -> None:
        root = data_dir / country
        self.data_dir = data_dir
        self.network = load_network(root)
        self.commodities = load_commodities(root / "commodities.json")
        self.scenarios = {
            name: load_scenario(data_dir / "scenarios" / f"{name}.json")
            for name in (SCENARIO, BASELINE)
        }
        self.policies = {p: load_policy(data_dir / "policies" / f"{p}.json") for p in POLICIES}
        self.supply_nodes = {r.node_id for r in self.network.supply}
        self.cache: dict[tuple[str, str], Run] = {}

    def run(
        self,
        scenario_key: str,
        policy_id: str,
        scenario: Scenario | None = None,
        network: Network | None = None,
        commodities: Commodities | None = None,
    ) -> Run:
        """Step a simulation to the horizon, recording what each supply node shipped per day.

        Runs of the shipped data are cached; passing any override (a toggle run) bypasses it.
        """
        key = (scenario_key, policy_id)
        cacheable = scenario is None and network is None and commodities is None
        if cacheable and key in self.cache:
            return self.cache[key]
        scenario = scenario or self.scenarios[scenario_key]
        network = network or self.network
        sim = Simulation(
            network, scenario, self.policies[policy_id], commodities or self.commodities
        )
        regions = {n.id for n in network.nodes if n.kind == "region"}
        hubs = {n.id for n in network.nodes if n.kind == "hub"}
        initial = dict(sim.state.stocks)
        rows = []
        while not sim.done:
            state = sim.step()
            for ship in state.in_transit:
                if ship.dispatch_day == state.day - 1 and ship.from_node in self.supply_nodes:
                    rows.append((ship.dispatch_day, ship.from_node, ship.commodity_id, ship.tonnes))
        shipped = pd.DataFrame(rows, columns=["day", "node_id", "commodity_id", "tonnes"])
        shipped = shipped.groupby(["day", "node_id", "commodity_id"], as_index=False)[
            "tonnes"
        ].sum()
        run = Run(sim.result(), shipped, initial, regions, hubs)
        if cacheable:
            self.cache[key] = run
        return run


def window(frame: pd.DataFrame, first: int = STRIKE[0], last: int = STRIKE[1]) -> pd.DataFrame:
    """Rows of `frame` with first <= day <= last."""
    return frame[(frame["day"] >= first) & (frame["day"] <= last)]


def region_rows(run: Run) -> pd.DataFrame:
    return run.result.daily[run.result.daily["node_id"].isin(run.regions)]


def unmet(run: Run, commodity: str, first: int = 0, last: int = 10**6) -> float:
    rows = region_rows(run)
    rows = rows[rows["commodity_id"] == commodity]
    return float(window(rows, first, last)["unmet"].sum())


def demand(run: Run, commodity: str, first: int, last: int) -> float:
    rows = region_rows(run)
    rows = rows[rows["commodity_id"] == commodity]
    return float(window(rows, first, last)["demand"].sum())


def shipped_per_day(run: Run, nodes: list[str], commodities: list[str], first: int, last: int):
    """Mean tonnes/day shipped by `nodes` for `commodities` over days first..last."""
    rows = run.shipped[run.shipped["node_id"].isin(nodes)]
    rows = rows[rows["commodity_id"].isin(commodities)]
    return float(window(rows, first, last)["tonnes"].sum()) / (last - first + 1)


def stock_total(run: Run, commodities: list[str], day: int) -> float:
    daily = run.result.daily
    rows = daily[(daily["day"] == day) & daily["commodity_id"].isin(commodities)]
    rows = rows[rows["node_id"].isin(run.regions | run.hubs)]
    return float(rows["stock"].sum())


def port_borne_drop(case: Case, scenario: Scenario, commodities: list[str]) -> float:
    """Share of port-borne tonnes lost on a strike day from the scenario factors alone."""
    mods = active_modifiers(scenario, STRIKE[0])
    kinds = {n.id: n.kind for n in case.network.nodes}
    base = left = 0.0
    for rec in case.network.supply:
        if kinds[rec.node_id] != "port" or rec.commodity_id not in commodities:
            continue
        factor = mods.node_factor.get(rec.node_id, 1.0)
        factor *= mods.node_commodity_factor.get((rec.node_id, rec.commodity_id), 1.0)
        base += rec.tonnes_per_day
        left += rec.tonnes_per_day * factor
    return 1.0 - left / base if base else 0.0


def domestic_share(case: Case, commodity: str) -> float:
    """Supply of `source` nodes as a share of total base demand for `commodity`."""
    kinds = {n.id: n.kind for n in case.network.nodes}
    made = sum(
        r.tonnes_per_day
        for r in case.network.supply
        if r.commodity_id == commodity and kinds[r.node_id] == "source"
    )
    need = sum(r.tonnes_per_day for r in case.network.demand if r.commodity_id == commodity)
    return made / need if need else 0.0


def long_scenario(scenario: Scenario) -> Scenario:
    """The scenario with a longer horizon, so backlogs can be followed until they clear."""
    return scenario.model_copy(update={"horizon_days": LONG_HORIZON})


def clearing_days(run: Run, commodity: str) -> int | None:
    """Days after the strike ends until the import backlog is below CLEARED_FRACTION of its peak."""
    imports = run.result.imports_daily
    series = imports[imports["commodity_id"] == commodity].set_index("day")["backlog"]
    threshold = CLEARED_FRACTION * series.max()
    after = series[series.index > STRIKE[1]]
    cleared = after[after <= threshold]
    return None if cleared.empty else int(cleared.index[0]) - STRIKE[1]


def regions_short(run: Run, commodity: str) -> int:
    rows = region_rows(run)
    rows = rows[(rows["commodity_id"] == commodity) & (rows["unmet"] > 0)]
    return int(rows["node_id"].nunique())


def hub_minimum(run: Run) -> dict[str, tuple[float, float]]:
    daily = run.result.daily
    hub_rows = daily[daily["node_id"].isin(run.hubs)]
    out = {}
    for (hub, cid), minimum in hub_rows.groupby(["node_id", "commodity_id"])["stock"].min().items():
        out[f"{hub}/{cid}"] = (float(minimum), run.initial_stocks[(hub, cid)])
    return out


def build_ledger(case: Case) -> list[Row]:
    """Evaluate every ledger line against the runs of both policies."""
    scenario = case.scenarios[SCENARIO]
    first, last = STRIKE
    runs = {p: case.run(SCENARIO, p) for p in POLICIES}
    bases = {p: case.run(BASELINE, p) for p in POLICIES}
    rows: list[Row] = []

    def add(ident, fact, source, measure, per_policy):
        row = Row(ident, fact, source, measure)
        for policy, (value, verdict) in per_policy.items():
            row.values[policy] = value
            row.verdicts[policy] = verdict
        rows.append(row)

    def per(fn):
        return {p: fn(runs[p], bases[p]) for p in POLICIES}

    drops = {c: port_borne_drop(case, scenario, [c]) for c in ESSENTIALS}
    total_drop = port_borne_drop(case, scenario, ESSENTIALS)
    verdict = MATCH if TRADE_HALT_BAND[0] <= total_drop <= TRADE_HALT_BAND[1] else MISMATCH
    text = (
        f"all {total_drop:.0%}; "
        + ", ".join(f"{c} {d:.0%}" for c, d in drops.items())
        + " (scenario factors, not a model output)"
    )
    add(
        "L1",
        "Public ports stopped; ~80% of foreign trade halted",
        f"{SOURCE_A55}, sect. 4.3 and summary",
        "share of port-borne medicine+food+fuel tonnes lost on a strike day, "
        f"band {TRADE_HALT_BAND[0]:.0%}-{TRADE_HALT_BAND[1]:.0%}",
        {p: (text, verdict) for p in POLICIES},
    )

    def private(run, base):
        factor = active_modifiers(scenario, first).node_factor.get(PRIVATE_SOURCE, 1.0)
        strike = shipped_per_day(run, [PRIVATE_SOURCE], ["fuel"], first, last)
        normal = shipped_per_day(base, [PRIVATE_SOURCE], ["fuel"], first, last)
        days = run.shipped[
            (run.shipped["node_id"] == PRIVATE_SOURCE) & (run.shipped["commodity_id"] == "fuel")
        ]
        active = int((window(days)["tonnes"] > 0).sum())
        ok = factor == 1.0
        value = (
            f"node factor {factor:g}; fuel shipped on {active}/{last - first + 1} strike days, "
            f"{strike:,.0f} vs {normal:,.0f} t/day in the baseline (shipping is need-driven; "
            "released reserves replace it under the statutory policy)"
        )
        return value, MATCH if ok else MISMATCH

    add(
        "L2",
        "Industry-owned private ports (Kilpilahti, Porvoo refinery) operated normally",
        f"{SOURCE_A55}, sect. 4.4",
        "refinery node factor stays 1 (input check; shipments are demand-driven)",
        per(private),
    )

    food_med = ["food", "medicine"]
    others = [p.id for p in case.network.nodes if p.kind == "port" and p.id not in FERRY_PORTS]

    def ferries(run, base):
        ferry = shipped_per_day(run, FERRY_PORTS, food_med, first, last)
        ferry_base = shipped_per_day(base, FERRY_PORTS, food_med, first, last)
        rest = shipped_per_day(run, others, food_med, first, last)
        rest_base = shipped_per_day(base, others, food_med, first, last)
        share = ferry / (ferry + rest) if ferry + rest else 0.0
        share_base = ferry_base / (ferry_base + rest_base) if ferry_base + rest_base else 0.0
        direct = 0.5 * ferry_base
        ok = ferry > 0 and rest < EPS and share > share_base
        value = (
            f"ferry ports food+medicine shipped {ferry:,.0f} t/day (baseline {ferry_base:,.0f}, "
            f"own direct offer at x0.5 {direct:,.0f}); other ports {rest:,.0f} (baseline "
            f"{rest_base:,.0f}); ferry share {share:.0%} vs {share_base:.0%}"
        )
        return value, MATCH if ok else MISMATCH

    add(
        "L3",
        "Ro-ro ferries kept running; trucked cargo rerouted via Turku, Naantali, Helsinki; "
        "container feeders stopped",
        f"{SOURCE_A55}, sect. 4.3 and 4.4",
        "food+medicine shipped from ferry ports > 0, from other ports = 0, and the ferry "
        "share of port-borne food+medicine higher than in the baseline",
        per(ferries),
    )

    def stockup(run, base):
        parts = []
        ok = True
        for c in food_med:
            ratio = stock_total(run, [c], STOCKUP_END_DAY) / stock_total(base, [c], STOCKUP_END_DAY)
            parts.append(f"{c} x{ratio:.2f}")
            ok = ok and ratio >= STOCKUP_MIN_RATIO
        return "stock at regions+hubs, end of day 3 vs baseline: " + ", ".join(parts), (
            MATCH if ok else MISMATCH
        )

    add(
        "L4",
        "Companies raised inventories before the strike (announced weeks ahead)",
        f"{SOURCE_A55}, sect. 4.4 and 4.5",
        f"food and medicine stock ratio at day {STOCKUP_END_DAY} at least {STOCKUP_MIN_RATIO}",
        per(stockup),
    )

    def none_unmet(commodity):
        def fn(run, base):
            total = unmet(run, commodity)
            inside = unmet(run, commodity, first, last)
            ok = total < EPS
            return (
                f"unmet over run {total:,.1f} t, over days {first}-{last} {inside:,.1f} t, "
                f"regions short {regions_short(run, commodity)}",
                MATCH if ok else MISMATCH,
            )

        return fn

    add(
        "L5",
        "No shortage of medicines reported",
        f"{SOURCE_A55}, sect. 4.4 and 4.6; spec 13.10",
        "total unmet medicine = 0",
        per(none_unmet("medicine")),
    )

    def food(run, base):
        inside = unmet(run, "food", first, last)
        share = inside / demand(run, "food", first, last)
        return (
            f"unmet {inside:,.1f} t = {share:.2%} of strike-window demand, "
            f"regions short {regions_short(run, 'food')}; domestic sources cover "
            f"{domestic_share(case, 'food'):.0%} of food demand (A55 table 3.1: imports ~30%)",
            MATCH if share <= FOOD_UNMET_TOLERANCE else MISMATCH,
        )

    add(
        "L6",
        "No severe lack of basic food (domestic share high, imports marginal)",
        f"{SOURCE_A55}, sect. 4.6",
        f"food unmet during the strike at most {FOOD_UNMET_TOLERANCE:.0%} of demand",
        per(food),
    )
    add(
        "L7",
        "Imported fresh produce was the first retail gap",
        f"{SOURCE_A55}, sect. 4.6",
        "needs a separate perishable commodity; `food` is one aggregate",
        {p: ("food is aggregate, produce not separated", NOT_MODELLED) for p in POLICIES},
    )

    def fuel(run, base):
        total = unmet(run, "fuel")
        return (
            f"unmet {total:,.0f} t, first day {run.result.summary['first_shortage_day']['fuel']}, "
            f"regions short {regions_short(run, 'fuel')}",
            MATCH if total < EPS else MISMATCH,
        )

    add(
        "L8",
        "No fuel shortage for consumers (Porvoo kept running; obligatory oil stocks exist)",
        f"spec 13.10 (press); {SOURCE_A55}, sect. 4.5 (oil reserves 90 days of imports)",
        "total unmet fuel = 0 (reference: the policy with statutory stocks)",
        per(fuel),
    )

    def hoarding(run, base):
        rows = region_rows(run)
        rows = rows[rows["commodity_id"] == "fuel"]
        base_rows = region_rows(base)
        base_rows = base_rows[base_rows["commodity_id"] == "fuel"]
        ratio = float(window(rows, first, first + 2)["demand"].sum()) / float(
            window(base_rows, first, first + 2)["demand"].sum()
        )
        return (
            f"fuel demand days {first}-{first + 2} x{ratio:.2f}, unmet {unmet(run, 'fuel'):,.0f} t",
            (MATCH if unmet(run, "fuel") < EPS else MISMATCH),
        )

    add(
        "L9",
        "Fuel: hoarding and local distribution hiccups, but no shortage",
        "spec 13.10 (press); hoarding size not sourced",
        "hoarding demand spike without unmet fuel (local hiccups: not modelled, "
        "needs sub-regional detail)",
        per(hoarding),
    )

    def pharma(run, base):
        helsinki = shipped_per_day(run, ["port-helsinki"], ["medicine"], first, last)
        helsinki_base = shipped_per_day(base, ["port-helsinki"], ["medicine"], first, last)
        daily = run.shipped[
            (run.shipped["node_id"] == "port-helsinki")
            & (run.shipped["commodity_id"] == "medicine")
        ]
        days_open = int((window(daily)["tonnes"] > 0).sum())
        offer = run.result.imports_daily
        offer = offer[offer["commodity_id"] == "medicine"]
        drop = float(window(offer)["drop"].mean())
        factor = active_modifiers(scenario, first).node_commodity_factor.get(
            ("port-helsinki", "medicine"), 1.0
        )
        low = min(m / i for k, (m, i) in hub_minimum(run).items() if k.endswith("/medicine"))
        ok = factor > 0 and low >= MEDICINE_HUB_MIN_SHARE
        return (
            f"Helsinki medicine factor {factor:g}, shipped on {days_open}/{last - first + 1} "
            f"strike days ({helsinki:,.1f} vs {helsinki_base:,.1f} t/day, need-driven); mean "
            f"national medicine drop {drop:.0%}; lowest medicine hub stock {low:.0%} of initial",
            MATCH if ok else MISMATCH,
        )

    add(
        "L10",
        "Pharma imports kept flowing via Vuosaari (Helsinki) with Kotka, Turku, Hanko as "
        "alternatives; medicines exempted/ensured by union-authority cooperation",
        f"{SOURCE_A55}, sect. 4.4 (cooperation); spec 13.10 (Vuosaari, alternatives)",
        "Helsinki medicine capacity open (factor > 0) on every strike day and no medicine hub "
        f"below {MEDICINE_HUB_MIN_SHARE:.0%} of its initial stock",
        per(pharma),
    )

    def backlog(run, base):
        long_run = case.run(SCENARIO, run.result.meta["policy_id"], long_scenario(scenario))
        parts = []
        ok = True
        for c in ESSENTIALS:
            cleared = clearing_days(long_run, c)
            parts.append(
                f"{c} peak {run.result.summary['backlog'][c]:,.0f} t, cleared "
                + (f"{cleared} days after day {last}" if cleared is not None else "not cleared")
            )
            ok = ok and (cleared is None or cleared >= WEEKS_DAYS)
        return "import backlog: " + "; ".join(parts), MATCH if ok else MISMATCH

    add(
        "L11",
        "Backlog took weeks to clear after the strike",
        "spec 13.10 (press on the aftermath); direction only, observed backlog is mostly exports",
        f"import backlog of every essential takes at least {WEEKS_DAYS} days after the strike "
        f"to fall below {CLEARED_FRACTION:.0%} of its peak (run extended to {LONG_HORIZON} days)",
        per(backlog),
    )
    for ident, fact, measure in (
        (
            "L12",
            "Export industry (paper, metals) shut plants within days",
            "exports are not a flow in the model; needs port sinks and an export commodity",
        ),
        (
            "L13",
            "Economic loss ~100 MEUR/day (government estimate)",
            "unmet demand is not priced and exports are not modelled",
        ),
        (
            "L14",
            "Rerouting surcharge +500-1000 EUR per truck unit (report: 300-500, max 1500)",
            "no price on rerouted tonnes; needs a cost event and a transport-cost metric",
        ),
    ):
        add(
            ident,
            fact,
            f"{SOURCE_A55}, sect. 4.3 and 4.6; spec 13.10",
            measure,
            {p: ("not computed", NOT_MODELLED) for p in POLICIES},
        )
    return rows


def print_ledger(rows: list[Row]) -> None:
    print("VALIDATION LEDGER - stevedore strike 4-19 March 2010 (day 4 = 4 March)")
    print(f"Reference policy for the headline verdict: {REFERENCE_POLICY}\n")
    for row in rows:
        print(f"[{row.ident}] {row.fact}")
        print(textwrap.indent(textwrap.fill(f"source: {row.source}", 96), "     "))
        print(textwrap.indent(textwrap.fill(f"measurable: {row.measure}", 96), "     "))
        for policy in POLICIES:
            body = textwrap.fill(f"{policy}: {row.values[policy]}", 96)
            print(textwrap.indent(body, "     "))
        verdicts = " | ".join(f"{p} {row.verdicts[p]}" for p in POLICIES)
        print(f"     VERDICT {row.verdicts[REFERENCE_POLICY]}   ({verdicts})\n")
    counts: dict[str, int] = {}
    for row in rows:
        verdict = row.verdicts[REFERENCE_POLICY]
        counts[verdict] = counts.get(verdict, 0) + 1
    print("Summary (reference policy): " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))


def print_metrics(case: Case) -> None:
    print("\nPER-COMMODITY METRICS")
    for policy in POLICIES:
        run = case.run(SCENARIO, policy)
        summary = run.result.summary
        print(f"== {SCENARIO} / {policy}")
        for cid in ESSENTIALS:
            print(
                f"  {cid:<9} unmet {summary['total_unmet_by_commodity'][cid]:>10,.1f} t  "
                f"first shortage day {summary['first_shortage_day'][cid]}  "
                f"regions short {regions_short(run, cid):>2}  "
                f"peak backlog {summary['backlog'].get(cid, 0.0):>10,.1f} t  "
                f"lost {summary['import_lost_by_commodity'].get(cid, 0.0):>10,.1f} t"
            )
        minima = hub_minimum(run)
        print(
            "  hub stock minimum t (share of initial): "
            + "; ".join(f"{k} {m:,.0f} ({m / i:.0%})" for k, (m, i) in minima.items())
        )
        print(f"  holding cost {summary['holding_cost']:,.0f}")


def print_offers(case: Case) -> None:
    run = case.run(SCENARIO, REFERENCE_POLICY)
    other = case.run(SCENARIO, POLICIES[1])
    same = run.result.imports_daily.equals(other.result.imports_daily)
    print(
        "\nPORT OFFERS DURING THE STRIKE (national imports_daily; identical under both "
        f"policies: {same})"
    )
    for cid in ("medicine", "food"):
        imports = run.result.imports_daily
        imports = window(imports[imports["commodity_id"] == cid], STRIKE[0] - 1, STRIKE[1] + 3)
        print(f"== {cid}: day  offer  baseline  drop  backlog  lost   | shipped by ferry ports")
        for _, r in imports.iterrows():
            day = int(r["day"])
            ships = run.shipped[(run.shipped["day"] == day) & (run.shipped["commodity_id"] == cid)]
            ferry = float(ships[ships["node_id"].isin(FERRY_PORTS)]["tonnes"].sum())
            print(
                f"  {day:>3} {r['offer']:>9,.1f} {r['baseline']:>9,.1f} {r['drop']:>5.0%} "
                f"{r['backlog']:>10,.1f} {r['lost']:>8,.1f}   | {ferry:>9,.1f}"
            )


def variant(scenario: Scenario, keep=lambda e: True, extend_to: int | None = None) -> Scenario:
    """Copy of `scenario` with events filtered and, optionally, the strike windows stretched."""
    data = scenario.model_dump()
    events = [e for e in data["events"] if keep(e)]
    if extend_to is not None:
        for e in events:
            if e["type"] == "node_capacity":
                e["until_day"] = extend_to
        data["horizon_days"] = extend_to + 7
    data["events"] = events
    return Scenario.model_validate(data)


def is_ferry_cargo(e: dict) -> bool:
    return (
        e["type"] == "node_capacity"
        and e["node_id"] in FERRY_PORTS
        and e["commodity_id"] in ("food", "medicine")
    )


def is_event(e: dict, node_id: str, commodity_id: str) -> bool:
    return (
        e["type"] == "node_capacity"
        and e["node_id"] == node_id
        and e["commodity_id"] == commodity_id
    )


def set_factor(scenario: Scenario, match, factor: float) -> Scenario:
    """Copy of `scenario` with the factor of every matching event replaced."""
    data = scenario.model_dump()
    for e in data["events"]:
        if match(e):
            e["factor"] = factor
    return Scenario.model_validate(data)


def with_headroom(network: Network, ratio: float) -> Network:
    """Copy of `network` where every port handles `ratio` x its baseline (toggle runs only)."""
    kinds = {n.id: n.kind for n in network.nodes}
    supply = [
        r.model_copy(update={"max_tonnes_per_day": r.tonnes_per_day * ratio})
        if kinds[r.node_id] == "port"
        else r
        for r in network.supply
    ]
    return network.model_copy(update={"supply": supply})


def with_domestic_scale(network: Network, commodity: str, scale: float) -> Network:
    """Copy of `network` where `source` supply of `commodity` is scaled (toggle runs only)."""
    kinds = {n.id: n.kind for n in network.nodes}
    supply = [
        r.model_copy(update={"tonnes_per_day": r.tonnes_per_day * scale})
        if r.commodity_id == commodity and kinds[r.node_id] == "source"
        else r
        for r in network.supply
    ]
    return network.model_copy(update={"supply": supply})


def with_hub_days(commodities: Commodities, commodity: str, days: float) -> Commodities:
    """Copy of `commodities` where `commodity` holds `days` of served demand at hubs."""
    changed = [
        c.model_copy(update={"hub_stock_days": days}) if c.id == commodity else c
        for c in commodities.commodities
    ]
    return commodities.model_copy(update={"commodities": changed})


def sensitivity_variants(case: Case) -> dict[str, dict]:
    """Toggle runs: each changes one assumption of the shipped scenario or data."""
    base = case.scenarios[SCENARIO]
    no_stock_up = lambda e: e["type"] != "stock_target"  # noqa: E731
    no_loss = case.commodities.model_copy(update={"import_backlog_loss_fraction_per_day": 0.0})
    return {
        "T0 scenario as shipped": {"scenario": base},
        "T1 Naantali fuel 0.0 (instead of 0.5)": {
            "scenario": set_factor(base, lambda e: is_event(e, "port-naantali", "fuel"), 0.0)
        },
        "T2 ferry food+medicine 0.3 (instead of 0.5)": {
            "scenario": set_factor(base, is_ferry_cargo, 0.3)
        },
        "T3 ferry food+medicine 0.0 (no trucked ro-ro)": {
            "scenario": set_factor(base, is_ferry_cargo, 0.0)
        },
        "T4 ferry food+medicine 1.0 (unaffected)": {
            "scenario": set_factor(base, is_ferry_cargo, 1.0)
        },
        "T5 no anticipatory stocking": {"scenario": variant(base, keep=no_stock_up)},
        "T6 no hoarding demand events": {
            "scenario": variant(base, keep=lambda e: e["type"] != "demand")
        },
        "T7 HaminaKotka medicine 0.5 (pharma alternative)": {
            "scenario": set_factor(
                base, lambda e: is_event(e, "port-hamina-kotka", "medicine"), 0.5
            )
        },
        "T8 strike twice as long (days 4-35)": {"scenario": variant(base, extend_to=35)},
        "T9 worst case: T3 + T5": {
            "scenario": variant(set_factor(base, is_ferry_cargo, 0.0), keep=no_stock_up)
        },
        "T10 port headroom 1.15 x baseline (instead of 1.4)": {
            "scenario": base,
            "network": with_headroom(case.network, 1.15),
        },
        "T11 backlog loss 0 per day (instead of 0.1)": {"scenario": base, "commodities": no_loss},
        "T12 headroom 1.15 and loss 0": {
            "scenario": base,
            "network": with_headroom(case.network, 1.15),
            "commodities": no_loss,
        },
        "T13 domestic food supply x0.7 (imports ~30% of food)": {
            "scenario": base,
            "network": with_domestic_scale(case.network, "food", 0.7),
        },
        "T14 T13 and no ferries (T3)": {
            "scenario": set_factor(base, is_ferry_cargo, 0.0),
            "network": with_domestic_scale(case.network, "food", 0.7),
        },
        "T15 T14 and food hub stock 2 days (short buffer)": {
            "scenario": set_factor(base, is_ferry_cargo, 0.0),
            "network": with_domestic_scale(case.network, "food", 0.7),
            "commodities": with_hub_days(case.commodities, "food", 2.0),
        },
        "T16 T13 and no anticipatory stocking (T5)": {
            "scenario": variant(base, keep=lambda e: e["type"] != "stock_target"),
            "network": with_domestic_scale(case.network, "food", 0.7),
        },
    }


CLEARING_VARIANTS = ("T0", "T10", "T11", "T12")


def print_sensitivity(case: Case) -> None:
    variants = sensitivity_variants(case)
    print("\nSENSITIVITY: unmet tonnes over the run [first shortage day]")
    print(f"  {'variant':<52} {'policy':<18}" + "".join(f"{c:>22}" for c in ESSENTIALS))
    for name, kwargs in variants.items():
        for policy in POLICIES:
            run = case.run(SCENARIO, policy, **kwargs)
            s = run.result.summary
            cells = [
                f"{s['total_unmet_by_commodity'][c]:,.0f} [{s['first_shortage_day'][c]}]".rjust(22)
                for c in ESSENTIALS
            ]
            print(f"  {name:<52} {policy:<18}" + "".join(cells))
    print(
        f"\nSENSITIVITY: days after day {STRIKE[1]} until the import backlog is below "
        f"{CLEARED_FRACTION:.0%} of its peak (run extended to {LONG_HORIZON} days)"
    )
    print(f"  {'variant':<52}" + "".join(f"{c:>12}" for c in ESSENTIALS))
    for name, kwargs in variants.items():
        if not name.startswith(CLEARING_VARIANTS) or name.startswith("T1 "):
            continue
        kwargs = {**kwargs, "scenario": long_scenario(kwargs["scenario"])}
        run = case.run(SCENARIO, REFERENCE_POLICY, **kwargs)
        cells = []
        for c in ESSENTIALS:
            days = clearing_days(run, c)
            cells.append(("never" if days is None else str(days)).rjust(12))
        print(f"  {name:<52}" + "".join(cells))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("solmu/data"))
    parser.add_argument("--country", default="finland")
    parser.add_argument("--sensitivity", action="store_true", help="also run the toggle runs")
    args = parser.parse_args()
    case = Case(args.data_dir, args.country)
    print_ledger(build_ledger(case))
    print_metrics(case)
    print_offers(case)
    if args.sensitivity:
        print_sensitivity(case)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
