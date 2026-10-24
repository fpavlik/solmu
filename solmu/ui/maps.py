"""Animated Plotly map: one frame per simulated day, scrubbed in the browser without a rerun."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from solmu.core.results import RunResult
from solmu.ui.tables import region_rows

MODE_COLORS = {"road": (120, 120, 130), "rail": (230, 140, 30), "sea": (40, 110, 200)}
MODE_COLORS_DARK = {"road": (170, 170, 180), "rail": (240, 160, 50), "sea": (80, 150, 235)}
SEVERITY_SCALE = [[0.0, "#2ea043"], [0.5, "#f0c828"], [1.0, "#d7263d"]]
PORT_COLOR, SOURCE_COLOR, HUB_COLOR = "#1446a0", "#8228a0", "#009688"
EDGE_WIDTH_MIN_PX, EDGE_WIDTH_MAX_PX = 1.0, 6.0
EDGE_ALPHA_MIN = 0.12
REGION_SIZE_MIN_PX, REGION_SIZE_MAX_PX = 7.0, 26.0
HUB_SIZE_MIN_PX, HUB_SIZE_MAX_PX = 11.0, 26.0
MAP_HEIGHT_PX = 640
SPEEDS = (("0.5x", 800), ("1x", 400), ("2x", 200))


@dataclass(frozen=True)
class MapRun:
    """One run to draw: label, result tables and the per-day target table."""

    label: str
    result: RunResult
    targets: pd.DataFrame


@dataclass(frozen=True)
class Palette:
    """Colours that depend on the Streamlit theme."""

    template: str
    land: str
    ocean: str
    coast: str
    marker_line: str
    modes: dict[str, tuple[int, int, int]]


LIGHT = Palette("plotly", "#eef0f2", "#dbe9f6", "#8a94a0", "#282828", MODE_COLORS)
DARK = Palette("plotly_dark", "#2b2f36", "#14181f", "#6b7280", "#e6e6e6", MODE_COLORS_DARK)


def _pivot(rows: pd.DataFrame, col: str, days: int, ids: list[str]) -> np.ndarray:
    table = rows.pivot(index="day", columns="node_id", values=col)
    return table.reindex(index=range(days), columns=ids).to_numpy(dtype=float)


def _edge_pivot(edges_daily: pd.DataFrame, col: str, days: int, ids: list[str]) -> np.ndarray:
    table = edges_daily.pivot(index="day", columns="edge_id", values=col)
    return table.reindex(index=range(days), columns=ids).fillna(0.0).to_numpy(dtype=float)


class _RunArrays:
    """Per-day arrays and hover strings of one run, ready to be turned into frame updates."""

    def __init__(
        self,
        run: MapRun,
        nodes: pd.DataFrame,
        edges: pd.DataFrame,
        commodity: str,
        palette: Palette,
    ) -> None:
        result = run.result
        self.days = int(result.meta["days_simulated"])
        self.regions = nodes[nodes["kind"] == "region"].reset_index(drop=True)
        self.hubs = nodes[nodes["kind"] == "hub"].reset_index(drop=True)
        self.edges = edges
        self.commodity = commodity
        self._palette = palette
        self._build_regions(run, commodity)
        self._build_hubs(run, commodity)
        self._build_edges(result)

    def _build_regions(self, run: MapRun, commodity: str) -> None:
        ids = list(self.regions["id"])
        rows = region_rows(run.result.daily, self.regions)
        rows = rows[rows["commodity_id"] == commodity]
        tgt = run.targets[run.targets["commodity_id"] == commodity]
        col = {c: _pivot(rows, c, self.days, ids) for c in ("stock", "strategic_stock", "demand")}
        unmet = _pivot(rows, "unmet", self.days, ids)
        target = _pivot(tgt, "target", self.days, ids)
        demand = np.nan_to_num(col["demand"])
        unmet = np.nan_to_num(unmet)
        self.region_ratio = np.divide(unmet, demand, out=np.zeros_like(unmet), where=demand > 0)
        self.region_text = [
            [
                f"<b>{r.id} {r.name}</b> ({commodity}, day {d})<br>"
                f"stock {np.nan_to_num(col['stock'][d, j]):,.1f} t<br>"
                f"strategic stock {np.nan_to_num(col['strategic_stock'][d, j]):,.1f} t<br>"
                f"demand {demand[d, j]:,.1f} t<br>"
                f"unmet {unmet[d, j]:,.1f} t ({self.region_ratio[d, j]:.0%})<br>"
                f"target {np.nan_to_num(target[d, j]):,.1f} t"
                for j, r in enumerate(self.regions.itertuples())
            ]
            for d in range(self.days)
        ]
        pops = self.regions["population"].to_numpy(dtype=float)
        top = max(pops.max(), 1.0) if len(pops) else 1.0
        span = REGION_SIZE_MAX_PX - REGION_SIZE_MIN_PX
        self.region_size = REGION_SIZE_MIN_PX + span * np.sqrt(pops / top)

    def _build_hubs(self, run: MapRun, commodity: str) -> None:
        ids = list(self.hubs["id"])
        daily = run.result.daily
        rows = daily[daily["node_id"].isin(ids)]
        sel = rows[rows["commodity_id"] == commodity]
        tgt = run.targets[run.targets["commodity_id"] == commodity]
        stock = _pivot(sel, "stock", self.days, ids)
        target = _pivot(tgt, "target", self.days, ids)
        ratio = np.divide(stock, target, out=np.full_like(stock, np.nan), where=target > 0)
        span = HUB_SIZE_MAX_PX - HUB_SIZE_MIN_PX
        size = HUB_SIZE_MIN_PX + span * np.clip(np.nan_to_num(ratio, nan=0.0), 0.0, 1.0)
        self.hub_size = np.where(np.isnan(ratio), HUB_SIZE_MIN_PX - 3, size)
        lines: dict[tuple[int, str], list[str]] = {}
        for r in rows.itertuples():
            lines.setdefault((r.day, r.node_id), []).append(f"{r.commodity_id}: {r.stock:,.0f} t")
        self.hub_text = [
            [
                "<br>".join([f"<b>{h.id} {h.name}</b> (hub, day {d})", *lines.get((d, h.id), [])])
                for h in self.hubs.itertuples()
            ]
            for d in range(self.days)
        ]

    def _build_edges(self, result: RunResult) -> None:
        ids = list(self.edges["id"])
        flow = _edge_pivot(result.edges_daily, "flow", self.days, ids)
        cap = _edge_pivot(result.edges_daily, "capacity", self.days, ids)
        self.edge_util = _edge_pivot(result.edges_daily, "utilization", self.days, ids)
        clipped = np.clip(self.edge_util, 0.0, 1.0)
        self.edge_width = EDGE_WIDTH_MIN_PX + (EDGE_WIDTH_MAX_PX - EDGE_WIDTH_MIN_PX) * clipped
        self.edge_alpha = EDGE_ALPHA_MIN + (1.0 - EDGE_ALPHA_MIN) * clipped
        self.edge_dynamic = [
            len({(round(w, 2), round(a, 2)) for w, a in zip(ws, als, strict=True)}) > 1
            for ws, als in zip(self.edge_width.T, self.edge_alpha.T, strict=True)
        ]
        self.edge_text = [
            [
                f"<b>{e.id}</b> ({e.mode})<br>{e.from_name} - {e.to_name}<br>"
                f"flow {flow[d, j]:,.0f} t of capacity {cap[d, j]:,.0f} t<br>"
                f"utilization {self.edge_util[d, j]:.0%} (day {d})"
                for j, e in enumerate(self.edges.itertuples())
            ]
            for d in range(self.days)
        ]
        self._modes = list(self.edges["mode"])

    def edge_color(self, day: int, j: int) -> str:
        """rgba colour of edge `j`: hue by mode, opacity by utilization."""
        r, g, b = self._palette.modes.get(self._modes[j], (100, 100, 100))
        return f"rgba({r},{g},{b},{self.edge_alpha[day, j]:.2f})"


def _static_markers(
    nodes: pd.DataFrame, kind: str, symbol: str, color: str, name: str, geo: str, legend: bool
) -> go.Scattergeo:
    rows = nodes[nodes["kind"] == kind]
    return go.Scattergeo(
        lon=rows["lon"],
        lat=rows["lat"],
        mode="markers",
        marker={
            "symbol": symbol,
            "size": 10,
            "color": color,
            "line": {"width": 1, "color": "white"},
        },
        text=[f"<b>{r.id} {r.name}</b> ({kind})" for r in rows.itertuples()],
        hoverinfo="text",
        name=name,
        showlegend=legend,
        geo=geo,
    )


def _legend_line(name: str, color: str, geo: str) -> go.Scattergeo:
    return go.Scattergeo(
        lon=[None],
        lat=[None],
        mode="lines",
        line={"color": color, "width": 3},
        name=name,
        geo=geo,
        hoverinfo="skip",
    )


def _geo_ranges(nodes: pd.DataFrame) -> tuple[list[float], list[float]]:
    pad_lon = max((nodes["lon"].max() - nodes["lon"].min()) * 0.08, 0.5)
    pad_lat = max((nodes["lat"].max() - nodes["lat"].min()) * 0.06, 0.5)
    return (
        [float(nodes["lon"].min() - pad_lon), float(nodes["lon"].max() + pad_lon)],
        [float(nodes["lat"].min() - pad_lat), float(nodes["lat"].max() + pad_lat)],
    )


def _map_traces(
    data: _RunArrays,
    nodes: pd.DataFrame,
    palette: Palette,
    geo: str,
    first: bool,
    out: list[go.Scattergeo],
) -> dict[str, list[int]]:
    """Append the traces of one map to `out`; return indices of the traces frames update."""
    index: dict[str, list[int]] = {"edges": [], "edge_hover": [], "regions": [], "hubs": []}
    edges = data.edges
    for j, e in enumerate(edges.itertuples()):
        index["edges"].append(len(out))
        out.append(
            go.Scattergeo(
                lon=[e.lon0, e.lon1],
                lat=[e.lat0, e.lat1],
                mode="lines",
                line={"width": float(data.edge_width[0, j]), "color": data.edge_color(0, j)},
                hoverinfo="skip",
                showlegend=False,
                geo=geo,
            )
        )
    index["edge_hover"].append(len(out))
    out.append(
        go.Scattergeo(
            lon=(edges["lon0"] + edges["lon1"]) / 2,
            lat=(edges["lat0"] + edges["lat1"]) / 2,
            mode="markers",
            marker={
                "size": 6,
                "opacity": 0.45,
                "color": [
                    "rgb({},{},{})".format(*palette.modes.get(m, (100, 100, 100)))
                    for m in edges["mode"]
                ],
            },
            text=data.edge_text[0],
            hoverinfo="text",
            showlegend=False,
            geo=geo,
        )
    )
    index["regions"].append(len(out))
    out.append(
        go.Scattergeo(
            lon=data.regions["lon"],
            lat=data.regions["lat"],
            mode="markers",
            marker={
                "size": data.region_size,
                "color": data.region_ratio[0],
                "colorscale": SEVERITY_SCALE,
                "cmin": 0.0,
                "cmax": 1.0,
                "line": {"width": 1, "color": palette.marker_line},
                "showscale": first,
                "colorbar": {
                    "title": {"text": "unmet / demand"},
                    "tickformat": ".0%",
                    "len": 0.6,
                    "thickness": 12,
                    "x": 1.0,
                    "xanchor": "left",
                },
            },
            text=data.region_text[0],
            hoverinfo="text",
            name="Regions",
            showlegend=first,
            geo=geo,
        )
    )
    out.append(_static_markers(nodes, "port", "diamond", PORT_COLOR, "Ports", geo, first))
    out.append(_static_markers(nodes, "source", "square", SOURCE_COLOR, "Sources", geo, first))
    index["hubs"].append(len(out))
    out.append(
        go.Scattergeo(
            lon=data.hubs["lon"],
            lat=data.hubs["lat"],
            mode="markers",
            marker={
                "symbol": "star",
                "size": data.hub_size[0],
                "color": HUB_COLOR,
                "line": {"width": 1.5, "color": "white"},
            },
            text=data.hub_text[0],
            hoverinfo="text",
            name="Hubs (size: stock / target)",
            showlegend=first,
            geo=geo,
        )
    )
    if first:
        for mode, rgb in palette.modes.items():
            out.append(_legend_line(f"{mode.capitalize()} edge", "rgb({},{},{})".format(*rgb), geo))
    return index


def _frame(day: int, datas: list[_RunArrays], indices: list[dict[str, list[int]]]) -> go.Frame:
    """Updates for one day; `type` and `geo` are repeated because plotly.js would otherwise
    turn an update without them into a cartesian scatter trace."""
    updates: list[dict] = []
    traces: list[int] = []

    def add(i: int, geo: str, **attrs: object) -> None:
        updates.append({"type": "scattergeo", "geo": geo, **attrs})
        traces.append(i)

    for m, (data, idx) in enumerate(zip(datas, indices, strict=True)):
        geo = "geo" if m == 0 else f"geo{m + 1}"
        for j, trace in enumerate(idx["edges"]):
            if data.edge_dynamic[j]:
                line = {"width": float(data.edge_width[day, j]), "color": data.edge_color(day, j)}
                add(trace, geo, line=line)
        add(idx["edge_hover"][0], geo, text=data.edge_text[day])
        marker = {"color": data.region_ratio[day].tolist()}
        add(idx["regions"][0], geo, marker=marker, text=data.region_text[day])
        marker = {"size": data.hub_size[day].tolist()}
        add(idx["hubs"][0], geo, marker=marker, text=data.hub_text[day])
    return go.Frame(data=updates, traces=traces, name=str(day))


def _controls(days: int) -> tuple[list[dict], list[dict]]:
    still = {"mode": "immediate", "frame": {"duration": 0, "redraw": True}}
    still["transition"] = {"duration": 0}
    buttons = [
        {
            "label": f"Play {name}",
            "method": "animate",
            "args": [
                None,
                {
                    "frame": {"duration": ms, "redraw": True},
                    "transition": {"duration": 0},
                    "fromcurrent": True,
                    "mode": "immediate",
                },
            ],
        }
        for name, ms in SPEEDS
    ]
    buttons.append(
        {
            "label": "Pause",
            "method": "animate",
            "args": [[None], {"frame": {"duration": 0, "redraw": False}, "mode": "immediate"}],
        }
    )
    menus = [
        {
            "type": "buttons",
            "direction": "left",
            "showactive": False,
            "x": 0.0,
            "y": -0.02,
            "xanchor": "left",
            "yanchor": "top",
            "buttons": buttons,
        }
    ]
    steps = [{"label": str(d), "method": "animate", "args": [[str(d)], still]} for d in range(days)]
    sliders = [
        {
            "active": 0,
            "x": 0.32,
            "len": 0.66,
            "xanchor": "left",
            "y": -0.02,
            "yanchor": "top",
            "pad": {"t": 4, "b": 0},
            "currentvalue": {"prefix": "Map day: ", "xanchor": "left"},
            "steps": steps,
        }
    ]
    return menus, sliders


def build_map_figure(
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
    runs: list[MapRun],
    commodity: str,
    dark: bool = False,
) -> go.Figure:
    """One figure, one geo subplot per run, one frame per day shared by all subplots."""
    palette = DARK if dark else LIGHT
    datas = [_RunArrays(r, nodes, edges, commodity, palette) for r in runs]
    days = min(d.days for d in datas)
    cols = len(runs)
    fig = make_subplots(
        rows=1,
        cols=cols,
        specs=[[{"type": "geo"}] * cols],
        subplot_titles=[r.label for r in runs],
        horizontal_spacing=0.02,
    )
    traces: list[go.Scattergeo] = []
    indices = [
        _map_traces(d, nodes, palette, "geo" if i == 0 else f"geo{i + 1}", i == 0, traces)
        for i, d in enumerate(datas)
    ]
    fig.add_traces(traces)
    lon_range, lat_range = _geo_ranges(nodes)
    fig.update_geos(
        projection_type="mercator",
        lonaxis_range=lon_range,
        lataxis_range=lat_range,
        resolution=50,
        showland=True,
        landcolor=palette.land,
        showocean=True,
        oceancolor=palette.ocean,
        showcoastlines=True,
        coastlinecolor=palette.coast,
        showcountries=True,
        countrycolor=palette.coast,
        showlakes=False,
        bgcolor="rgba(0,0,0,0)",
    )
    menus, sliders = _controls(days)
    fig.update_layout(
        template=palette.template,
        height=MAP_HEIGHT_PX,
        dragmode="pan",
        margin={"l": 0, "r": 0, "t": 30, "b": 80},
        paper_bgcolor="rgba(0,0,0,0)",
        legend={
            "x": 0.0,
            "y": 0.98,
            "xanchor": "left",
            "yanchor": "top",
            "bgcolor": "rgba(0,0,0,0)",
        },
        updatemenus=menus,
        sliders=sliders,
    )
    fig.frames = [_frame(d, datas, indices) for d in range(days)]
    return fig
