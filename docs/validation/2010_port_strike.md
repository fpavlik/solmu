# Validation ledger: stevedore strike, 4-19 March 2010

Purpose: check the model against one real disruption before trusting its numbers (spec 13.10).
Scenario: `solmu/data/scenarios/port_strike_2010.json` (horizon 30, day 4 = 4 March 2010, strike days 4-19).
Reproduce: `.venv/Scripts/python scripts/validate_2010.py` (ledger, metrics, port offers) and
`... --sensitivity` (toggle runs T0-T16 used below). Policies: `finland_statutory` is the reference
because Finland held obligatory stocks in 2010; `no_stockpiling` is a counterfactual.
The Finland base data are unchanged for this case; scenario factors and their reasoning are in
`solmu/data/finland/SOURCES.md`.

Sources
- A55: Yliskylä-Peuralahti, Spies, Kämärä & Tapaninen, University of Turku Centre for Maritime Studies,
  report A55 "Finnish critical industries, maritime transport vulnerabilities and societal implications"
  (STOCA project, company interviews).
- Spec 13.10: facts listed in `IMPLEMENTATION_PLAN.md` (Boston.com/AP 19 Mar 2010, Swarthmore database,
  Finnish press on the 2023/2024 strikes). These were not re-checked here; where a fact is only in the
  spec and not in A55 it is marked "spec only".

What the test can and cannot show: the essential commodities are protected by large stocks in the data
(hubs 7-14 days, strategic reserves 90-120 days under the statutory policy), so most outcomes are
insensitive to the scenario factors (see T0-T16). A match on "no shortage" confirms consistency with the
observation but does not discriminate between scenario assumptions. The informative results are the
`no_stockpiling` fuel shortage, the import-offer arithmetic and the mismatch on backlog clearing.

## Ledger

Verdict is for `finland_statutory`; the `no_stockpiling` verdict is given when it differs.
Numbers are from the final run of `scripts/validate_2010.py`.

| ID | Observed fact | Source | Model measurable | Result `finland_statutory` / `no_stockpiling` | Verdict |
|---|---|---|---|---|---|
| L1 | Public ports stopped, about 80% of foreign trade halted | A55 summary, sect. 4.3 | Share of port-borne medicine+food+fuel tonnes lost on a strike day (band 70-90%) | 81% (medicine 69%, food 63%, fuel 85%). This follows from the scenario factors, not from model dynamics | MATCH (input check) |
| L2 | Private industry ports (Kilpilahti, Porvoo refinery) operated normally | A55 sect. 4.4, 3.2.1 | Refinery node factor stays 1 | Factor 1. Refinery shipped on 2/16 strike days under statutory (shipping is demand-driven and released reserves replace it), 16/16 under no_stockpiling at 14,000 t/day | MATCH (input check) |
| L3 | Ro-ro ferries kept running; trucked cargo rerouted via Turku, Naantali, Helsinki; container feeders stopped | A55 sect. 4.3, 4.4 | Food+medicine shipped from ferry ports > 0, from other ports = 0, ferry share higher than baseline | Ferry ports 1,751 / 1,777 t/day (baseline 2,852); other ports 0 (baseline 203); ferry share 100% vs 93% | MATCH |
| L4 | Companies raised inventories before the strike (announced weeks ahead) | A55 sect. 4.4, 4.5 | Regions+hubs stock at end of day 3 vs baseline, at least 1.05 | Food x1.16, medicine x1.08 (both policies) | MATCH |
| L5 | No shortage of medicines | A55 sect. 4.4, 4.6; spec only for consumer outcome | Total unmet medicine = 0 | 0.0 t, 0 regions short (both) | MATCH |
| L6 | No severe lack of basic food; domestic share high, imports marginal | A55 sect. 4.6 | Food unmet during strike at most 1% of demand | 0.0 t (both). Domestic sources cover 100% of food demand in the data, A55 table 3.1 gives imports about 30% of consumer food (see M3) | MATCH, but see M3 |
| L7 | Imported fresh produce was the first retail gap | A55 sect. 4.6 | Needs a separate perishable commodity | `food` is one aggregate | NOT MODELLED |
| L8 | No fuel shortage for consumers (Porvoo kept running; obligatory oil stocks; reserves for 90 days of imports) | Spec only (press); A55 sect. 4.5 for the reserves | Total unmet fuel = 0 | 0 t / 80,140 t (first day 6, 15 regions) | MATCH (statutory); MISMATCH (no_stockpiling), see M2 |
| L9 | Fuel hoarding and local hiccups but no shortage | Spec only; hoarding size not sourced | Hoarding spike without unmet fuel | Demand x1.15 on days 4-6, unmet 0 t / 80,140 t. Local hiccups need sub-regional detail | MATCH (statutory, hoarding only); hiccups NOT MODELLED |
| L10 | Pharma imports kept flowing (Vuosaari, with Kotka, Turku, Hanko as alternatives); medicines ensured by union-authority cooperation | A55 sect. 4.4 (cooperation); spec only for Vuosaari and alternatives | Helsinki medicine capacity open every strike day and no medicine hub below 25% of initial stock | Helsinki factor 0.5; lowest medicine hub stock 65% / 41% of initial; national medicine offer 26 of 85 t/day (-69%) | MATCH |
| L11 | Backlog took weeks to clear after the strike | Spec only; observed backlog is mostly exports | Import backlog of every essential needs at least 14 days after day 19 to fall below 1% of its peak | Medicine 9 days, food 9 days, fuel 13 days (identical under both policies) | MISMATCH, see M1 |
| L12 | Export industry (paper, metals) shut plants within days; 70% of paper output stopped | A55 sect. 4.6 | Needs exports | Not computed | NOT MODELLED |
| L13 | Economic loss about 100 MEUR/day (government estimate, spec only); A55: technology sector export loss 70 MEUR/day, paper 2.5-3 MEUR/day | A55 sect. 4.6; spec | Needs pricing and exports | Not computed | NOT MODELLED |
| L14 | Rerouting surcharge 500-1000 EUR per unit (spec); A55: 300-500 EUR per load, maximum 1,500, up to twice the normal price | A55 sect. 4.3; spec | Needs a price on rerouted tonnes | Not computed | NOT MODELLED |

Summary: MATCH 9, MISMATCH 1, NOT MODELLED 4 (reference policy). Two of the matches (L1, L2) are checks of
scenario inputs, not model outputs.

## Model results (both policies)

| Commodity | Unmet statutory | Unmet no_stockpiling | First shortage day | Peak import backlog | Import lost |
|---|---|---|---|---|---|
| medicine | 0 t | 0 t | none | 455 t | 502 t |
| food | 0 t | 0 t | none | 17,333 t | 18,468 t |
| fuel | 0 t | 80,140 t | none / day 6 | 145,446 t | 183,618 t |

Hub stock minimum as share of initial: food Oulu 27%, Tampere 51%, Vantaa 55%; medicine Tampere 65% / 41%
(statutory / no_stockpiling), Vantaa 76%. National medicine offer: 26 t/day against 85 on days 4-19, back to
85 from day 20. Food offer: 12,400 against 14,750 t/day (-16%, domestic production is unaffected).
The day-by-day offers, backlog and ferry-port shipments are printed by the script.

## Mismatch analysis

Method: one toggle at a time on the shipped scenario or data (copies built in the script; no file is
changed). Numbers are tonnes unmet over the 30-day run unless stated.

### M1. L11, backlog clears in 9-13 days, observation says weeks

Most likely cause: data values, plus a mismatch of what is measured. The redirect and backlog mechanism has
two unsourced parameters: port headroom 1.4 x baseline (estimated) and backlog loss 0.1 per day
(placeholder). The observed backlog is mostly export and industrial cargo, which the model does not carry;
the essentials the model carries are all moved first and clear fast.

Toggle results (days after day 19 until the backlog is below 1% of peak; medicine / food / fuel):

| Run | medicine | food | fuel |
|---|---|---|---|
| T0 as shipped (headroom 1.4, loss 0.1) | 9 | 9 | 13 |
| T10 headroom 1.15 | 16 | 15 | 18 |
| T11 loss 0 | 24 | 21 | 43 |
| T12 headroom 1.15 and loss 0 | not cleared in 70 days | not cleared | not cleared |

So the mechanism can produce "weeks"; the shipped values sit at the fast end. The report gives some
capacity facts that could constrain the headroom (not applied): Naantali capacity limited to 10 Mt/year against
8.12 Mt handled in 2010 (about 1.23 x; the data use 1.1), and Vuosaari described as "rather far from
reaching its maximum handling capabilities" (quoted capacity up to 1.2 million TEU, Port of Helsinki 365,000 TEU
in 2009, A55 sect. 3.2.4 and table 3.18). No change was made to the base data. Fix: calibrate headroom per port
from port statistics and set the loss fraction from an observed cargo diversion share.

### M2. L8 and L9 under `no_stockpiling`, fuel shortage 80,140 t (not observed)

Most likely cause: scenario assumption, not a model fault. `no_stockpiling` has no obligatory oil stocks,
which existed in 2010 (A55 table 4.2: reserve stocks for 90 days of imports), so it is not the comparator for
the observation. The reference policy gives 0 t.

First-order check: with the refinery at full output (14,000 t/day) and Naantali at 0.5 (3,000 t/day) the
national fuel supply is 17,000 t/day against 22,030 t/day demand, a structural deficit of 5,030 t/day. Over 16
days this is 80,480 t; the model gives 70,226 t without hoarding (T6; the retail buffer absorbs about 10 kt)
and 80,140 t with the x1.15 hoarding spike.

Toggles (no_stockpiling fuel unmet): T1 Naantali 0.0 instead of 0.5: 127,750 t (+47,610 t; hand check
3,000 t/day x 16 days = 48,000 t). T6 no hoarding: 70,226 t. T8 strike twice as long: 155,815 t. Statutory
policy: 0 t in every toggle including T8 and T9. The Naantali factor is the least certain fuel input
(not sourced). The result for the reference policy does not depend on it.

### M3. L6, food: match for a possibly wrong reason

The food outcome (0 t) is the same in every toggle, including the harsh ones, because the data put domestic
production at 100% of demand (agri sources 11,000 against demand 11,015 t/day) and hubs hold 7 days. A55
table 3.1 gives about 30% of consumer food as imported. Toggles: T13 domestic supply x0.7: 0 t; T14 plus no
ferries: 0 t; T16 plus no anticipatory stocking: 0 t, under both policies. Stocks (hubs 7 days plus 2 days of
retail cover) cover a 16-day loss of 30% of supply (4.8 days of demand). Only T15 (T14 and the food hub buffer cut to 2
days) produces a food shortage: 36,276 t under no_stockpiling, first day 7; statutory stays 0 because
the strategic food stock is released. This is the closest the model comes to L7: a perishable good is a
matter of a short buffer. Cause: data value (domestic share placeholder, hub buffer placeholder).
Fix: data only, see L7 below. No base data were changed.

### Findings not tied to one ledger line

- Anticipatory stocking (T5) and hoarding (T6) do not change any essential outcome under the statutory
  policy and only the fuel number under no_stockpiling. The stock-up reaches only x1.16 (food) and x1.08
  (medicine) in four days because supply and arc capacity limit it. The real build-up lasted about two weeks
  (spec 13.10), which the scenario cannot represent because day 0 is 1 March; a longer lead-in would need
  an earlier scenario start. Not changed.
- Ferry factor (T2 0.3, T3 0.0, T4 1.0) does not change food or medicine outcomes either. The
  dependence on trucked ro-ro capacity is invisible at this stock level.
- Only the worst case T9 (no ferries and no stocking) under no_stockpiling gives a medicine shortage: 149 t, first
  day 22, after the medicine hubs run out. T8 (strike twice as long) gives no essentials shortage under the statutory
  policy. This agrees with A55 only loosely: interviewees said a month-long strike would have forced
  industrial plants to close, which concerns industry, not consumer supply.
- Under the statutory policy fuel reserves are drawn down by 2.04 Mt (77% of 2.64 Mt) in 16 days while the
  national fuel deficit is about 0.08-0.09 Mt. The `on_disruption` release (13.9) moves a fixed fraction of
  stock per day regardless of need, which also raises holding cost to 27.1 million. No source says how much
  of the obligatory stock was released in 2010; not sourced. Smallest change if wanted: cap the release
  by the regional gap to target (a policy parameter, default off). Not implemented here.

### Not modelled, and the smallest change that would allow each

- L7 fresh produce as the first retail gap: data only. Add a commodity `food_fresh` (via_hubs, own
  `hub_stock_days` of 2-3 days from A55 table 4.2, import-heavy supply records at ports) and a short
  `target_stock_days` entry in the policies; no code change. `perishable_days` stays unused.
- L9 local fuel hiccups: needs sub-regional nodes (regions are aggregates). Data-only if the regions are split.
- L12 export plants shutting: three small code changes: supply records allowed at regions, demand records
  allowed at port nodes for an `export` commodity, and a storage cap at producing regions so output stops
  when it cannot be shipped.
- L13 losses of about 100 MEUR/day: a `value_per_tonne` per commodity and a loss metric in the summary;
  meaningful only after exports exist.
- L14 rerouting surcharge: an additive per-tonne cost event on edges and a transport-cost metric in the
  summary (the solver already minimises `cost_per_tonne x transit_days`).

## Status of the numbers

All Finland data used here are unchanged. Scenario factors for this case are `placeholder` or `estimated`
in `solmu/data/finland/SOURCES.md` (the Naantali fuel factor 0.5, the ferry factor 0.5, the stock-up factor 1.5 and
the hoarding factors are judgement). The ledger tests the model's structure against the direction of the
facts, not its calibration.
