"""Reshape `RunResult` tables and the `State` history into what the UI shows (no model logic)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from solmu.core.model import Network, Scenario, ScenarioEvent
from solmu.core.results import RunResult
from solmu.core.scenario import active_modifiers
from solmu.core.state import State

EPS = 1e-9
WILDCARD = "*"


def node_frame(network: Network) -> pd.DataFrame:
    """Nodes as a table (id, name, kind, lat, lon, population)."""
    return pd.DataFrame(
        [(n.id, n.name, n.kind, n.lat, n.lon, n.population or 0) for n in network.nodes],
        columns=["id", "name", "kind", "lat", "lon", "population"],
    )


def edge_frame(network: Network) -> pd.DataFrame:
    """Edges with endpoint coordinates and endpoint names."""
    nodes = {n.id: n for n in network.nodes}
    rows = []
    for e in network.edges:
        a, b = nodes[e.from_node], nodes[e.to_node]
        rows.append((e.id, e.mode, a.lon, a.lat, b.lon, b.lat, a.name, b.name))
    return pd.DataFrame(
        rows, columns=["id", "mode", "lon0", "lat0", "lon1", "lat1", "from_name", "to_name"]
    )


def region_rows(daily: pd.DataFrame, nodes: pd.DataFrame) -> pd.DataFrame:
    """`daily` restricted to region nodes (hubs have rows with demand 0)."""
    return daily[daily["node_id"].isin(nodes.loc[nodes["kind"] == "region", "id"])]


def hub_stock_frame(
    nodes: pd.DataFrame, result: RunResult, day: int, commodity: str | None = None
) -> pd.DataFrame:
    """Hub rows for one day: node id, name, commodity, stock (t)."""
    hubs = nodes[nodes["kind"] == "hub"][["id", "name", "lat", "lon"]]
    rows = result.daily[result.daily["day"] == day]
    if commodity is not None:
        rows = rows[rows["commodity_id"] == commodity]
    out = hubs.merge(
        rows[["node_id", "commodity_id", "stock"]], left_on="id", right_on="node_id", how="inner"
    )
    return out.drop(columns="node_id")


def target_frame(states: Sequence[State]) -> pd.DataFrame:
    """Target stock in tonnes per day, node and commodity.

    `states[0]` is the initial state; `states[i + 1]` is the state after simulated day `i`.
    """
    rows = [
        (s.day - 1, node, cid, tonnes)
        for s in states[1:]
        for (node, cid), tonnes in s.target_stocks.items()
    ]
    return pd.DataFrame(rows, columns=["day", "node_id", "commodity_id", "target"])


def stock_breakdown(
    result: RunResult,
    states: Sequence[State],
    nodes: pd.DataFrame,
    commodity: str,
) -> pd.DataFrame:
    """Per day: usable regional stock, strategic stock, hub stock, in transit, import backlog."""
    daily = result.daily[result.daily["commodity_id"] == commodity]
    kind = dict(zip(nodes["id"], nodes["kind"], strict=True))
    daily = daily.assign(kind=daily["node_id"].map(kind))
    days = pd.Index(range(int(result.meta["days_simulated"])), name="day")
    regions = daily[daily["kind"] == "region"].groupby("day")[["stock", "strategic_stock"]].sum()
    hubs = daily[daily["kind"] == "hub"].groupby("day")["stock"].sum()
    transit = pd.Series(
        {
            s.day - 1: sum(t.tonnes for t in s.in_transit if t.commodity_id == commodity)
            for s in states[1:]
        },
        dtype=float,
    )
    imports = result.imports_daily
    backlog = imports[imports["commodity_id"] == commodity].set_index("day")["backlog"]
    table = pd.DataFrame(
        {
            "usable regional stock": regions["stock"],
            "strategic stock": regions["strategic_stock"],
            "hub stock": hubs,
            "in transit": transit,
            "import backlog": backlog,
        }
    )
    return table.reindex(days).fillna(0.0)


def region_series(
    result: RunResult, targets: pd.DataFrame, region: str, commodity: str
) -> pd.DataFrame:
    """Stock, strategic stock, target and unmet of one region and commodity per day."""
    rows = result.daily[
        (result.daily["node_id"] == region) & (result.daily["commodity_id"] == commodity)
    ][["day", "stock", "strategic_stock", "demand", "unmet"]]
    tgt = targets[(targets["node_id"] == region) & (targets["commodity_id"] == commodity)]
    return rows.merge(tgt[["day", "target"]], on="day", how="left").fillna({"target": 0.0})


def edge_run_stats(result: RunResult, edge_id: str) -> tuple[float, float, int] | None:
    """(mean utilization over open days, peak utilization, days simulated) of an edge."""
    rows = result.edges_daily[result.edges_daily["edge_id"] == edge_id]
    if rows.empty:
        return None
    open_days = rows[rows["capacity"] > 0]
    mean = float(open_days["utilization"].mean()) if len(open_days) else 0.0
    return mean, float(rows["utilization"].max()), len(rows)


@dataclass(frozen=True)
class EventRow:
    """One scenario event resolved to display names."""

    kind: str
    label: str
    factor: float
    first_day: int
    last_day: int


def _event_target(event: ScenarioEvent, names: dict[str, str], edge_names: dict[str, str]) -> str:
    if event.type == "edge_capacity":
        return edge_names.get(event.edge_id, event.edge_id)
    if event.node_id == WILDCARD:
        target = "all regions"
    else:
        target = names.get(event.node_id, event.node_id)
    return f"{target}, {event.commodity_id}" if event.commodity_id else target


def event_rows(scenario: Scenario, network: Network) -> list[EventRow]:
    """Scenario events with the target name resolved through the network."""
    names = {n.id: n.name for n in network.nodes}
    edge_names = {
        e.id: f"{e.id} ({names[e.from_node]} - {names[e.to_node]})" for e in network.edges
    }
    return [
        EventRow(e.type, _event_target(e, names, edge_names), e.factor, e.day, e.until_day)
        for e in scenario.events
    ]


def _shortage_keys(daily: pd.DataFrame, nodes: pd.DataFrame, day: int) -> set[tuple[str, str]]:
    rows = region_rows(daily[daily["day"] == day], nodes)
    rows = rows[rows["unmet"] > 0]
    return set(zip(rows["node_id"], rows["commodity_id"], strict=True))


def releases_on_day(
    result: RunResult, states: Sequence[State], nodes: pd.DataFrame, day: int
) -> pd.DataFrame:
    """Strategic stock that left the reserve on `day`: node_id, commodity_id, tonnes, region."""
    keys = ["node_id", "commodity_id"]
    today = result.daily[result.daily["day"] == day].set_index(keys)["strategic_stock"]
    if day == 0:
        before = pd.Series(dict(states[0].strategic_stocks), dtype=float)
        before.index = pd.MultiIndex.from_tuples(before.index, names=keys)
    else:
        prev = result.daily[result.daily["day"] == day - 1]
        before = prev.set_index(keys)["strategic_stock"]
    drop = (before.reindex(today.index) - today).dropna()
    drop = drop[drop > EPS]
    out = drop.rename("tonnes").reset_index()
    out["region"] = out["node_id"].map(dict(zip(nodes["id"], nodes["name"], strict=True)))
    return out.sort_values("tonnes", ascending=False, ignore_index=True)


def shortage_changes(
    result: RunResult, nodes: pd.DataFrame, day: int
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(entering, leaving) shortage on `day`, as (region id, commodity id) pairs."""
    now = _shortage_keys(result.daily, nodes, day)
    before = _shortage_keys(result.daily, nodes, day - 1) if day > 0 else set()
    return sorted(now - before), sorted(before - now)


def import_row(result: RunResult, day: int, commodity: str) -> pd.Series | None:
    """Imports table row for one day and commodity (None if the table has no such row)."""
    imports = result.imports_daily
    rows = imports[(imports["day"] == day) & (imports["commodity_id"] == commodity)]
    return None if rows.empty else rows.iloc[0]


def capacity_warnings(
    scenario: Scenario,
    region_ids: Sequence[str],
    result: RunResult | None,
    edge_id: str,
    check_days: Sequence[int],
) -> list[str]:
    """Reasons why renting capacity on `edge_id` is probably wasted."""
    warnings = []
    for day in sorted(set(check_days)):
        factor = active_modifiers(scenario, day, region_ids).edge_factor.get(edge_id, 1.0)
        if factor == 0:
            warnings.append(
                f"Edge {edge_id} is closed by an event on day {day} (factor 0). "
                "Rented capacity is multiplied by the event factor, so a closed edge stays closed."
            )
    stats = None if result is None else edge_run_stats(result, edge_id)
    if stats is not None and stats[0] < 0.5:
        mean, peak, days = stats
        warnings.append(
            f"Edge {edge_id} averaged {mean:.0%} utilization (peak {peak:.0%}) over the "
            f"{days} day(s) run so far: not a bottleneck, likely no effect."
        )
    return warnings
