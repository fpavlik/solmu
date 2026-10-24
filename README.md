# Solmu

**Supply-chain resilience simulator for a country.** Solmu (Finnish for *knot*, as in a
node of a network) shows, day by day, how a disruption — a port closed by ice, a refinery
fire, a dock strike — moves through a national logistics network, where essential goods run
short, and how much a resilience policy such as statutory stockpiling changes the outcome and
at what cost.

Headless Python core plus a Streamlit interface. First dataset: Finland. Everything that
makes Finland Finland is data, not code; another country is another folder.

> **Prototype v0.2.** The network, demand and capacities are approximate, built from open
> statistics and documented number by number in
> [`solmu/data/finland/SOURCES.md`](solmu/data/finland/SOURCES.md) with a confidence level
> for each. The model has been checked against one real event (the 2010 stevedore strike,
> [`docs/validation/2010_port_strike.md`](docs/validation/2010_port_strike.md)) and is not
> calibrated. Treat results as directions and orders of magnitude, not forecasts.

## Quick start

```bash
pip install -e ".[ui,dev]"
pytest -q                                  # 222 tests, incl. the dataset's sanity checks
python -m streamlit run solmu/ui/app.py    # map, day slider, policy comparison, actions
python scripts/run_scenario.py --scenario porvoo_refinery_outage --policy finland_statutory
python scripts/sanity_check.py             # required outcomes of the Finland dataset
```

## What it models

- **Network** of regions (demand, stock), ports (import capacity with spare handling
  headroom), sources (a refinery, farming regions) and wholesaler hubs with their own stock;
  road, rail and sea edges with capacity, transit time, cost and cargo restrictions.
- **Daily tick**: events → arrivals → reserve release → consumption → flow allocation
  (min-cost flow per commodity by priority, two tiers for goods that move through
  warehouses) → dispatch. Deterministic: same inputs, same tables.
- **Disruptions** as data: port or source capacity (per commodity if needed), edge capacity,
  demand spikes, anticipatory stock building. A closed port does not delete its cargo: ships
  divert to ports with spare capacity after a delay; the rest waits and is partly lost.
- **Policies** as data: how many days of strategic stock, **who holds it** (regions,
  wholesaler hubs, or a refinery with a crude share that is useless while the refinery is
  down; company stock drawn automatically vs state stock released by decision), and **when
  it is released** (on observed shortage, or when the national import offer drops).
- **Actions** between days — release reserve, rent capacity, prioritise a commodity — with
  a budget. The seam an analyst's script or an optimiser uses.
- **Costs on both sides**: holding cost of the stocks against a priced cost of unmet demand,
  so "how much resilience is worth buying" is one chart.

## Finland: scenarios and what they show

Three policies: `no_stockpiling`, `finland_statutory_reactive` (statutory stocks, released
when a shortage is seen) and `finland_statutory` (the same stocks, released when imports
drop — how the national emergency supply agency actually decides).

| Scenario | No stockpiling | Stocks, reactive release | Stocks, anticipatory release |
|---|---|---|---|
| **HaminaKotka closed 14 days** (45 d) | no shortage; cargo diverts to other ports | — | — |
| **Gulf of Finland ice**, southern ports shut ~3 weeks (60 d) | 465 t medicine short from day 22; losses 93 M | 139 t; 67 M holding + 28 M losses | none; 66 M holding |
| **Porvoo refinery out 42 days** (90 d) | 115,718 t fuel short from day 7; losses 579 M | 446 t; 95 M + 2 M | 446 t; 95 M + 2 M |

Three things the model says that are worth knowing: a single-port closure is not a crisis
for essentials in Finland; a refinery outage is absorbed almost entirely by companies'
obligatory product stocks, before any government decision; and for medicines the *timing* of
the release decision matters as much as the size of the stock. Numbers are from
`scripts/sanity_check.py`; costs in currency units with placeholder prices.

## Layout

```
solmu/core/       engine, solvers, events, policies, actions, results, batch — no UI imports
solmu/ui/         Streamlit app: animated map, comparison mode, timeline, charts, action queue
solmu/data/       finland/ (nodes, edges, demand, supply, SOURCES.md), policies/, scenarios/
scripts/          run_scenario, run_batch, sanity_check, validate_2010, build_demand
tests/            pytest suite with hand-computed expectations on tiny networks
docs/             MODEL.md (the model as built), DATA.md (file schemas), validation/
```

## Documents

- [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — the specification.
- [`docs/MODEL.md`](docs/MODEL.md) — every formula and simplification as implemented.
- [`docs/DATA.md`](docs/DATA.md) — data file schemas; add a country by adding a folder.
- [`AGENTS.md`](AGENTS.md) — rules for AI coding agents working on the repository.

## Status and direction

v0 and v0.1 built the engine, the Finland dataset, the UI and the first historical
validation. Next: calibration against more events, a multi-commodity solver, a second country
and an HTTP API for batch and session use. Contributions are welcome; read
`AGENTS.md` first — the same rules apply to humans.

## Licence and author

Code: [Apache License 2.0](LICENSE). Data files carry their own attribution (National Land
Survey of Finland and Statistics Finland, CC BY 4.0; see `SOURCES.md`).

Copyright 2026 Filipp Pavlik — see [`NOTICE`](NOTICE).
