"""Run results: daily tables, summary metrics and parquet export."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

DAILY_COLUMNS = [
    "day",
    "node_id",
    "commodity_id",
    "stock",
    "strategic_stock",
    "demand",
    "consumed",
    "unmet",
    "inbound",
    "outbound",
]
EDGES_COLUMNS = ["day", "edge_id", "flow", "capacity", "utilization"]
IMPORTS_COLUMNS = ["day", "commodity_id", "offer", "baseline", "drop", "backlog", "lost"]

DAILY_FILE = "daily.parquet"
EDGES_FILE = "edges_daily.parquet"
IMPORTS_FILE = "imports_daily.parquet"
INFO_FILE = "result.json"


@dataclass
class RunResult:
    """Everything a client needs after (part of) a run."""

    meta: dict[str, Any]
    daily: pd.DataFrame
    edges_daily: pd.DataFrame
    summary: dict[str, Any]
    actions_log: list[dict[str, Any]] = field(default_factory=list)
    imports_daily: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(columns=IMPORTS_COLUMNS)
    )

    def to_parquet(self, directory: Path | str) -> None:
        """Write the three tables as parquet and meta/summary/actions_log as JSON."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.daily.to_parquet(directory / DAILY_FILE, index=False)
        self.edges_daily.to_parquet(directory / EDGES_FILE, index=False)
        self.imports_daily.to_parquet(directory / IMPORTS_FILE, index=False)
        info = {"meta": self.meta, "summary": self.summary, "actions_log": self.actions_log}
        (directory / INFO_FILE).write_text(json.dumps(info, indent=2), encoding="utf-8")

    @classmethod
    def from_parquet(cls, directory: Path | str) -> RunResult:
        """Read a result written by `to_parquet`."""
        directory = Path(directory)
        info = json.loads((directory / INFO_FILE).read_text(encoding="utf-8"))
        imports_path = directory / IMPORTS_FILE
        imports = (
            pd.read_parquet(imports_path)
            if imports_path.exists()
            else pd.DataFrame(columns=IMPORTS_COLUMNS)
        )
        return cls(
            meta=info["meta"],
            daily=pd.read_parquet(directory / DAILY_FILE),
            edges_daily=pd.read_parquet(directory / EDGES_FILE),
            summary=info["summary"],
            actions_log=info["actions_log"],
            imports_daily=imports,
        )


def summarize(
    daily: pd.DataFrame,
    edges_daily: pd.DataFrame,
    populations: Mapping[str, int],
    holding_cost: float,
    action_spend: float,
    backlog_peak: Mapping[str, float] | None = None,
    import_lost: Mapping[str, float] | None = None,
    shortage_cost_per_tonne: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Aggregate metrics over the days present in the tables; `backlog` is peak import backlog.

    `shortage_cost_per_tonne` prices unmet demand per commodity (default: nothing is priced).
    """
    short = daily[daily["unmet"] > 0]
    unmet_by_commodity = {
        str(c): float(v) for c, v in daily.groupby("commodity_id")["unmet"].sum().items()
    }
    prices = shortage_cost_per_tonne or {}
    shortage_cost_by_commodity = {
        c: unmet * float(prices.get(c, 0.0)) for c, unmet in unmet_by_commodity.items()
    }
    shortage_cost = float(sum(shortage_cost_by_commodity.values()))
    earliest = short.groupby("commodity_id")["day"].min()
    first_day = {c: (int(earliest[c]) if c in earliest else None) for c in unmet_by_commodity}
    region_days = short.drop_duplicates(["day", "node_id"])
    population_days = float(sum(populations.get(n, 0) for n in region_days["node_id"]))
    peak = float(edges_daily["utilization"].max()) if len(edges_daily) else 0.0
    return {
        "total_unmet_by_commodity": unmet_by_commodity,
        "total_unmet": float(sum(unmet_by_commodity.values())),
        "population_days_in_shortage": population_days,
        "first_shortage_day": first_day,
        "peak_edge_utilization": peak,
        "holding_cost": float(holding_cost),
        "action_spend": float(action_spend),
        "shortage_cost_by_commodity": shortage_cost_by_commodity,
        "shortage_cost": shortage_cost,
        "total_cost": float(holding_cost + action_spend + shortage_cost),
        "backlog": {str(c): float(v) for c, v in (backlog_peak or {}).items()},
        "import_lost_by_commodity": {str(c): float(v) for c, v in (import_lost or {}).items()},
    }
