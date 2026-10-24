# Model as implemented

This file documents the model as built in `solmu/core`. Modelling decisions and
simplifications are recorded here; where this file and `IMPLEMENTATION_PLAN.md`
disagree, this file describes the code and the deviation is listed in the text.

## Status

Prototype v0 plus hubs, import redirection and wildcard events: entities and loader, tick engine (`engine.py`, `state.py`,
`results.py`), flow solvers (greedy and min-cost-flow), events (`scenario.py`), policy release
rule (`policy.py`), actions (`actions.py`), batch runner, Finland dataset, CLI scripts and
Streamlit UI. `scripts/sanity_check.py` passes.

Not implemented:
- Surplus stock as a supply source (regions above target are not used as sources).
- Joint multi-commodity optimisation (allocation is sequential by priority).
- Re-routing or splitting of shipments once dispatched; disruption of nodes mid-route; stock at ports.
- Reset of the shortage or disruption release clock.
- `Commodity.perishable_days` is parsed but unused; `Policy.stock_holder` is informational.
- Out of scope per the plan: OSM import, HTTP API, agent-based simulation.

## Entities

Defined in `solmu/core/model.py` (pydantic v2, frozen, unknown fields rejected):
`Commodity`, `Commodities`, `Node`, `Edge`, `Arc`, `DemandRecord`,
`SupplyRecord` (optional `max_tonnes_per_day`), `Network`, `Policy` (with `ReleaseRule`; optional `target_stock_days`,
default None; `capacity_rental_cost_per_tonne_day`, default 10.0), `Scenario`
(`demand_noise_fraction`, default 0) and four
events (`NodeCapacityEvent`, `EdgeCapacityEvent`, `DemandEvent`, `StockTargetEvent`, discriminated
on `type`, sharing `day`, `until_day`, `factor`).

Arc expansion: `Network.arcs()` returns one directed arc per edge with the
same id (`<id>`, from_node to to_node), plus `<id>:rev` (reversed) when the
edge is bidirectional. Both arcs carry `edge_id` and the edge's capacity;
capacity is per arc, not shared between the two directions (assumption: edge
`capacity_per_day` is the limit in each direction). Events target `edge_id`,
so an `edge_capacity` event affects both arcs.

Assumptions (not in the spec):
- `per_capita_kg_per_day` is a field of `Commodity` (spec 5.3 has it, 4.1 omits it); default 0.
- `Node.country` defaults to empty; lat/lon range-checked.
- Event windows are inclusive `[day, until_day]`; `day >= 0`.
- `ReleaseRule.delay_days` defaults to 0, `max_release_fraction_per_day` to 1 and must be in [0, 1].
- Policy JSON is plain JSON (no `50_000_000` literals or comments as in spec 4.6).
- `load_network` rejects duplicate node/edge ids and duplicate (node, commodity)
  rows in demand/supply, but does not check commodity ids; that needs the
  commodity catalogue and is left to the engine/step 4.
- Event targets (node/edge/commodity ids) are validated against the network
  in `Simulation.__init__` (`ValueError` naming scenario id, event index and target),
  not at load time.
- Loader errors are `LoadError(ValueError)` naming file, record (row number
  and id) and field.

## Tick order

`Simulation.step()` simulates day `t = state.day` and returns a state with
`day = t + 1`; `done` is `day >= horizon_days`. Daily result rows carry the
tick day `t` (0 .. horizon-1) and end-of-day values. Order, one private method each:

1. `_apply_events` - `scenario.active_modifiers(scenario, t)` gives node/edge/demand
   factors; demand = base x factor (x noise if enabled).
2. `_import_drop` - per commodity the national import drop and, for `on_disruption`, the
   release clock (see Policy rules).
3. `_apply_actions` - actions queued by `apply()` take effect; budget and `action_spend` change.
4. `_arrivals` - shipments with `arrival_day <= t` are added to usable stock.
5. `_policy_step` - `policy.release_step` moves strategic stock into the holder's usable stock
   (regions, hubs) or into the day's offer (source nodes).
6. `_consume` - consumed = min(demand, usable stock); unmet = demand - consumed
   (shortfalls below 1e-9 t are treated as 0: floating-point dust).
7. `_allocate` - per commodity in solver order: port offers (with import redirection), then
   one solver call, or two for `via_hubs` commodities (hub refill, then hub to region).
8. `_dispatch` - planned flows become shipments (hub shipments reduce hub stock); capacity
   use is recorded; reserve offered at a source node and not shipped returns to the reserve.
9. `_snapshot` - result rows, cumulative metrics, holding cost, new immutable `State`.

State holds read-only mappings (`MappingProxyType` over private copies) and tuples
of frozen dataclasses, so a returned `State` never changes. Besides stocks it carries
`target_stocks` (order-up-to level in tonnes, for the UI), `release_started` (day the
shortage release clock started per region and commodity), and the active
`capacity_boosts` / `priority_boosts`.

Initial stocks: usable = target stock = `(cover_days + lead time) x base demand`
(see below; deliberate deviation from spec 4.5); strategic = `policy.min_stock_days[c] x
base demand`, held separately by whoever `Policy.strategic_stock_at` names (see "Strategic
stock holders"; default: the regions). Only `region` nodes have demand (demand
records on other kinds, and unknown commodity ids in demand/supply/policy, raise
`ValueError` at construction). Holding cost accrues daily as `strategic_stock x
holding_cost_per_tonne_day`, on end-of-day strategic stock including the unprocessed part
(usable stock carries no holding cost).

## Strategic stock holders

`Policy.strategic_stock_at[c]` is a list of holders with shares summing to 1 (default: one
`region` holder, the v0 behaviour). Per holder kind:

- `region`: `share x min_stock_days x region demand` at every region; released into the
  region's usable stock.
- `hub` (needs a `via_hubs` commodity with holding hubs): `share x min_stock_days x served
  demand` at every hub holding `c`; released into the hub's stock, from where tier 2 ships it.
  Regions without a catchment are not covered by hub stock.
- `node` (a `source` node): `share x min_stock_days x national demand` at that node, split into
  releasable stock and `requires_processing` stock (`State.strategic_unprocessed`), e.g. crude
  at a refinery. Released tonnes become part of the node's **offer of the day** and reach regions
  through the solver with real lead times. What is not shipped returns to the releasable stock at
  the end of the day; the node's own production is counted as shipped first. The unprocessed part
  is releasable only on days the node's factor is above 0 (processed into product on release).
  A node holder with `release: automatic` (companies' obligatory stock kept as their own
  operating inventory) is on offer in full every day without any release rule
  (`State.automatic_stocks`); shipped tonnes are taken from automatic stock before policy stock,
  and the rest stays. It cannot carry `requires_processing`.

Release rules work on the combined releasable amount per holder key. Hubs and nodes have no
demand of their own, so for the `shortage` trigger they stand in for the regions: the clock
starts when any region had unmet demand of `c` the previous day, and they release while any
region's usable stock is below its demand. `on_disruption` is national already. `ReleaseReserve`
accepts any holder node id and releases releasable stock only. The daily `strategic_stock`
column shows hub stock on hub rows; node-held stock is only in `State`.

## Needs, targets and supply

- **Lead time** `L(region, c)`, computed once in `__init__` on the base (undisrupted)
  arc graph with weight = `transit_days`: suppliers (nodes with a positive supply record
  for c) are sorted by shortest-path transit days to the region; their supply is summed
  until it covers the region's base demand, and `L` is the distance of the last supplier
  needed. When the nearest supplier alone is large enough this equals the plain minimum
  distance. Deviation from the plain minimum: a region whose
  nearest supplier is small (e.g. a 50 t/day port next to a 234 t/day region) is in
  practice fed from far away; with the plain minimum its pipeline is longer than its
  target cover, which gives permanent stock-outs (seen in the Finland baseline).
  Unreachable: `L = 0` (the region simply cannot be served and runs short once its stock
  is gone). Exposed as `Simulation.lead_times`.
- **Target** `target_stock_days = cover_days + L`, with `cover_days =
  commodities.default_stock_days`, or `policy.target_stock_days[c]` when the policy sets it.
  Target stock = target days x effective demand of the day.
- Need = max(0, target - usable stock after consumption - tonnes of that commodity still
  in transit to the node).
- **Initial usable stock = target stock** (deviation from spec 4.5, which says
  `default_stock_days x demand`): the pipeline starts empty, so a smaller start would make
  every region short in the first `L` days before the first shipment lands; starting at the
  target avoids a warm-up. It still covers only `cover + L` days: if the solver routes
  through paths longer than that the region runs short (data issue, see `SOURCES.md`,
  Oulu fuel).
- Supply nodes (any node with a `supply.csv` record) offer `tonnes_per_day x
  node_factor` per commodity per day. Supply is not stocked: unused supply is lost.
- A node with both supply and need nets them first (`net_supplies_needs`); the
  matched tonnes are delivered to its stock at once, no transit.
- Regions with stock above target are NOT used as sources (spec 6.2 item 6 mentions
  "surplus stocks"). The solver interface already accepts arbitrary `supplies`.
- Ports never hold stock; hubs hold stock only for `via_hubs` commodities they `holds`;
  otherwise flows pass through in the same dispatch.
- Capacity: each arc has `(capacity_per_day + active boosts) x edge_factor` per direction per
  day, shared by all commodities (residual graph passed down in solver order). `node_factor`
  scales only the supply offered at that node (ports, sources); it does not limit the
  throughput of regions or hubs.
- Solver order: commodities by priority, except commodities under an active
  `PrioritizeCommodity`, which come first (among themselves by priority).

## Hubs and catchments

Opt-in: `Commodity.via_hubs` (default false). For such a commodity ports and sources ship only
to hubs and regions receive only from hubs. A hub handles a commodity if its node tag `holds`
lists it (`holds=medicine|food`; `|` because `;` separates tags). A hub without `holds`, or a
commodity that is not `via_hubs`, behaves exactly as before: pass-through, no stock (a `holds`
entry for a non-`via_hubs` commodity is ignored). A `via_hubs` commodity that no hub holds raises
`ValueError` at construction. Hubs never consume and may not have demand records.

- **Catchment** (`Simulation.catchments[commodity][region] = hub`): each region belongs to the
  handling hub with the lowest shortest-path cost (`cost_per_tonne x transit_days`, base graph,
  ties by hub id); regions no hub can reach are absent (lead time 0, runs short once its own
  stock is gone). It sizes stocks only: the solver may ship from any hub with surplus.
- **Served demand** of a hub = sum of its regions' base demand (events do not change it).
- **Hub target** = `(hub_stock_days + L_hub) x served demand`; `hub_stock_days` is
  `Commodity.hub_stock_days` or else `Commodities.hub_stock_days` (default 7). `L_hub` uses the
  same supplier-coverage rule as regions, with ports/sources as suppliers. Initial hub stock =
  target. Hub need = target - stock - tonnes in transit to the hub.
- **Region lead time** for a `via_hubs` commodity is the transit time from its catchment hub
  (simplification of "suppliers = hubs": the nearest-by-days hub could belong to another
  catchment). Region target = `(cover + L) x demand` as before.
- Hub stocks are in `State.stocks` and `State.target_stocks`; `RunResult.daily` has hub rows
  (demand, consumed, unmet 0; `strategic_stock` is the hub-held strategic stock, 0 unless the
  policy places stock at hubs). They do not affect shortage metrics. Where strategic stock sits
  and how it is released is set by `Policy.strategic_stock_at` ("Strategic stock holders").

## Two-tier allocation

Per `via_hubs` commodity, in solver order, two solver calls share the residual arc capacities:
tier 1 supplies = port/source offers, needs = hub needs; tier 2 supplies = hub surplus
`max(0, stock - safety)` with `safety` = 1 day of served demand, needs = region needs. The safety
stock stops a hub from emptying in one tick. A tier-2 shipment reduces the hub stock at
dispatch. Hub need is computed before the tier-2 dispatch of the same day, so hub stock lags one
day behind (steady state: hub stock at allocation = target - one day of served demand;
see `tests/test_hubs.py`). Tier 1 runs before tier 2, so hub replenishment has first call on shared
arcs. Other commodities use one call as before.

## Import redirection and backlog

Opt-in through `supply.csv` `max_tonnes_per_day` (default = baseline, no headroom). Per tick, per
commodity, for `kind == "port"` supply nodes only (sources are unaffected):
baseline `b = tonnes_per_day`, cap `= max x node_factor`, direct offer `d = min(b x node_factor,
cap)` (equals the spec's `min(b, cap)` for factors <= 1; a factor above 1 keeps today's
behaviour of scaling the offer), shortfall `s = max(0, b - d)`.

- Shortfalls of day `t` enter a **national** per-commodity pool available from day
  `t + import_redirect_delay_days` (default 2). The pool is national on purpose: ships at sea have
  no geography in this aggregate model.
- Each day the available pool plus the ready backlog is spread over ports in proportion to spare
  capacity `cap - d`, capped by it. Port offer = `d + redirected`. The remainder is the backlog
  and waits for later days, including after a closed port reopens (surge, bounded by spare capacity).
- `import_backlog_loss_fraction_per_day` (default 0): after distribution, that fraction of the
  remaining *available* backlog is lost (sold elsewhere, ships turning back). Pending tonnes still
  inside the delay are not reduced. Cumulative loss is `State.import_lost` and
  `summary["import_lost_by_commodity"]`.
- `State.import_backlog` = ready + pending tonnes per commodity (`import_ready`, `import_pending`
  are the parts); `summary["backlog"]` is the peak of `import_backlog` per commodity. With the
  default max there is no spare, so a closure only grows the backlog (cargo waiting forever) and
  flows are unchanged.

## Wildcard events

A `demand` event may have `node_id: "*"`: it applies to every region (`active_modifiers`
takes the region ids and expands it). Validation accepts it.

## Edge commodity restrictions

`Edge.commodities` (optional frozenset, default None = all) limits an edge to those commodities.
Unknown ids raise `ValueError` naming the edge in `Simulation.__init__`. For each commodity the
solver sees only the arcs that allow it; the residual capacity of an arc is still shared by
all commodities allowed on it. Lead times, hub catchments and hub lead times use the same
per-commodity graph, so a region that no allowed arcs reach has lead time 0 for that commodity
and runs short once its stock is gone (no error). Capacity events act on the edge as usual.

## Flow solver

`FlowSolver.allocate(graph, supplies, needs, commodity) -> {arc_id: tonnes}` with
`EffectiveGraph` (arcs with residual capacity, `cost = cost_per_tonne x transit_days`).
Flows are per directed arc id (`edge` and `edge:rev`).

- `MinCostFlowSolver` (engine default, spec 6.3): super-source and super-sink,
  `networkx.max_flow_min_cost`, so it first maximises delivered tonnes and then
  minimises cost. Tonnes x10 (capacities, supplies, needs floored so the plan
  never exceeds the true residual, i.e. up to 0.1 t per quantity is dropped) and
  cost x100 (rounded to nearest). networkx has no multigraph flow, so every arc
  is routed through its own midpoint node. Zero-capacity arcs are skipped.
- `GreedySolver`: needs served in node-id order, each from the nearest supply by
  shortest-path cost over arcs with residual capacity, bounded by bottleneck.
  Not optimal (see `test_min_cost_beats_greedy...`); kept as a baseline.
- Sequential per commodity (priority order), not a joint multi-commodity optimum.

Multi-hop semantics: arc flows are decomposed (`decompose_flows`) into
origin-to-destination paths. One `Shipment` per path: it consumes capacity on
every arc of the path on the dispatch day, and arrives at the destination after
the SUM of the arcs' `transit_days`. It is not tracked at intermediate nodes
(no stock or disruption mid-route). Circulating zero-cost cycles are dropped.

## Results

`RunResult.daily` has one row per day x region x commodity (`inbound` = tonnes
arrived that day, incl. local matches; `outbound` = tonnes dispatched from the
node, currently always 0 for regions). `edges_daily` has one row per edge per
day: `flow` = busier direction's tonnes, `capacity` = effective per-direction
capacity, `utilization` = flow / capacity (0 when capacity is 0). Summary:
`total_unmet_by_commodity`, `total_unmet`, `population_days_in_shortage`
(population of each region-day with any unmet demand), `first_shortage_day`
(None if never), `peak_edge_utilization`, `holding_cost`, `action_spend`,
`shortage_cost_by_commodity` and `shortage_cost` (unmet tonnes x `Commodity.shortage_cost_per_tonne`,
default 0 so nothing is priced unless the catalogue says so),
`total_cost = holding_cost + action_spend + shortage_cost`. Pricing unmet demand is what makes the
"cost of resilience" comparison two-sided: holding cost on one side, crisis losses on the other.
`RunResult.imports_daily` has one row per day x commodity: `offer` (national import offer before
redirection), `baseline`, `drop`, `backlog` (end of day, ready + pending), `lost` (tonnes lost
that day). `to_parquet` writes `daily.parquet`, `edges_daily.parquet`, `imports_daily.parquet`,
`result.json`; `from_parquet` accepts older folders without the imports file.

## Policy rules

`policy.release_step(rule, day, strategic, stocks, demand, prev_unmet, started)`, called
every tick before consumption, returns tonnes to move from strategic into usable stock.
A release is a fraction (<= 1) of the strategic stock left, so it never exceeds it.

- `manual`: never releases automatically (only `ReleaseReserve`).
- `always`: every day from day 0, `max_release_fraction_per_day x current strategic stock`.
- `shortage`: the clock of a (region, commodity) starts on the first day whose previous
  day recorded unmet demand (so the first unmet day itself cannot be prevented). Release
  is allowed from `started + delay_days`. On such days the max fraction of current strategic
  stock is released if usable stock after arrivals is below today's demand ("shortage
  persists"; alternative considered: previous-day unmet > 0). The clock is never reset
  within a run, so after a first shortage the reserve is drawn just-in-time whenever stock
  would not cover the day. Spec 6.4 does not say when the countdown resets; this is the
  simplest choice.
- `on_disruption` (spec 13.9, anticipatory): `release.disruption_threshold` (0-1, default 0.3)
  and `release.disruption_persistence_days` (>= 1, default 1). Per commodity and day the
  engine computes `import_drop = max(0, 1 - offer / baseline)`, with `offer` the summed offers
  of all ports and sources after node factors and handling caps and before redirection, and
  `baseline` the summed `tonnes_per_day` (baseline 0 gives drop 0; the clamp only matters for
  node factors above 1). `State.import_drop` exposes it. `policy.disruption_step` (pure) keeps
  a streak of consecutive days with drop > threshold; when it reaches the persistence the
  countdown starts (`State.disruption_started`, day of that event). Release is allowed from
  `started + delay_days`: every region holding strategic stock of the commodity releases
  `max_release_fraction_per_day x strategic stock` on days when drop > threshold or any region
  had unmet demand of the commodity the previous day. When neither holds, release pauses.
  Modelling choices: the clock is national per commodity (one decision of the supply agency,
  not per region); it starts once per run and is never reset, so a later disruption resumes
  release at once without a new countdown or persistence check; the drop is measured before
  redirection on purpose (redirected cargo is the response, not a sign the disruption is over).
  Day-`t` events are visible to the trigger on day `t`, so with delay 0 the release happens on
  the first day of the drop.
- Holding cost as above.

## Scenario events

`scenario.py` has one handler class per event type, registered in `HANDLERS` (a new type
is one class plus one dict entry, plus its pydantic event model). A handler has
`problems(event, ids)` (target validation) and `apply(event, modifiers)`.
`active_modifiers(scenario, day)` multiplies the factors of all events whose inclusive window
`[day, until_day]` contains `day`, giving `node_factor`, `edge_factor`,
`demand_factor[(node, commodity)]`, `node_commodity_factor[(node, commodity)]` and
`target_factor[(node, commodity or None)]` (absent = 1). `active_modifiers(scenario, day,
region_ids, hub_ids)` expands `"*"` to the regions (`demand`) or regions plus hubs (`stock_target`).

- `node_capacity` scales supply at the node; `edge_capacity` scales both arcs of an edge
  (and the capacity reported in `edges_daily`); `demand` scales a region's demand.
- `node_capacity` with the optional `commodity_id` (spec 13.10) scales only that commodity at the
  node (a strike stops containers and bulk while trucked food and medicine pass through the same
  port). The factor used for (node, commodity) is `node_factor[node] x
  node_commodity_factor[(node, commodity)]`; it is applied in the port offers (including the
  handling cap, so a port at 0.5 can handle at most 0.5 x its `max_tonnes_per_day`) and in the
  national `import_drop`. `State.effective_node_factor` shows the node-wide part only. Default
  `None` = all commodities, unchanged behaviour.
- `stock_target` (spec 13.10; `node_id` = a region, a hub or `"*"` for all regions and hubs;
  optional `commodity_id`, `None` = all; `factor >= 0`) multiplies the order-up-to target of the
  matching regions and hubs on the window: region target `target_days x demand x factor`, hub
  target `hub target x factor` (factors with and without `commodity_id` multiply). Need =
  target - stock - in transit as before, so a factor above 1 makes nodes order more
  (anticipatory stocking, limited by supply and arc capacity), a factor below 1 lets them run
  stock down. Initial stocks are not changed, hub safety stock (one day of served demand) is not
  scaled, and `State.target_stocks` shows the scaled targets.
- Events whose node/edge/commodity ids are not in the network (or a demand event on a
  non-region) raise `ValueError` at `Simulation.__init__`.

## Actions

`apply(actions)` validates everything (queued plus new) and raises `ActionError`
(`.problems` lists all issues) without queuing anything if any problem exists. Queued
actions take effect at the start of the next `step()` (tick day `t`), are logged in
`actions_log` as `{day, action, cost, params}`, decrement `budget` and raise
`action_spend`. Validation covers ids, positive amounts, total `ReleaseReserve` per key
within strategic stock, and total cost within the budget.

| Action | Effect | Cost |
|---|---|---|
| `ReleaseReserve(node_id, commodity_id, tonnes)` | strategic -> usable stock at a region or hub, or the day's offer at a source node | 0 |
| `AddCapacity(edge_id, extra_tonnes_per_day, days)` | extra per-direction capacity on days `t .. t+days-1`; added before the edge factor, so a closed edge stays closed | `extra x days x policy.capacity_rental_cost_per_tonne_day` (default 10.0) |
| `PrioritizeCommodity(commodity_id, days)` | commodity first in solver order on days `t .. t+days-1` | 0 |

## Randomness and determinism

`self.rng = numpy.random.default_rng(seed)` is created in `reset()`. The only randomness is
`Scenario.demand_noise_fraction` (default 0, no random draws): each tick every region and
commodity demand is multiplied by `1 + U(-f, f)`, drawn in sorted key order. Same seed gives
identical `daily` / `edges_daily`; different seeds differ when noise > 0. Solver ties are
broken by sorted ids.

## UI

`solmu/ui` is a client of the core: it holds `Simulation` objects in Streamlit session state
and calls `step()`, `run()` and `apply()`; it computes no model values. Actions apply to run A
only. Map, chart and metric figures are read from `RunResult` tables.

## Finland v0.1 dataset

The shipped Finland data uses all v0.1 mechanisms: five hubs (two for medicine, three for food;
`via_hubs` true for medicine and food, hub stock 14 and 7 days), 1.4x port headroom (1.1x
Naantali), redirect delay 2 days with 5%/day backlog loss, three edges restricted by commodity,
nationwide demand spikes and priced shortages. With these, a single-port closure
(`hamina_winter_closure`, 45 days) causes no unmet demand; the stress scenario
`gulf_of_finland_ice` (60 days) shortens only medicine, after the hubs run out (about day 22);
`porvoo_refinery_outage` (90 days, refinery at 0 for 42 days) is the one case that shortens fuel.
Three policies: `no_stockpiling`, `finland_statutory` (stocks + anticipatory release) and
`finland_statutory_reactive` (the same stocks, released only on observed shortage), so the effect
of holding stock and the effect of the release decision can be read separately. Both statutory
policies hold medicine stock 70% at the pharma hubs and 30% at regions, and all fuel stock at
the Porvoo refinery: 40% companies' obligatory stock drawn automatically, 60% state stock
released by the policy rule, of which 40% is crude that is useless while the refinery is down. Provenance of
every number: `solmu/data/finland/SOURCES.md`.

## Historical validation

`scenarios/port_strike_2010.json` reproduces the stevedore strike of 4-19 March 2010 with the per-commodity
`node_capacity` and `stock_target` events. `scripts/validate_2010.py` prints the ledger (match, mismatch,
not modelled) with live numbers; the sourced ledger and the mismatch analysis are in
`docs/validation/2010_port_strike.md`. The scenario changes no base data.

## Known simplifications

- Lead time uses the base graph (no disruption) and the supplier-coverage rule above;
  routes the solver picks under disruption or competition can be longer, which shows up as
  shortages (that is also what makes disruptions bite).
- Unused supply is lost (except redirected port imports, which queue); no surplus re-export; no
  stock at ports or at hubs that do not `holds` the commodity.
- Catchments are static (base graph); hub need ignores same-day tier-2 outflow; the redirect
  pool is national and ignores geography; hub targets use base demand.
- Shipments are not split or re-routed once dispatched.
- Per-direction arc capacity; sequential, not joint, commodity allocation.
- Release clocks (`shortage`, `on_disruption`) are never reset within a run. The statutory
  Finland policy uses `on_disruption`; in `gulf_of_finland_ice` it releases from day 6 and the
  medicine shortage seen under the `shortage` trigger (first day 22, 139 t) does not occur.
- Unmet demand is not priced in `total_cost`.
