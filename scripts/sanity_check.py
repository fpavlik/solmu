"""Dataset sanity checks (spec 13.6, 13.9): baseline, single-port closure, stress scenario,
refinery outage.

Usage: python scripts/sanity_check.py [--data-dir solmu/data] [--country finland]
Exit code 1 if a required outcome fails.
"""

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

from solmu.core.engine import Simulation
from solmu.core.loader import load_commodities, load_network, load_policy, load_scenario

SCENARIOS = ["baseline", "hamina_winter_closure", "gulf_of_finland_ice", "porvoo_refinery_outage"]
POLICIES = ["no_stockpiling", "finland_statutory"]
CLOSURE = "hamina_winter_closure"
STRESS = "gulf_of_finland_ice"
REFINERY = "porvoo_refinery_outage"
MAX_MEDICINE_UNMET_FRACTION = 0.05
MIN_POLICY_CONTRAST = 2.0
MAX_SECONDS_PER_30_DAYS = 2.0
ABOVE_BASELINE_TOLERANCE = 1e-6


def run_one(data_dir: Path, country: str, scenario_id: str, policy_id: str):
    """Run a scenario with a policy and return (result, network, elapsed seconds)."""
    root = data_dir / country
    network = load_network(root)
    scenario = load_scenario(data_dir / "scenarios" / f"{scenario_id}.json")
    sim = Simulation(
        network,
        scenario,
        load_policy(data_dir / "policies" / f"{policy_id}.json"),
        load_commodities(root / "commodities.json"),
    )
    start = time.perf_counter()
    result = sim.run()
    return result, network, scenario, time.perf_counter() - start


def region_rows(result, network) -> pd.DataFrame:
    """`daily` restricted to region nodes (hub rows carry no demand)."""
    regions = {n.id for n in network.nodes if n.kind == "region"}
    return result.daily[result.daily["node_id"].isin(regions)]


def hub_minima(result, network) -> dict[str, float]:
    """Lowest end-of-day stock per hub and held commodity over the run."""
    hubs = {n.id for n in network.nodes if n.kind == "hub"}
    hub_rows = result.daily[result.daily["node_id"].isin(hubs)]
    minima = hub_rows.groupby(["node_id", "commodity_id"])["stock"].min()
    return {f"{n}/{c}": float(v) for (n, c), v in minima.items() if hub_rows["stock"].max() > 0}


def report(scenario_id: str, policy_id: str, result, network, elapsed: float) -> dict:
    """Print one run's sanity metrics and return them."""
    s = result.summary
    regions = region_rows(result, network)
    short = regions[regions["unmet"] > 0]
    print(f"== {scenario_id} / {policy_id}  ({elapsed:.2f}s)")
    for cid, unmet in s["total_unmet_by_commodity"].items():
        n_short = short[short["commodity_id"] == cid]["node_id"].nunique()
        print(
            f"  {cid:<9} unmet {unmet:>10,.1f} t  first shortage day {s['first_shortage_day'][cid]}"
            f"  regions short {n_short:>2}  peak backlog {s['backlog'].get(cid, 0.0):>9,.1f} t"
            f"  import lost {s['import_lost_by_commodity'].get(cid, 0.0):>9,.1f} t"
        )
    print(
        f"  holding cost {s['holding_cost']:,.0f}  shortage cost {s['shortage_cost']:,.0f}"
        f"  total cost {s['total_cost']:,.0f}"
    )
    minima = hub_minima(result, network)
    print("  hub stock minimum (t): " + "; ".join(f"{k} {v:,.1f}" for k, v in minima.items()))
    return {
        "summary": s,
        "elapsed": elapsed,
        "horizon": int(result.meta["horizon_days"]),
        "result": result,
        "medicine_demand": float(regions[regions["commodity_id"] == "medicine"]["demand"].sum()),
    }


def redirection_evidence(run, baseline_run, network, scenario) -> tuple[bool, str]:
    """Whether a non-closed port's link carried more than in the baseline during the closure.

    Port offers are not part of `RunResult`; the daily flow on each `link-<port>-*` edge is the
    shipped part of the offer, compared day by day with the baseline run. Peak backlog is the
    second piece of evidence.
    """
    closed = {e.node_id for e in scenario.events if e.type == "node_capacity" and e.factor == 0.0}
    ports = {n.id for n in network.nodes if n.kind == "port"} - closed
    links = {e.id for e in network.edges if e.from_node in ports and e.id.startswith("link-")}
    flows = run["result"].edges_daily
    base = baseline_run["result"].edges_daily
    merged = flows[flows["edge_id"].isin(links)].merge(
        base[base["edge_id"].isin(links)], on=["day", "edge_id"], suffixes=("", "_base")
    )
    excess = merged[merged["flow"] > merged["flow_base"] + ABOVE_BASELINE_TOLERANCE]
    peak_backlog = max(run["summary"]["backlog"].values(), default=0.0)
    ok = not excess.empty and peak_backlog > 0
    detail = f"{excess['edge_id'].nunique()} non-closed port links above baseline flow on some day"
    return ok, f"{detail}, peak backlog {peak_backlog:,.0f} t"


def first_day(run) -> int:
    """Earliest first-shortage day over commodities (large when there is none)."""
    days = [d for d in run["summary"]["first_shortage_day"].values() if d is not None]
    return min(days) if days else 10**6


def shown_day(run) -> str:
    """First shortage day for display, 'none' when the run has no shortage."""
    day = first_day(run)
    return "none" if day >= 10**6 else str(day)


def evaluate(data_dir: Path, country: str) -> dict[str, bool]:
    """Run every scenario x policy, print the metrics and return each required outcome."""
    runs = {}
    scenarios = {}
    for scenario_id in SCENARIOS:
        for policy_id in POLICIES:
            result, network, scenario, elapsed = run_one(data_dir, country, scenario_id, policy_id)
            scenarios[scenario_id] = scenario
            runs[(scenario_id, policy_id)] = report(
                scenario_id, policy_id, result, network, elapsed
            )

    open_run = runs[(CLOSURE, "no_stockpiling")]
    redirected, detail = redirection_evidence(
        open_run, runs[("baseline", "no_stockpiling")], network, scenarios[CLOSURE]
    )
    medicine_unmet = open_run["summary"]["total_unmet_by_commodity"]["medicine"]
    food_unmet = open_run["summary"]["total_unmet_by_commodity"]["food"]
    print(f"\n{CLOSURE} (no_stockpiling): {detail}")
    print(f"{CLOSURE} (no_stockpiling): food unmet {food_unmet:,.1f} t (reported, not bounded)")

    stress_open = runs[(STRESS, "no_stockpiling")]
    stress_held = runs[(STRESS, "finland_statutory")]
    unmet_open = stress_open["summary"]["total_unmet"]
    unmet_held = stress_held["summary"]["total_unmet"]
    refinery_open = runs[(REFINERY, "no_stockpiling")]
    refinery_held = runs[(REFINERY, "finland_statutory")]
    fuel_open = refinery_open["summary"]["total_unmet_by_commodity"]["fuel"]
    fuel_held = refinery_held["summary"]["total_unmet_by_commodity"]["fuel"]
    checks = {
        "baseline has zero unmet under both policies": all(
            runs[("baseline", p)]["summary"]["total_unmet"] == 0 for p in POLICIES
        ),
        f"{CLOSURE}: other ports offer above baseline and backlog > 0": redirected,
        f"{CLOSURE}: medicine unmet < {MAX_MEDICINE_UNMET_FRACTION:.0%} of run demand": (
            medicine_unmet < MAX_MEDICINE_UNMET_FRACTION * open_run["medicine_demand"]
        ),
        f"{STRESS}: unmet without stockpiling ({unmet_open:,.0f} t) >= {MIN_POLICY_CONTRAST:g}x "
        f"statutory ({unmet_held:,.0f} t)": unmet_open >= MIN_POLICY_CONTRAST * unmet_held
        and unmet_open > 0,
        f"{STRESS}: statutory first shortage day {shown_day(stress_held)} later than "
        f"no_stockpiling {shown_day(stress_open)}, or no shortage (13.9)": first_day(stress_held)
        > first_day(stress_open),
        f"{REFINERY}: fuel unmet without stockpiling ({fuel_open:,.0f} t) >= "
        f"{MIN_POLICY_CONTRAST:g}x statutory ({fuel_held:,.0f} t)": fuel_open
        >= MIN_POLICY_CONTRAST * fuel_held
        and fuel_open > 0,
        f"{REFINERY}: statutory first shortage day {shown_day(refinery_held)} later than "
        f"no_stockpiling {shown_day(refinery_open)}, or no shortage": first_day(refinery_held)
        > first_day(refinery_open),
        f"every run under {MAX_SECONDS_PER_30_DAYS:g} s per 30 days of horizon (13.8)": all(
            r["elapsed"] < MAX_SECONDS_PER_30_DAYS * max(1.0, r["horizon"] / 30)
            for r in runs.values()
        ),
    }
    return checks


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("solmu/data"))
    parser.add_argument("--country", default="finland")
    args = parser.parse_args()
    checks = evaluate(args.data_dir, args.country)
    print()
    for label, ok in checks.items():
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
