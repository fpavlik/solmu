# Data provenance — Finland v0.2

Every number in `solmu/data/` has a line here. Confidence is one of
`measured` / `estimated` / `placeholder`. Nothing here is calibrated; all
values were set by hand from general knowledge of public statistics (not
looked up row by row) and are order-of-magnitude. Treat the whole dataset as a
demonstration, not as an official figure for any quantity.

Region ids use real ISO 3166-2:FI codes (e.g. `FI-09` Kymenlaakso, `FI-16`
Päijät-Häme, `FI-08` Keski-Suomi).

## nodes.csv

| What | Value(s) | Source / reasoning | Confidence |
|---|---|---|---|
| Region population | FI-01 30500; FI-02 126000; FI-03 186000; FI-04 146000; FI-05 69000; FI-06 168000; FI-07 68000; FI-08 270000; FI-09 163000; FI-10 117000; FI-11 538000; FI-12 175000; FI-13 160000; FI-14 417000; FI-15 245000; FI-16 200000; FI-17 214000; FI-18 1730000; FI-19 485000 (sum ≈ 5.51 M) | Statistics Finland population by region (https://stat.fi/en/statistics/vaerak), recalled approximately for 2023, rounded; the sum is close to the national ~5.6 M. | estimated |
| Region centroid lat/lon | one pair per region (e.g. FI-18 60.25/24.95, FI-14 64.90/26.50) | Eyeballed geographic centre of each maakunta; accurate to roughly 0.2 degrees. Used only for map display and edge-length heuristics. | estimated |
| Port lat/lon | helsinki 60.21/25.15 (Vuosaari); hamina-kotka 60.47/26.95; turku 60.43/22.22; naantali 60.46/21.99; rauma 61.13/21.46; hanko 59.82/22.97; oulu 65.01/25.43; kemi 65.67/24.52 | Approximate harbour locations from public maps. | estimated |
| Source lat/lon | porvoo refinery 60.31/25.55 (Kilpilahti); agri South Ostrobothnia 62.79/22.83; agri Southwest Finland 60.85/22.30 | Refinery location from public maps; agricultural source points are representative centroids, not real facilities. | estimated |
| Port host region tag (`host=`) | helsinki FI-18; hamina-kotka FI-09; turku FI-19; naantali FI-19; rauma FI-17; hanko FI-18; oulu FI-14; kemi FI-10 | Municipality of the port lies in that maakunta (Kotka/Hamina in Kymenlaakso, Kemi in Lappi, Rauma in Satakunta, Hanko in Uusimaa). | measured |
| `sea=` tags | gulf_of_finland, archipelago_sea, baltic_sea, bothnian_sea, bothnian_bay | Coastline each region faces; informational only. | estimated |
| Hub nodes (`kind=hub`) | hub-pharma-vantaa 60.29/25.04 (`holds=medicine`, host FI-18); hub-pharma-tampere 61.45/23.85 (`holds=medicine`, host FI-11); hub-food-vantaa 60.31/25.10 (`holds=food`, host FI-18); hub-food-tampere 61.40/23.70 (`holds=food`, host FI-11); hub-food-oulu 65.00/25.50 (`holds=food`, host FI-14) | Medicine: pharmaceutical wholesale in Finland is concentrated in two firms (Tamro, Oriola) with central warehouses in the Vantaa/Espoo and Tampere areas. Food: grocery groups run central warehouses (Kesko in Vantaa, Inex in Sipoo/Vantaa area, S-group in Vantaa, Tampere and Oulu). Locations are approximate positions and the sites are representative, not a list of real facilities; no population, no demand. | estimated |

## commodities.json

| What | Value(s) | Source / reasoning | Confidence |
|---|---|---|---|
| Priorities | medicine 1, food 2, fuel 3 | Plan section 5.3 design choice: life-critical goods are served first. | placeholder |
| Per-capita rates (kg/person/day) | medicine 0.01; food 2.0; fuel 4.0 | Medicine: about 3.7 kg/person/year of pharmaceutical product mass, order of magnitude. Food: about 730 kg/person/year of food mass bought and eaten, plausible for national supply. Fuel: about 1.5 t/person/year of road, heating, aviation and industrial oil products, roughly 8 Mt/year national consumption. | estimated |
| default_stock_days | 2 | Typical retail/wholesale buffer of a couple of days; plan example. | placeholder |
| `via_hubs` | medicine true; food true; fuel false | Medicines and most food go port to central wholesaler/grocery warehouse to region; fuel moves from refinery/depots direct to regions (plan 13.1). | estimated |
| `hub_stock_days` | catalogue default 7; medicine 14; food 7 (days of served demand held at hubs, plus the supplier lead time) | Wholesalers hold about two weeks of medicines (assumption, within the statutory 3-10 month obligation stocks held elsewhere); grocery warehouses hold about a week of fast-moving food. No measured figure. | placeholder |
| `import_redirect_delay_days` | 2 | Diverting ships to another port takes 1-3 days (plan 13.1); 2 is the middle. | placeholder |
| `import_backlog_loss_fraction_per_day` | 0.05 | Share of waiting cargo lost to other markets or turned back each day. Lowered from 0.1 (plan 13.6 suggestion): at 10%/day nine tenths of diverted cargo vanished within three weeks, which is too harsh for contracted essential imports (ships wait or divert to neighbouring ports rather than resell); 5%/day loses half in two weeks. No measured value. | placeholder |
| `shortage_cost_per_tonne` | medicine 200000; food 5000; fuel 5000 | Societal cost of one tonne of unmet demand, used only to put crisis losses on the same axis as holding cost. Food and fuel: roughly 1.5-2x retail value (food ~3 EUR/kg, road fuel ~2 EUR/kg) to stand in for lost activity beyond the goods themselves. Medicine: pharmaceutical product mass is worth on the order of 100-300 EUR/kg at wholesale; 200 EUR/kg chosen. Order-of-magnitude judgement, not a damage study. | placeholder |

## demand.csv

| What | Value(s) | Source / reasoning | Confidence |
|---|---|---|---|
| All 57 rows (19 regions x 3 commodities) | tonnes/day = population x per-capita kg / 1000, rounded to 1 decimal | Generated by `scripts/build_demand.py` from nodes.csv and commodities.json; national totals: medicine 55.3, food 11015.0, fuel 22030.0 t/day. | estimated |

## supply.csv

Ports: import capacity in t/day. Public port statistics (Finnish Transport
Infrastructure Agency, Finnish Port Association) give total annual cargo
throughput per port: very roughly Helsinki 12-15 Mt, HaminaKotka 10-12 Mt,
Naantali 7-8 Mt (oil, chemicals), Turku 3-4 Mt, Rauma 3-4 Mt, Hanko 2-3 Mt,
Oulu 2-3 Mt, Kemi 2-3 Mt (recalled, not looked up row by row). Annual tonnes
divided by 365 gives 20-40 kt/day, of which only the share that is food, fuel
or medicine is listed. The commodity shares below are judgement, not
statistics.

| What | Value(s) | Source / reasoning | Confidence |
|---|---|---|---|
| Helsinki import capacity | medicine 40; food 1500; fuel 3000 t/day | Largest passenger/unitised-cargo port; ferry-borne and container food imports from Estonia/Germany; main medicine entry. | estimated |
| HaminaKotka import capacity | medicine 30; food 700; fuel 4000 t/day | Large container and bulk port, oil product imports; second medicine entry. | estimated |
| Turku import capacity | medicine 10; food 700; fuel 500 t/day | Ferry traffic from Sweden. | estimated |
| Naantali import capacity | food 200; fuel 6000 t/day | Finland's main oil product port by volume, about 7 Mt/year of liquid bulk (about 19 kt/day); a third taken as fuel entering the domestic market. No medicine. | estimated |
| Rauma import capacity | food 100; fuel 1000 t/day | Industry-oriented port; small shares. | estimated |
| Hanko import capacity | medicine 2; food 400; fuel 1500 t/day | Finland's biggest transit port for cars and Ro-Ro, some oil products. | estimated |
| Oulu import capacity | medicine 3; food 100; fuel 3000 t/day | Northern port, fuel for the northern regions. Fuel raised from 1500 to 3000 in step 4 (placeholder tuning, not a measured figure): with the order-up-to target (cover + supplier lead time) a central/northern region such as Keski-Pohjanmaa (FI-07) must be able to draw fuel from a nearby supplier, otherwise its pipeline is longer than its target cover and it shows structural baseline shortages. The northern regions together consume about 2700 t/day of fuel, so 1500 t/day at Oulu could not serve them. | placeholder |
| Kemi import capacity | food 50; fuel 500 t/day | Small northern port. | estimated |
| Porvoo refinery fuel supply | 14000 t/day | Neste Porvoo capacity about 200 kbbl/day, about 27 kt/day of products; about half assumed for domestic market, the remainder exported. | estimated |
| Agri South Ostrobothnia food supply | 6500 t/day | Finland's biggest agricultural region; modest fraction of national production. | placeholder |
| Agri Southwest Finland food supply | 4500 t/day | Second big agricultural region. | placeholder |
| Design totals | Unchanged in v0.1 (hubs and `max_tonnes_per_day` do not change baseline offers). Supply/demand ratios: medicine 85.0/55.3 = 1.54; food 14750/11015 = 1.34; fuel 33500/22030 = 1.52 (Oulu raised to 3000, see above) | Chosen so that the baseline has no shortage (margin 1.3-2x). Medicine entering via Helsinki + HaminaKotka = 70 of 85 t/day = 82%. This concentration is a deliberate design assumption (medicine is imported mostly through a few southern ports) so that closures of the southern ports matter (in v0.1 the single-port closure alone no longer produces a shortage, see scenarios); real shares were not measured. | placeholder |
| `max_tonnes_per_day` of container/ro-ro ports | 1.4 x baseline: Helsinki medicine 56, food 2100, fuel 4200; HaminaKotka 42 / 980 / 5600; Turku 14 / 980 / 700; Hanko 2.8 / 560 / 2100; Rauma food 140, fuel 1400; Oulu 4.2 / 140 / 4200; Kemi food 70, fuel 700 t/day | Spare handling capacity of 30-50% over typical throughput for unitised-cargo ports (plan 13.6 range 1.3-1.5); no measured utilisation figures. | placeholder |
| `max_tonnes_per_day` of Naantali | 1.1 x baseline: food 220, fuel 6600 t/day | Liquid bulk terminals run closer to capacity, so less headroom. | placeholder |
| `max_tonnes_per_day` of sources | empty (= baseline, no redirection) | Sources are production, not ship arrivals. | placeholder |

## edges.csv

| What | Value(s) | Source / reasoning | Confidence |
|---|---|---|---|
| Road adjacency | 38 `road-FI-a-FI-b` edges between neighbouring maakunta (id order follows the CSV) | Land borders between maakunta, from general geographic knowledge (some minor borders such as FI-03/FI-07 may be debatable). | estimated |
| Rail corridors | 18 `rail-FI-a-FI-b` edges: Helsinki-Hämeenlinna-Tampere-Seinäjoki-Kokkola-Oulu-Rovaniemi (18-06, 06-11, 11-03, 03-07, 07-14, 14-10); Helsinki-Lahti-Kouvola-Mikkeli-Kuopio (18-16, 16-09, 09-04, 04-15), Kouvola-Lappeenranta (09-02); Turku-Helsinki (19-18), Turku-Toijala (19-06); Tampere-Pori (11-17); Tampere-Jyväskylä (11-08); Seinäjoki-Vaasa (03-12); Kuopio-Oulu (15-14); Kuopio-Joensuu (15-13) | Main lines of the Finnish rail network mapped to region pairs. | estimated |
| Port and source links | `link-port-*-<host>` and `link-src-*-<host>` road edges, capacity 30000 (ports) / 20000 (sources), cost 0.2, transit 1; plus `link-port-hamina-kotka-FI-09-rail` rail capacity 15000, cost 0.15 | Deliberately generous so that the port/source node, not the link, is the limiting capacity. | placeholder |
| Åland ferry | `sea-port-turku-FI-01`: capacity 800, cost 1.5, transit 1 | Åland has no land connection; ferry connection via Turku. Åland demand is about 190 t/day, so 800 is generous. | placeholder |
| Road capacity | 20000 t/day if both endpoints below lat 62; 5000 if the southernmost endpoint is at or above lat 64; otherwise 10000 | Rough tiering by traffic density; no measured road data. | placeholder |
| Rail capacity | 12000 t/day (south, same lat rule as road); 5000 (north); 8000 otherwise | Rough tiering; chosen so rail carries a meaningful but not dominant share and the baseline is feasible (static max-flow check passes for all three commodities). | placeholder |
| Cost per tonne | road = 0.5 + d/200; rail = 0.6 x road, where d is the great-circle distance in km between region centroids; rounded to 2 decimals | Relative solver cost only, rail cheaper than road (plan section 8). | placeholder |
| Transit days | 1 if d < 250 km, otherwise 2 | Daily tick granularity; only long northern legs reach 2. | placeholder |
| bidirectional | true on all edges | All links are used in both directions. | placeholder |
| Hub links | `link-hub-<hub>-<host>`: road, capacity 30000, cost 0.2, transit 1, bidirectional | Hubs sit on their host region node; deliberately generous so that the hub stock, not the link, limits flows (same convention as port links). | placeholder |
| Edge commodity restrictions | `link-src-porvoo-refinery-FI-18` fuel only; `link-port-hamina-kotka-FI-09-rail` fuel and food (tank wagons, bulk grain); `link-port-naantali-FI-19` fuel and food (liquid bulk port, no medicine imports in this dataset). All other edges empty (all commodities) | Porvoo refinery ships oil products only; rail from HaminaKotka is typical for fuel and bulk goods. Only three edges are restricted so that the baseline stays feasible. The Åland ferry `sea-port-turku-FI-01` was NOT restricted to food and medicine as proposed: with the restriction Åland (about 122 t/day fuel demand) has no fuel route and the baseline shows 3,416 t unmet fuel from day 2 (tankers reach Åland directly in reality, which is not modelled). | placeholder |

## policies/*.json

| What | Value(s) | Source / reasoning | Confidence |
|---|---|---|---|
| finland_statutory min_stock_days | food 20; fuel 150; medicine 90 | Fuel: Government Decision on security-of-supply objectives (2018) sets a stock of imported fuels equal to five months of normal consumption, held as compulsory company stocks plus state emergency stocks (https://www.huoltovarmuuskeskus.fi/en/a/topical-questions-and-answers-about-security-of-supply); 150 days. The EU Directive 2009/119/EC floor is 90 days of net imports. Medicine: Act on the Stockpiling of Medicinal Products requires 3-10 months by category; 90 days as a lower-middle value. Food: the only statutory food stock is the state grain reserve, about 6 months of grain consumption. Grain products are roughly 70-80 kg/person/year of the ~730 kg/person/year of food mass, so 6 months of grain is about 40 kg/person, or 20 days of total food mass at 2 kg/day; 20 days. Previously 90 for food, which treated all food as stockpiled. | estimated |
| `strategic_stock_at` medicine | hub 0.7, region 0.3 (both statutory policies) | The Act on Obligatory Storing of Medicines (979/2008) places the obligation on manufacturers/importers, health care units and THL (https://fimea.fi/en/supervision/mandatory_reserve_supplies). Importers' stocks sit physically in the single-channel wholesalers' warehouses (Tamro, Oriola), i.e. the pharma hubs; hospital pharmacies hold their own, i.e. the regions. The hospital share by mass is not published; hospital sales are roughly a quarter to a third of the market by value, so 0.3 at regions. | placeholder |
| `strategic_stock_at` fuel | node `src-porvoo-refinery`: 0.4 `release: automatic` + 0.6 policy with `requires_processing` 0.4 (both statutory policies) | Two obligations: importers must hold two months of net imports as compulsory stock (their own operating inventory, drawn as the market needs it, no government decision) and NESA holds state emergency stocks released by decision, five months of consumption in total (https://www.iea.org/articles/finland-s-legislation-on-oil-security; https://www.huoltovarmuuskeskus.fi/en/international-co-operation/international-energy-program). 2 of 5 months = 0.4 automatic, 0.6 state. Both sit at the refinery and coastal depots, not in regions; the model has one fuel source node, so all of it sits there and enters the network through the solver with real lead times. Composition: IEA's Finland review (data April 2012) gives about 23% crude in emergency stocks overall (https://iea.blob.core.windows.net/assets/97a133e9-4565-4388-9c17-7dfacb6bcf2c/CountryChapterFinland.pdf); companies' operating stock is taken as finished product, so the crude is placed in the state part: 0.4 x 0.6 = 0.24 of the total. Crude is useless while the refinery is down and is released only when the source runs. Old data; no newer public breakdown found. | estimated (structure) / placeholder (0.4, 0.4) |
| finland_statutory_reactive | identical to finland_statutory except `release.trigger` = shortage (delay 2, max fraction 0.1) | Control policy: same stocks, released only after a region records unmet demand. Lets the value of holding stock and the value of the early release decision (13.9) be read separately. | estimated (by construction) |
| no_stockpiling min_stock_days | food 0; fuel 0; medicine 0 | Counterfactual by definition (only default_stock_days is held). | estimated |
| stock_holder | food state; fuel companies; medicine companies | Informational. Fuel and medicine obligations lie with companies; grain reserve is held by the state agency (NESA), per its public description. | estimated |
| release.delay_days | 2 | Plan example value; no empirical basis. | placeholder |
| release.max_release_fraction_per_day | 0.1 | Plan example value. | placeholder |
| release.trigger | on_disruption | The national emergency supply agency (NESA/Huoltovarmuuskeskus) decides on releases when it observes a fall in import capacity, not when shelves are empty (plan 13.9); the reactive `shortage` trigger understated what the policy can do. Previously `shortage` (plan example value). | estimated |
| release.disruption_threshold | 0.3 | Fraction by which the national import offer must fall below baseline before the release countdown starts. No empirical basis. | placeholder |
| release.disruption_persistence_days | 1 | Days the drop must persist before the countdown starts; 1 means act on the first day of the drop. No empirical basis. | placeholder |
| emergency_budget | 50000000 | Plan example value, currency units. | placeholder |
| capacity_rental_cost_per_tonne_day | 40 (both policies) | Finnish road haulage costs roughly 0.1 EUR per tonne-km; a 300 km detour is about 30 EUR/t, plus a short-notice premium, so 40 per tonne of extra capacity per day. Raised from the engine default 10 because AddCapacity was too cheap. | estimated |
| emergency_budget note | 50000000 kept | Placeholder for a 30-day national emergency allocation; at 40 per tonne-day it funds roughly 20-25 large interventions (e.g. +2,500 t/day for 20 days costs 2 M, so 50 M buys about 25 of them). | placeholder |
| holding_cost_per_tonne_day | food 0.5; fuel 0.3; medicine 2.0 | Plan example values. | placeholder |

## scenarios/*.json

| What | Value(s) | Source / reasoning | Confidence |
|---|---|---|---|
| horizon_days | baseline 30; port_strike_2010 30; hamina_winter_closure 45; gulf_of_finland_ice 60; porvoo_refinery_outage 90 | Plan section 4.7 gives 30. Raised where the disruption ran to the end of the horizon so the recovery (port reopening, backlog surge, stock rebuilding) is visible: HaminaKotka reopens on day 18, the Gulf ports on day 28, the refinery on day 47. Longer horizons change no outcome before day 30. | placeholder |
| `porvoo_refinery_outage` | `src-porvoo-refinery` factor 0.0 days 5-46 (42 days); fuel demand `"*"` x1.15 days 5-8; horizon 90 | Finland has one refinery since Naantali closed in 2021; Porvoo supplies about half the domestic fuel market in this dataset (14000 of 33500 t/day). A planned maintenance stop of two to four weeks is covered by product stocks and routine imports and is not a crisis; the six-week outage stands for a fire-scale incident (refinery fires elsewhere have stopped units for one to three months). No port events: imports continue at baseline. Hoarding size as in `port_strike_2010`. | placeholder |
| baseline events | none | Sanity scenario, expected zero unmet demand. | placeholder |
| HaminaKotka closure | day 3 to 17, factor 0.0 on `port-hamina-kotka` | Plan section 4.7 (14-day closure). | placeholder |
| Helsinki reduction | day 3 to 20, factor 0.6 on `port-helsinki` | Plan section 4.7. | placeholder |
| Rail Lahti-Kouvola reduction | day 3 to 10, factor 0.5 on `rail-FI-16-FI-09` | Plan section 4.7 with the plan's `rail-kouvola-lahti` mapped to the Päijät-Häme (FI-16) to Kymenlaakso (FI-09) rail edge. | placeholder |
| Demand surge | day 5 to 9, factor 1.5, food, node `FI-02` (Etelä-Karjala) | Plan example used FI-01 (Åland); moved to South Karelia, a south-eastern region exposed to the closure, since Åland is reached only via the ferry link. | placeholder |
| Nationwide food spike in `hamina_winter_closure` | day 3 to 8, factor 1.3, food, `"*"` (all regions) | Household stockpiling after disruption news (plan 13.6); size is a judgement. | placeholder |
| `gulf_of_finland_ice` ports | HaminaKotka and Helsinki factor 0.0 days 4-27; Hanko 0.0 days 4-25; Turku 0.1 days 4-23 | The proposed version (HaminaKotka 0.0 days 4-14, Helsinki 0.3 days 4-14, Hanko 0.5 days 4-12, Turku 0.8 days 4-10) was run first: with hub stocks (14 days medicine), 1.4x port headroom and redirection it produced zero unmet demand under both policies, so no contrast existed. Probing showed the first medicine shortage appears only when the Gulf ports stay shut for about three weeks (hub stock exhausted at about day 22). The shipped durations are the shortest tested that give a clear policy contrast (unmet without stockpiling 305 t against 139 t with the statutory policy, ratio 2.2); shorter or partial closures give none. This is a deliberately extreme stress case (an ice winter lasting most of the horizon), not a forecast. With the `on_disruption` trigger (13.9) the statutory policy releases from day 6 (drop above 0.3 from day 4, delay 2) and the shortage disappears entirely (medicine unmet 0 t, against 139 t and first shortage day 22 under the earlier `shortage` trigger); the closure durations were not changed to restore a shortage. | placeholder |
| `gulf_of_finland_ice` demand spikes | food x1.4 nationwide days 4-10; medicine x1.2 nationwide days 5-9 | Stockpiling by households (plan 13.6). | placeholder |
| `gulf_of_finland_ice` rail | `rail-FI-18-FI-16` (Helsinki-Lahti) factor 0.6 days 4-9 | Storm disruption of the main eastern rail line. | placeholder |
| `port_strike_2010` timing | horizon 30; strike days 4-19 (16 days), so day 4 = 4 March 2010 and day 0 = 1 March | Strike 4-19 March 2010, 16 days (Yliskylä-Peuralahti, Spies, Kämärä & Tapaninen, University of Turku Centre for Maritime Studies, report A55, STOCA project, sect. 4.1 and 4.5). | measured |
| `port_strike_2010` fuel at public ports | `node_capacity` with `commodity_id: fuel`, factor 0.0 days 4-19 at Helsinki, HaminaKotka, Turku, Hanko, Rauma, Oulu, Kemi; Naantali 0.5 | The strike stopped public-port handling, about 80% of foreign trade; only industry-owned private ports ran normally (A55, summary and sect. 4.4). Fuel is liquid bulk handled by stevedores, so factor 0.0. Naantali 0.5 is an assumption: its oil terminal is partly tanker and pipeline operated and the refinery-side handling may not need public stevedores. How much of Naantali's fuel flow continued is NOT SOURCED. Toggle run T1 (Naantali 0.0) is in `docs/validation/2010_port_strike.md`. | estimated (Naantali: placeholder) |
| `port_strike_2010` Porvoo refinery | no event: `src-porvoo-refinery` stays at factor 1 | The refinery's private Kilpilahti port operated normally during the strike (A55, sect. 4.4: only industry-owned private ports operated normally; Kilpilahti is Finland's largest port, sect. 3.2.1). | measured |
| `port_strike_2010` ferry ports, food and medicine | `node_capacity` factor 0.5 days 4-19 for food and medicine at Helsinki, Turku, Naantali, Hanko (only commodities with a supply record at the port) | Liner ro-ro ferries to Sweden, Estonia and Germany kept running because truck units with drivers need no stevedores; companies rerouted via Turku, Naantali, Vaasa and Helsinki-Tallinn (A55, sect. 4.3 and 4.4). The share of normal port-borne food and medicine that still moved is NOT SOURCED; 0.5 reflects that driver-and-truck capacity on the ferries was limited. Because the engine scales a port's handling cap by the same factor, a port at 0.5 can handle at most 0.7 x its baseline (1.4 x 0.5). The rerouting surcharge (A55: 300-500 EUR per load, up to 1500; the spec quotes 500-1000 EUR per unit) is not modelled. | placeholder |
| `port_strike_2010` other ports, food and medicine | factor 0.0 days 4-19 at HaminaKotka, Rauma, Oulu, Kemi (food; medicine where a supply record exists: HaminaKotka, Oulu) | No liner ferry traffic at these ports; container feeders from Hamburg, Rotterdam and Antwerp stopped (A55, sect. 4.4). Spec 13.10 lists Kotka as a pharma alternative in the 2023/2024 strikes; for 2010 the scenario keeps it closed (toggle T7 tests 0.5). | estimated |
| `port_strike_2010` anticipatory stocking | `stock_target` `"*"` (all regions and hubs) factor 1.5 days 0-3 for food and medicine | Companies raised inventories before the strike, at their own and their customers' sites (A55, sect. 4.4 and 4.5). The strike was announced weeks ahead and the original 19 February deadline was postponed by two weeks (spec 13.10). The size 1.5 is a judgement. One wildcard event per commodity covers regions and hubs; a second hub-only event would multiply to 2.25 at hubs and was not added. The window is four days only because the scenario starts on 1 March; the real build-up lasted about two weeks. | placeholder |
| `port_strike_2010` hoarding | `demand` `"*"` fuel x1.15 days 4-6; food x1.1 days 4-6 | Hoarding is reported in later strikes (press, spec 13.10), not in A55; the sizes are judgement. | placeholder |
| `port_strike_2010` no new base data | no change to nodes, edges, supply, demand, commodities or policies | Deliberate: the case must be judged against the unchanged Finland dataset. Mismatches are analysed in `docs/validation/2010_port_strike.md`, not removed by editing data. | measured (statement of method) |
