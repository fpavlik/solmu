"""Plotly charts built from `RunResult` tables."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from solmu.core.results import RunResult
from solmu.ui.tables import EventRow

TEMPLATES = {False: "plotly", True: "plotly_dark"}
EVENT_COLORS = {"node_capacity": "#1446a0", "edge_capacity": "#e68c1e", "demand": "#b03060"}
STACK_COLORS = {
    "usable regional stock": "#2e86c1",
    "strategic stock": "#8e44ad",
    "hub stock": "#009688",
    "in transit": "#f39c12",
    "import backlog": "#95a5a6",
}


def _mark_day(fig: go.Figure, day: int | None) -> None:
    if day is not None:
        fig.add_vline(x=day, line_width=1.5, line_dash="dot", line_color="gray")


def unmet_per_day(result: RunResult) -> pd.DataFrame:
    """Total unmet tonnes per day and commodity (sum over regions)."""
    return result.daily.groupby(["day", "commodity_id"], as_index=False)["unmet"].sum()


def unmet_chart(
    runs: dict[str, RunResult], day: int | None = None, dark: bool = False
) -> go.Figure:
    """Unmet tonnes per day, one line per commodity; dash style tells the runs apart."""
    fig = go.Figure()
    dashes = ["solid", "dash"]
    for i, (label, result) in enumerate(runs.items()):
        table = unmet_per_day(result)
        for commodity, part in table.groupby("commodity_id"):
            fig.add_scatter(
                x=part["day"],
                y=part["unmet"],
                mode="lines",
                name=f"{commodity} - {label}",
                legendgroup=str(commodity),
                line={"dash": dashes[i % len(dashes)]},
            )
    _mark_day(fig, day)
    fig.update_layout(
        template=TEMPLATES[dark],
        xaxis_title="day",
        yaxis_title="unmet demand (t)",
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
        height=340,
        legend={"orientation": "h", "y": -0.25},
        title="Total unmet demand per day (dotted line: metrics day)",
    )
    return fig


def where_chart(
    breakdown: pd.DataFrame, label: str, commodity_label: str, day: int | None, dark: bool
) -> go.Figure:
    """Stacked areas: where the commodity is held or waiting, per day."""
    fig = go.Figure()
    for column in breakdown.columns:
        fig.add_scatter(
            x=breakdown.index,
            y=breakdown[column],
            mode="lines",
            stackgroup="where",
            name=column,
            line={"width": 0.5, "color": STACK_COLORS[column]},
        )
    _mark_day(fig, day)
    fig.update_layout(
        template=TEMPLATES[dark],
        xaxis_title="day",
        yaxis_title="tonnes",
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
        height=340,
        legend={"orientation": "h", "y": -0.25},
        title=f"Where is the {commodity_label}? {label}",
    )
    return fig


def region_chart(series: pd.DataFrame, title: str, day: int | None, dark: bool) -> go.Figure:
    """Usable stock and target (and strategic stock) of one region over the run."""
    fig = go.Figure()
    fig.add_scatter(x=series["day"], y=series["stock"], mode="lines", name="stock")
    fig.add_scatter(
        x=series["day"], y=series["target"], mode="lines", name="target", line={"dash": "dash"}
    )
    fig.add_scatter(
        x=series["day"],
        y=series["strategic_stock"],
        mode="lines",
        name="strategic stock",
        line={"dash": "dot"},
    )
    _mark_day(fig, day)
    fig.update_layout(
        template=TEMPLATES[dark],
        xaxis_title="day",
        yaxis_title="tonnes",
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
        height=280,
        legend={"orientation": "h", "y": -0.3},
        title=title,
    )
    return fig


def timeline_chart(events: list[EventRow], horizon: int, day: int | None, dark: bool) -> go.Figure:
    """Horizontal bar per scenario event (active window inclusive), metrics day marked."""
    fig = go.Figure()
    for kind in EVENT_COLORS:
        rows = [e for e in events if e.kind == kind]
        if not rows:
            continue
        fig.add_bar(
            orientation="h",
            name=kind,
            y=[f"{e.label} x{e.factor:g}" for e in rows],
            x=[e.last_day - e.first_day + 1 for e in rows],
            base=[e.first_day for e in rows],
            marker={"color": EVENT_COLORS[kind]},
            text=[f"x{e.factor:g}" for e in rows],
            textposition="inside",
            hovertext=[
                f"{kind}: {e.label}<br>factor {e.factor:g}, days {e.first_day}-{e.last_day}"
                for e in rows
            ],
            hoverinfo="text",
        )
    _mark_day(fig, None if day is None else day + 0.5)
    fig.update_layout(
        template=TEMPLATES[dark],
        barmode="overlay",
        height=max(140, 40 + 28 * len(events)),
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
        xaxis={"title": "day", "range": [0, max(horizon, 1)]},
        yaxis={"autorange": "reversed"},
        legend={"orientation": "h", "y": 1.15},
        title="Scenario events (dotted line: metrics day)",
    )
    return fig
