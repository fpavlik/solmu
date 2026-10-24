# Supply Resilience Simulator — Implementation Plan (Prototype v0)

This document is the specification for building the first working prototype of
an open-source **supply-chain resilience simulator** on a real regional network
(Finland). It is written so that an engineer or coding agent can implement it
without further context.

Read the whole document before writing code. Sections 2 and 3 (principles and
architecture) are mandatory constraints, not suggestions. The prototype is
small, but the structure must survive the next two stages: a full simulation
engine (OSM import, API, calibration) and further clients on top of the same core.

---

## 1. Goal and scope

### 1.1 What we are building

A headless simulation core plus a web visualization that shows, day by day,
how a disruption (e.g. a port closure in winter) propagates through the Finnish
logistics network and where shortages of essential goods appear — and how the
outcome changes under different **resilience policies** (e.g. statutory
stockpiling rules).

The demo scenario: *"Port of HaminaKotka closed for 14 days in winter, other
Gulf of Finland ports at reduced capacity"*, run twice: with Finnish statutory
stockpiling rules and without them, shown side by side.

### 1.2 In scope for the prototype

- Regional-scale network of Finland: ~25 nodes, ~60 edges (hand-built CSV).
- Three commodities: `food`, `fuel`, `medicine`.
- Daily tick simulation with flow allocation, stocks, shortages.
- Policy profiles (stockpiling rules, release delay, emergency budget).
- Scenario files with timed disruption events.
- Analyst **actions** (release reserve, add capacity, reroute) applied
  between ticks — the hook scripts, optimisers and an API will later use.
- Batch runs with seeds (Monte Carlo-ready).
- Streamlit UI: map, day slider, policy comparison, metrics, action buttons.
- Tests for the core.

### 1.3 Explicitly out of scope (do not build, but do not block)

- OSM import, routing engines, city-level detail.
- HTTP API server, sessions.
- UI polish beyond what the analysis needs.
- Calibrated real data. The prototype uses open and approximate data, and
  says so everywhere (README, UI footer, slides).
- Agent-based (per-vehicle) simulation.

---

## 2. Architecture principles (mandatory)

1. **Headless core.** The simulation core (`solmu.core`) imports no UI, no
   Streamlit, no plotting, no HTTP. It is a plain Python library callable from
   scripts, tests, notebooks and a future API server.

2. **Everything is data.** Networks, demand, supply, policies and scenarios are
   files (CSV / JSON) with documented schemas. No Finland-specific constants in
   code. Swapping Finland for Sweden must require only new data files.

3. **Step-based, deterministic.** The core exposes `reset() / step() / state /
   result()`. Same inputs + same seed → identical output. Randomness only via
   an explicit `numpy.random.Generator` seeded from `seed`.

4. **Actions are first-class.** Interventions (by a human, a script, an
   optimizer, a future API client) go through one `apply(actions)` interface
   between ticks. The core never reads a UI.

5. **Separation of layers:** `data → model → engine → results → clients`. A
   client (Streamlit now, API later) only consumes `State` and
   `RunResult` objects; it never touches solver internals.

6. **Typed, validated inputs.** All input files are parsed into typed models
   (pydantic) with validation and clear error messages. Invalid data fails
   fast with a message naming the file, row/field and problem.

7. **Open formats, stable identifiers.** Node/edge ids are stable strings
   (`FI-18` for a region, `port-hamina-kotka`), never positional indices.
   These ids appear in results so other tools can join on them.

8. **Prototype-grade accuracy, engine-grade structure.** Simplified models
   are fine (and documented as such), but hacks that would have to be undone
   later (hard-coded ids, globals, UI code mutating simulation state) are not.

---

## 3. Repository layout

```
solmu/                      # Python package (name is provisional)
  core/
    model.py                # typed entities: Node, Edge, Commodity, Policy, ...
    loader.py               # CSV/JSON → validated models
    engine.py               # Simulation class: reset/step/apply/result
    solver.py               # flow allocation (interface + reference impl)
    policy.py               # policy rules applied at each tick
    scenario.py             # event scheduling, capacity modifiers
    actions.py              # Action types and their effects
    state.py                # State / Snapshot dataclasses
    results.py              # RunResult, metrics, export to pandas/parquet
    batch.py                # run N seeds / N variants, aggregate
  data/
    finland/
      nodes.csv
      edges.csv
      commodities.json
      demand.csv
      supply.csv
      SOURCES.md            # provenance of every number
    policies/
      finland_statutory.json
      no_stockpiling.json
    scenarios/
      hamina_winter_closure.json
      baseline.json
  ui/
    app.py                  # Streamlit entry point
    maps.py                 # pydeck/plotly layers built from State
    charts.py
  scripts/
    run_scenario.py         # CLI: run one scenario, write results
    run_batch.py            # CLI: Monte Carlo / policy comparison
tests/
  test_loader.py
  test_engine_small.py      # tiny 3-node network, hand-verifiable numbers
  test_policy.py
  test_scenario.py
  test_determinism.py
docs/
  MODEL.md                  # the model as implemented, formulas, simplifications
  DATA.md                   # schemas (copy of section 5 kept up to date)
README.md
AGENTS.md                   # rules of conduct for AI coding agents (canonical)
CLAUDE.md                   # imports AGENTS.md; Claude Code specifics
pyproject.toml
```

Tooling: Python 3.11+, `pyproject.toml`, dependencies kept minimal
(`pydantic`, `numpy`, `pandas`, `networkx`, `pyarrow`; UI adds `streamlit`,
`pydeck` or `plotly`). Use `ruff` for lint/format and `pytest` for tests.
Type hints everywhere in `core`.

License: Apache-2.0 for the code. Data files carry their own attribution
(OSM/ODbL where applicable; see `SOURCES.md`).

---

## 4. Domain model

All entities live in `core/model.py` as pydantic models (or frozen
dataclasses with explicit validation). Field names below are normative.

### 4.1 Commodity

```
id: str            # "food" | "fuel" | "medicine" (extensible)
name: str
unit: str          # "t" (tonnes) — one unit per commodity, document it
priority: int      # lower = served first when capacity is scarce (medicine=1, food=2, fuel=3)
perishable_days: int | None   # None = not perishable (prototype may ignore, keep the field)
```

### 4.2 Node

```
id: str
name: str
kind: "region" | "port" | "source" | "hub"
lat: float
lon: float
population: int | None          # regions only; used to derive demand
country: str                    # ISO-2, e.g. "FI"
tags: dict[str, str]            # free-form (e.g. {"sea": "gulf_of_finland"})
```

- `region`: has population → has demand, holds stocks.
- `port`: entry point from abroad; has per-commodity import capacity.
- `source`: domestic production (refinery, agricultural region as producer).
- `hub`: transshipment node with no demand. Since v0.1 a hub may hold stock
  for the commodities named in its `holds=` tag (section 13.2).

### 4.3 Edge

```
id: str
from_node: str
to_node: str
mode: "road" | "rail" | "sea"
capacity_per_day: float         # tonnes/day, all commodities combined
transit_days: int               # >= 1 for v0; flows arrive after this many ticks
cost_per_tonne: float           # relative cost used by the solver
bidirectional: bool             # default true; loader expands into two directed arcs
commodities: set[str] | None    # v0.1, optional: commodities allowed on this edge; None = all
```

### 4.4 Demand and supply

`demand.csv`: `node_id, commodity_id, tonnes_per_day`.
For regions, generated from `population × per_capita_rate` (rates in
`commodities.json`), but stored explicitly so it can be overridden.

`supply.csv`: `node_id, commodity_id, tonnes_per_day` for ports (import
capacity) and sources (production). v0.1 adds optional `max_tonnes_per_day`
for ports (handling capacity available for redirected imports, section 13.3).

### 4.5 Stock

Per (node, commodity): `tonnes`. Initial stock = `policy.min_stock_days ×
daily_demand` for regions (if the policy mandates it), else a small default
(`default_stock_days` in commodities.json, e.g. 2 days of retail buffer).

### 4.6 Policy profile (`policies/*.json`)

```json
{
  "id": "finland_statutory",
  "name": "Finland — statutory stockpiling (approximate)",
  "description": "...",
  "min_stock_days": {"food": 90, "fuel": 60, "medicine": 180},
  "stock_holder": {"food": "state", "fuel": "companies", "medicine": "companies"},
  "release": {
    "trigger": "shortage",          // "shortage" | "manual" | "always"
    "delay_days": 2,                // days between trigger and release
    "max_release_fraction_per_day": 0.1
  },
  "emergency_budget": 50_000_000,   // currency units available for actions
  "holding_cost_per_tonne_day": {"food": 0.5, "fuel": 0.3, "medicine": 2.0}
}
```

Numbers above are placeholders; the real approximate values and their
sources go into `SOURCES.md`. `stock_holder` is informational in v0 (kept
for the engine stage; it will affect release behaviour later).

### 4.7 Scenario (`scenarios/*.json`)

```json
{
  "id": "hamina_winter_closure",
  "name": "Winter closure of HaminaKotka",
  "description": "...",
  "horizon_days": 30,
  "events": [
    {"day": 3,  "type": "node_capacity", "node_id": "port-hamina-kotka", "factor": 0.0, "until_day": 17},
    {"day": 3,  "type": "node_capacity", "node_id": "port-helsinki",     "factor": 0.6, "until_day": 20},
    {"day": 3,  "type": "edge_capacity", "edge_id": "rail-kouvola-lahti", "factor": 0.5, "until_day": 10},
    {"day": 5,  "type": "demand",        "node_id": "FI-01", "commodity_id": "food", "factor": 1.5, "until_day": 9}
  ]
}
```

Event types for v0: `node_capacity`, `edge_capacity`, `demand` (`node_id` may
be `"*"` for all regions since v0.1). Each has a
`factor` applied multiplicatively to the base value for `[day, until_day]`.
Multiple events on the same target multiply. Design the event dispatcher so
new types (e.g. `stock_loss`, `cost`) are one class each.

### 4.8 Actions (`core/actions.py`)

Actions are the intervention interface. Each action has a cost (deducted
from `emergency_budget`), a validation step and an effect on the state.

v0 action set:

```
ReleaseReserve(node_id, commodity_id, tonnes)        # move strategic stock into usable stock
AddCapacity(edge_id, extra_tonnes_per_day, days)     # e.g. rent extra trains/trucks
PrioritizeCommodity(commodity_id)                    # change solver priority order for N days
```

`apply(actions)` validates all actions (budget, ids, ranges) before applying
any; on validation error none are applied and an `ActionError` lists the
problems. Applied actions are recorded in the run log.

---

## 5. Data files and schemas

Keep `docs/DATA.md` identical in content to this section.

### 5.1 `nodes.csv`

```
id,name,kind,lat,lon,population,country,tags
FI-18,Uusimaa,region,60.25,24.95,1750000,FI,"sea=gulf_of_finland"
port-hamina-kotka,HaminaKotka,port,60.47,26.95,,FI,"sea=gulf_of_finland"
src-porvoo-refinery,Porvoo refinery,source,60.31,25.55,,FI,""
```

`tags` is `key=value;key=value`. Empty population for non-regions.

### 5.2 `edges.csv`

```
id,from_node,to_node,mode,capacity_per_day,transit_days,cost_per_tonne,bidirectional
road-FI-18-FI-05,FI-18,FI-05,road,20000,1,1.0,true
rail-kouvola-lahti,FI-08,FI-07,rail,15000,1,0.6,true
sea-hamina-FI-08,port-hamina-kotka,FI-08,road,30000,1,0.8,true
```

Ports connect to their host region with a short `road`/`rail` edge; the port
node itself carries the import capacity via `supply.csv`.

### 5.3 `commodities.json`

```json
{
  "commodities": [
    {"id": "medicine", "name": "Medicines", "unit": "t", "priority": 1, "per_capita_kg_per_day": 0.01},
    {"id": "food",     "name": "Food",      "unit": "t", "priority": 2, "per_capita_kg_per_day": 2.0},
    {"id": "fuel",     "name": "Fuel",      "unit": "t", "priority": 3, "per_capita_kg_per_day": 4.0}
  ],
  "default_stock_days": 2
}
```

### 5.4 `demand.csv`, `supply.csv`

See 4.4. Generated by `scripts/build_demand.py` from population and
per-capita rates, then committed (reviewable, overridable).

### 5.5 `SOURCES.md`

One line per number or group of numbers: what it is, value, source (URL or
reasoning), confidence (`measured` / `estimated` / `placeholder`). This file
is part of the deliverable; the demo is honest about data quality.

---

## 6. Simulation engine

### 6.1 Public interface (`core/engine.py`)

```python
class Simulation:
    def __init__(self, network: Network, scenario: Scenario, policy: Policy,
                 commodities: Commodities, seed: int = 0) -> None: ...
    def reset(self) -> State: ...
    def apply(self, actions: list[Action]) -> None: ...   # before step()
    def step(self) -> State: ...                           # advance one day
    @property
    def day(self) -> int: ...
    @property
    def done(self) -> bool: ...                            # day >= horizon
    @property
    def state(self) -> State: ...
    def run(self, until: int | None = None) -> RunResult: ...  # convenience
    def result(self) -> RunResult: ...
```

`State` is an immutable snapshot (day, stocks, in-transit flows, shortages,
effective capacities, budget, cumulative metrics). `step()` returns a new
`State`; it never mutates a previously returned one.

### 6.2 Tick order (one day)

Implement exactly this order; document it in `docs/MODEL.md`.

1. **Apply scenario events** active on this day → effective node/edge
   capacities and demand multipliers.
2. **Apply queued actions** (from `apply()`), update budget.
3. **Arrivals:** flows dispatched `transit_days` ago arrive at their
   destination stocks.
4. **Policy step:** evaluate release triggers; move strategic stock into
   usable stock subject to `delay_days` and `max_release_fraction_per_day`.
5. **Consumption:** each region consumes `demand` from usable stock.
   Shortfall = demand − consumed → recorded as `unmet_demand[node, commodity]`.
6. **Flow allocation (solver):** decide today's dispatches from supply
   nodes and surplus stocks toward nodes whose stock is below target
   (`target_stock_days`, from policy or default). See 6.3.
7. **Dispatch:** decrement origin stocks / supply, enqueue in-transit flows
   with arrival day = today + transit_days.
8. **Metrics & snapshot:** compute daily metrics, append to history, return
   `State`.

### 6.3 Flow solver (`core/solver.py`)

Define an interface and one reference implementation:

```python
class FlowSolver(Protocol):
    def allocate(self, graph: EffectiveGraph, supplies: dict[NodeId, float],
                 needs: dict[NodeId, float], commodity: Commodity) -> dict[EdgeId, float]: ...
```

**Reference implementation (v0): sequential min-cost flow per commodity.**

- Process commodities in `priority` order.
- For each commodity build a flow problem: super-source → supply nodes
  (capacity = available supply/stock surplus), edges with residual
  capacity (shared across commodities: subtract what earlier commodities
  used), demand nodes → super-sink (capacity = need), edge costs =
  `cost_per_tonne × transit_days` (or plain cost).
- Solve with `networkx.min_cost_flow` / `max_flow_min_cost`. networkx
  requires **integer** capacities and costs: scale tonnes by a fixed
  factor (e.g. ×10) and costs by ×100, then unscale. Encapsulate scaling
  inside the solver.
- Record per-edge flows; reduce residual capacities for the next commodity.

Known simplification (document it): sequential allocation is not a true
multi-commodity optimum. The interface allows a later LP-based solver
(OR-Tools / scipy) without touching the engine.

### 6.4 Policy rules (`core/policy.py`)

- Initial strategic stock per region = `min_stock_days × demand`.
- Strategic stock is separate from usable stock. It is released only via
  the policy's `release` rule or a `ReleaseReserve` action.
- Trigger `shortage`: when a region records unmet demand for a commodity,
  start a countdown of `delay_days`; after it, release up to
  `max_release_fraction_per_day × strategic_stock` per day while shortage
  persists.
- Holding cost accrues daily: `strategic_stock × holding_cost_per_tonne_day`
  → cumulative `holding_cost` metric. This is what makes the "cost of
  resilience" comparison possible.

### 6.5 Determinism

The only randomness allowed in v0 is optional noise on demand (`±x%`,
disabled by default). All randomness uses `self.rng =
numpy.random.default_rng(seed)`. `test_determinism.py` asserts two runs with
the same seed produce identical `RunResult` tables.

### 6.6 Results (`core/results.py`)

`RunResult` contains:

- `meta`: scenario id, policy id, seed, horizon, code version.
- `daily`: DataFrame `day, node_id, commodity_id, stock, strategic_stock,
  demand, consumed, unmet, inbound, outbound`.
- `edges_daily`: DataFrame `day, edge_id, flow, capacity, utilization`.
- `summary`: dict — total unmet by commodity, population-days in shortage,
  first shortage day per commodity, peak edge utilization, holding cost,
  action spend, total cost.
- `actions_log`: list of applied actions with day and cost.
- `to_parquet(dir)` / `from_parquet(dir)`.

### 6.7 Batch (`core/batch.py`)

`run_matrix(scenario, policies, seeds) -> DataFrame of summaries`. Used by
`scripts/run_batch.py` and, later, by the API. Runs are independent; use
`concurrent.futures` only if trivially safe.

---

## 7. Visualization (Streamlit, `ui/app.py`)

The UI is a client of `core`. It may hold a `Simulation` object in session
state and call `step()`/`apply()`; it never computes model logic itself.

Required features:

1. **Sidebar:** scenario selector, policy selector(s) (allow two for
   comparison), seed, "Run" button, action controls (ReleaseReserve,
   AddCapacity with cost shown), "Step 1 day" / "Run to end".
2. **Map (pydeck preferred, plotly acceptable):** regions as polygons or
   large circles coloured by shortage severity (ratio unmet/demand for the
   selected commodity), ports/sources as markers, edges as lines with width
   by utilization and colour by mode. Day slider scrubs through history.
3. **Comparison mode:** two maps side by side (policy A / policy B) sharing
   the day slider, and one chart of unmet demand per day for both runs.
4. **Metrics panel:** regions currently in shortage, days to first shortage
   per commodity, cumulative unmet, holding cost vs crisis losses for each
   policy.
5. **Footer:** "Prototype. Network and demand are approximate, built from
   open data; see SOURCES.md." Always visible.

Performance target: a 30-day run of the Finland network completes in under
2 seconds on a laptop; the UI must not re-run the simulation on every slider
move (cache `RunResult`).

---

## 8. Finland dataset (v0)

Build by hand; precision is not required, plausibility and provenance are.

- **Regions:** 19 maakunta, centroid lat/lon, population (Statistics
  Finland, latest available). Ids `FI-01 … FI-19` or ISO 3166-2 codes
  (`FI-18` = Uusimaa) — pick ISO codes.
- **Ports:** Helsinki, HaminaKotka, Turku, Naantali, Rauma, Hanko, Oulu,
  Kemi. Import capacity per commodity: estimate from public port
  statistics (tonnes/year ÷ 365), mark confidence.
- **Sources:** Porvoo refinery (fuel), one or two agricultural regions
  (food) as `source` nodes with modest production; medicine has no domestic
  source in v0 (imports only — this is what makes it interesting).
- **Edges:** adjacent regions connected by `road`; main rail corridors
  (Helsinki–Tampere–Oulu, Helsinki–Kouvola–Kuopio, Turku–Helsinki,
  Kouvola–Kotka/Hamina, Tampere–Pori/Rauma, Oulu–Kemi–Rovaniemi) as `rail`.
  Capacities as rough estimates; cost: rail cheaper than road.
- **Policies:** `finland_statutory.json` with approximate statutory minimums
  (fuel per EU/Finnish obligations, medicines by category, grain/food) and
  `no_stockpiling.json` (only `default_stock_days`). Cite sources; mark
  values `estimated`.
- **Scenario:** `hamina_winter_closure.json` as in 4.7, plus `baseline.json`
  with no events (used for sanity checks: no shortages in baseline).

Sanity requirements for the dataset: baseline run has zero unmet demand;
the closure scenario without stockpiling produces a visible medicine
shortage within ~a week in eastern/south-eastern regions; with the statutory
policy the shortage is delayed or avoided. If these do not hold, adjust
estimates and record the reasoning in `SOURCES.md`.

---

## 9. Testing

Minimum tests (pytest), all deterministic and fast:

- `test_loader.py`: valid files load; missing node referenced by an edge
  fails with a message containing the edge id; negative capacity fails.
- `test_engine_small.py`: 3-node network (source → hub → region), hand
  computed expectations for stock levels and unmet demand over 5 days,
  including a transit delay.
- `test_scenario.py`: an `edge_capacity` event with factor 0 stops flow on
  that edge for the given days and restores it after.
- `test_policy.py`: with `min_stock_days = 10` a 5-day cut-off causes no
  shortage; with `0` it does; release delay is respected.
- `test_actions.py`: budget is enforced; invalid action ids rejected
  atomically.
- `test_determinism.py`: identical results for identical seeds; different
  seeds differ when noise is enabled.

CI is optional for v0 but `pytest` must pass locally before the demo.

---

## 10. Extension points (why the structure is the way it is)

Do not implement these now; keep the seams clean so they can be added.

| Future need | Seam in v0 |
|---|---|
| OSM-based network import | `loader.py` produces `Network`; an `osm_importer.py` will produce the same object. |
| Better solver (LP multi-commodity, agent-based) | `FlowSolver` protocol. |
| Calibration against historical events | `RunResult.daily` tables + `batch.run_matrix`. |
| HTTP API (batch, streaming, sessions) | `Simulation.step()` already is the session loop; `State` serializes to JSON. |
| Other clients (API, notebooks, optimisers) | Actions + budget already exist; any client builds on `State` and `apply`, never inside `core`. |
| Several actors on one network | One `Simulation` per world; actors map to node subsets via a future `Actor` concept; actions already carry a target. |
| Country policy catalogs | Policy files are data; a catalog is a directory. |
| Sensitive real data | Data directory is swappable; code never embeds data. |

Rules that protect these seams:
- No module in `core` imports from `ui` or `scripts`.
- No Finland ids or numbers in Python code (tests may use tiny synthetic
  networks).
- Everything a client needs is on `State` / `RunResult`; if the UI needs
  something not there, add it to those objects, not by reaching into engine
  internals.

---

## 11. Definition of done (prototype v0)

- [ ] `pip install -e .` and `streamlit run solmu/ui/app.py` work from a
      clean checkout; README explains both in under 10 lines.
- [ ] `scripts/run_scenario.py --scenario hamina_winter_closure --policy
      finland_statutory --seed 0` writes a results directory with parquet
      files and prints the summary.
- [ ] Baseline scenario produces no shortages; closure scenario shows the
      expected contrast between the two policies.
- [ ] UI shows map, day slider, side-by-side policy comparison, metrics,
      at least two working actions with budget deduction.
- [ ] `pytest` passes; `ruff check` clean.
- [ ] `docs/MODEL.md` describes the tick order, solver and simplifications;
      `data/finland/SOURCES.md` lists provenance and confidence for all data.
- [ ] A 3-minute screen recording of the demo can be made without
      workarounds.

---

## 12. Implementation order (dependency order, not a schedule)

1. `model.py`, `loader.py`, tiny synthetic test network, `test_loader.py`.
2. `engine.py` tick loop with a trivial solver (greedy nearest supply),
   `results.py`, `test_engine_small.py`.
3. `solver.py` reference min-cost flow; swap in; tests still pass.
4. `scenario.py` events, `policy.py` rules, `actions.py`; their tests.
5. Finland dataset + `SOURCES.md`; sanity checks from section 8.
6. `scripts/run_scenario.py`, `batch.py`, `scripts/run_batch.py`.
7. Streamlit UI: single run → comparison → actions → polish.
8. `docs/MODEL.md`, README, final test pass.

Each step ends with passing tests and a short note in `docs/MODEL.md` if a
modelling decision was made. When unsure between a simpler model and a more
realistic one, choose the simpler one, document the simplification, and keep
the interface general enough to replace it.

---

## 13. Realism extensions (v0.1) — decided 2026-10-21

Prototype v0 (sections 1–12) is complete. The product owner reviewed the demo
scenario and asked that nodes react the way real ones do, while staying at the
level of aggregate properties (route capacity, warehouse and port throughput
and stock). No individual ships or trucks, no export accounting yet. The
following mechanisms are part of the specification from now on. Each is
**opt-in via data**: with the default values every dataset behaves exactly as
in v0, so swapping countries still means swapping data files only.

### 13.1 Why

- Medicines and most food in Finland move port → central wholesaler warehouse
  → region (Tamro/Oriola; Kesko/Inex). Fuel moves refinery/depot → region.
  Without a warehouse layer, shortages appear in the wrong places and at the
  wrong time.
- A closed port does not delete its cargo. Ships divert to other ports after
  1–3 days, limited by those ports' spare handling capacity; the rest waits,
  and part of it is lost to other markets. When the port reopens, the backlog
  is handled as a surge.
- Edges carry specific kinds of cargo (tank wagons vs containers).
- Disruptions trigger demand spikes (stockpiling by households).

### 13.2 Warehouses (hubs) with stock and catchments

- `Commodity.via_hubs: bool = false`. For a `via_hubs` commodity ports/sources
  ship only to hubs; regions receive only from hubs. Otherwise unchanged.
- A hub holds the commodities listed in its node tag `holds=medicine|food`
  (pipe-separated inside the tag value). A hub without `holds` is pass-through.
- Catchment: each region is assigned to the cheapest-path hub holding the
  commodity (base graph). A hub's served demand is the sum of its regions'
  base demand. Hub target stock = `(hub_stock_days + lead time from
  ports/sources) × served demand`; `hub_stock_days` is set globally in
  `commodities.json` (default 7) and may be overridden per commodity.
- Allocation becomes two tiers per `via_hubs` commodity (ports/sources → hubs,
  then hubs → regions), both through the existing `FlowSolver`, on shared
  residual capacities. A hub keeps one day of served demand as safety stock.
- Hubs never consume. Strategic (policy) stock stays per region in v0.1.

### 13.3 Import redirection between ports

- `supply.csv` gains an optional `max_tonnes_per_day` (default =
  `tonnes_per_day`, i.e. no spare capacity, no redirection).
- Per commodity and day, a port's direct offer is
  `min(baseline × node_factor, max)`. Its shortfall enters a national
  "diverted" pool that becomes available after
  `import_redirect_delay_days` (default 2) and is distributed to ports in
  proportion to spare capacity. The remainder waits as backlog; each day a
  fraction `import_backlog_loss_fraction_per_day` (default 0) of the waiting
  cargo is lost. Both parameters live in `commodities.json`.
- The pool is national: ships at sea have no geography in this model. That is
  the intended abstraction level.
- `State` exposes `import_backlog`, `import_lost`; `RunResult.summary` has
  peak backlog and lost tonnes per commodity.

### 13.4 Edge commodity restriction

- `edges.csv` gains an optional `commodities` column (pipe-separated ids,
  empty = all). An edge not allowing a commodity is absent from that
  commodity's graph; capacity stays shared among the allowed commodities.
  Lead times and catchments use the same per-commodity graphs.

### 13.5 Demand spikes

- A `demand` event may use `node_id: "*"` for all regions. Scenarios model
  stockpiling by households as explicit per-commodity events. An endogenous
  reaction (demand rising where a shortage is observed) is a candidate for a
  later step and needs a sourced parameter.

### 13.6 Finland dataset v0.1 (data step for 13.2–13.5)

- Hubs: two pharmaceutical wholesaler warehouses (capital region, Tampere
  area) holding `medicine`; two or three grocery central warehouses (capital
  region, Tampere, Oulu) holding `food`. `via_hubs` true for medicine and
  food, false for fuel.
- Ports: `max_tonnes_per_day` ≈ 1.3–1.5 × baseline for container/ro-ro
  ports; liquid bulk ports get less headroom. Redirect delay 2 days, backlog
  loss ≈ 0.1/day. All values `estimated`/`placeholder` in `SOURCES.md`.
- Edges: rail corridors restricted where realistic (e.g. tank-wagon lines for
  fuel); the Åland ferry carries food and medicine only.
- Scenario `hamina_winter_closure`: keep the port events, add a nationwide
  food demand spike (`"*"`, ×1.3, days 3–8). Add a second scenario that
  stresses the system (e.g. `gulf_of_finland_ice`: all Gulf of Finland ports
  at reduced capacity for 10 days plus a spike) because a single-port closure
  with realistic stocks is expected to produce little or no shortage of
  essentials. That outcome is correct and must be reported as such.
- Sanity requirements replace those of section 8: baseline has zero unmet;
  the single-port scenario shows redirection (other ports above baseline,
  non-zero backlog) and at most a small, late shortage; the stress scenario
  shows a clear contrast between the two policies.

### 13.7 UI

- Hubs appear as a distinct marker with stock level in the tooltip; the
  metrics panel shows import backlog and lost tonnes per commodity; region
  colouring and shortage counts ignore hub rows.

### 13.9 Anticipatory reserve release (decided 2026-10-21)

Reality: the national emergency supply agency decides to release reserves
when it sees import capacity fall, not when shelves are already empty. The
reactive `shortage` trigger (6.4) therefore understates what a stockpiling
policy can do. A new policy trigger makes the timing of the decision an
explicit, data-driven lever:

- `release.trigger: "on_disruption"` with parameters
  `disruption_threshold` (fraction, default 0.3) and
  `disruption_persistence_days` (int, default 1). Per commodity, the engine
  computes the national effective import offer (ports and sources after node
  factors, before redirection) relative to the undisrupted baseline. When the
  drop exceeds the threshold for `persistence` consecutive days, the release
  countdown (`delay_days`) starts for every region holding strategic stock of
  that commodity; release then proceeds at `max_release_fraction_per_day`
  while the disruption persists or any region records unmet demand.
- Existing triggers `shortage`, `manual`, `always` are unchanged and remain
  the default (`shortage`). Which trigger a country uses is a property of its
  policy file. Finland's statutory policy switches to `on_disruption`
  (threshold 0.3, persistence 1, delay 2); the reasoning goes to `SOURCES.md`.
- `State` exposes the current import-drop fraction per commodity so the UI
  can show why a release started. Sanity requirement added to 13.6: in the
  stress scenario the statutory policy's first shortage day must be later
  than without stockpiling, or absent.

### 13.10 Historical validation: stevedore strike 4–19 March 2010 (decided 2026-10-21)

The model must be checked against a real disruption before anyone trusts its
numbers. The first case is the Finnish stevedore strike of 4–19 March 2010
(16 days). Sources: University of Turku / Centre for Maritime Studies report
A55 "Finnish critical industries, maritime transport vulnerabilities and
societal implications" (STOCA project, interviews with critical industries);
Boston.com/AP 19 Mar 2010; Swarthmore Global Nonviolent Action Database;
Finnish press on the 2023 and March 2024 AKT strikes for exemptions practice.

Observed facts to reproduce (the validation ledger, kept in
`docs/validation/2010_port_strike.md`):
- Public ports stopped; ~80% of foreign trade halted; industry-owned private
  ports (notably the Kilpilahti oil port of the Porvoo refinery) operated
  normally.
- Liner ro-ro ferries to Sweden, Estonia and Germany kept running because
  truck units with drivers need no stevedores: companies rerouted via Turku,
  Naantali, Vaasa and Helsinki–Tallinn, at +500–1000 EUR per unit. Container
  feeders from Hamburg/Rotterdam/Antwerp stopped.
- Companies raised inventories before the strike (it was announced weeks
  ahead; the original deadline of 19 Feb was postponed by two weeks).
- No shortage of medicines, basic food or fuel for consumers was reported.
  Imported fresh produce was the first retail gap. Fuel distribution saw
  local hiccups and hoarding, not shortage (Porvoo kept running; obligatory
  oil stocks exist). In the 2023/2024 strikes medicines and energy cargo were
  explicitly exempted; in 2010 pharma imports went via Vuosaari with Kotka,
  Turku, Hanko as alternatives.
- Export industry (paper, metals) shut plants within days; the backlog took
  weeks to clear after the strike. Government loss estimate ~100 MEUR/day.

Model additions needed (opt-in, defaults unchanged):
- `node_capacity` events may carry an optional `commodity_id`; absent = all
  commodities (today). Lets a strike stop containers and bulk while trucked
  food and medicine continue through the same port.
- New event type `stock_target` (`node_id` or `"*"`, optional
  `commodity_id`, `factor`): scales the order-up-to target of regions and
  hubs for the window. Models anticipatory inventory building before an
  announced disruption.

Deliverables: scenario `port_strike_2010.json` for both policies; a script
`scripts/validate_2010.py` that prints model outcomes against each ledger
line (match / mismatch / not modelled); the ledger document with sources;
an analysis of every mismatch with the most likely cause (model, data or
scenario) and what would fix it. Data changes made for this case go to
`SOURCES.md`. Export losses are not modelled and are reported as such.

### 13.8 Definition of done (v0.1)

- [ ] All v0 tests pass unchanged; new mechanisms have hand-computed tests.
- [ ] Finland dataset uses hubs, port headroom, edge restrictions and a demand
      spike; every new number is in `SOURCES.md`.
- [ ] `scripts/sanity_check.py` enforces 13.6 and passes.
- [ ] A 30-day Finland run stays under 2 s.
- [ ] `docs/MODEL.md` and `docs/DATA.md` describe 13.2–13.5 and 13.9 as built.
