"""Data loading and run helpers for the UI (no model logic)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from solmu.core.engine import Simulation
from solmu.core.loader import (
    load_commodities,
    load_network,
    load_policy,
    load_scenario,
)
from solmu.core.model import Commodities, Network, Policy, Scenario

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@dataclass(frozen=True)
class Country:
    """Network and commodity catalogue of one country directory."""

    network: Network
    commodities: Commodities


@st.cache_data
def list_countries() -> list[str]:
    """Directories under `data/` that hold a network (contain `nodes.csv`)."""
    return sorted(p.name for p in DATA_DIR.iterdir() if (p / "nodes.csv").is_file())


@st.cache_resource
def load_country(country: str) -> Country:
    """Parse and validate one country's data once per server process."""
    root = DATA_DIR / country
    return Country(load_network(root), load_commodities(root / "commodities.json"))


@st.cache_resource
def load_scenarios() -> dict[str, Scenario]:
    """All scenario files by id."""
    found = (load_scenario(p) for p in sorted((DATA_DIR / "scenarios").glob("*.json")))
    return {s.id: s for s in found}


@st.cache_resource
def load_policies() -> dict[str, Policy]:
    """All policy files by id."""
    found = (load_policy(p) for p in sorted((DATA_DIR / "policies").glob("*.json")))
    return {p.id: p for p in found}


def make_simulation(country: Country, scenario: Scenario, policy: Policy, seed: int) -> Simulation:
    """A fresh simulation at day 0."""
    return Simulation(country.network, scenario, policy, country.commodities, seed)
