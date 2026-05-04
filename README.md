# pyExaMINE

**Python ExaScale Minerals & Infrastructure Network Evaluation**

An agent-based model (ABM) for simulating worldwide critical-minerals
supply chains at facility resolution. Per-facility mines, processors,
and recyclers; per-country manufacturers, retailers, and consumers; a
per-country transport fleet that routes shipments through real maritime
chokepoints (Strait of Hormuz, Suez Canal, Malacca Strait, Panama Canal,
Cape of Good Hope) with mode-specific lead times and price dynamics
shaped by random geopolitical disruptions, scheduled political embargoes,
chokepoint crises, and material substitution.

## Overview

This project models three critical minerals essential for clean energy
transitions:
- **Lithium** - Battery technology
- **Nickel** - Battery cathodes and stainless steel
- **Platinum** - Catalytic converters and fuel cells

All facility-level inputs live in `data/` at the project root and are
hand-curated estimates compiled from USGS Mineral Commodity Summaries
2025, IEA Critical Minerals Outlook, S&P/BMI databases, and company
filings. Per-mineral CSVs cover individual mines, processors, and
recyclers (e.g. `data/lithium_mines.csv` lists Greenbushes, Pilgangoora,
Salar de Atacama (SQM), etc.); country-level CSVs cover manufacturer
and consumer demand shares; a single `data/transport_fleet.csv` defines
the per-country shipping/rail/truck fleet.

## Key Features

### 🌍 Data-Driven
- **Per-facility** mines, processors, and recyclers for Lithium, Nickel,
  and Platinum (~30 Li mines, ~25 Li processors, ~20 Li recyclers; the
  Ni and Pt files are similarly populated).
- **Country-level** manufacturer and consumer demand shares (~12
  manufacturer countries, ~25 consumer countries).
- **GDP-scaled per-country agent fan-out**: each country in the share
  CSVs is split into `max(1, round(gdp_billion / agents_per_gdp_billion))`
  manufacturer / retailer / consumer agents (default knob 500, so USA
  ($28T) gets 56 agents per role, China ($18T) 36, Germany ($4.5T) 9,
  down to top-30 cutoff at 1; non-top-30 entries — "Other countries",
  Madagascar, Cuba — stay at 1). The per-country *aggregate* share /
  demand is unchanged, only the within-country granularity increases.
  Total downstream agents: ~150 manufacturers, ~180 retailers, ~180
  consumers per mineral (vs ~12 / 25 / 25 under the previous one-per-
  country aggregation).
- Each agent labelled with its country and facility name (e.g.
  `Australia/Greenbushes`, `China/Tianqi-Sichuan`); within-country
  agents add an index suffix (`USA/retail#7`, `China/manufacturers#22`).
- Calibrated extraction costs per facility, regional energy costs, and
  recovery / conversion efficiencies per facility.

### 🤖 7 Agent Types
- **MineAgent** - Extracts raw minerals. **Sustained-pressure mothball**:
  a mine only mothballs after `mothball_trigger_steps` (default 52
  weeks) of price below cash cost (= `extraction_cost ×
  mine_cash_cost_fraction`, defaults 0.65 Li/Ni, 0.60 Pt). Real mines
  almost never full-shutter on a single below-cost week — offtake
  contracts and care-and-maintenance avoidance keep them running.
  Restart fires above `extraction_cost × mine_restart_margin` (1.2x);
  a **warm restart** (`mine_warm_restart_lag_steps`, default 12 wks /
  26 wks Pt) is used if the mine was mothballed within the last
  `mine_warm_restart_window_steps`, otherwise the **cold** lag (26 wks
  Li/Ni, 40 wks Pt) applies. **Output flexes with price** via a
  configurable utilization factor (linear ramp between
  `mine_min_utilization` and `mine_max_utilization`), rescaled so USGS
  baseline output is preserved at the anchored "normal" multiple of
  cost. **Capacity expansion is price-responsive**: per-step growth
  is `mine_capacity_growth_per_year / steps_per_year` baseline, but
  flips to a higher rate (`mine_capacity_growth_per_year_high`, e.g.
  15 %/yr Li, 10 %/yr Ni, 5 %/yr Pt) once price stays above
  `extraction_cost × mine_expansion_price_threshold` (default 2.0)
  for `mine_expansion_trigger_steps` (default 52 weeks ~ 1 yr,
  matching greenfield permit-to-pour lead time). Reverts to baseline
  when the counter relaxes; already-built capacity stays. Reserves
  are replenished at `output × reserve_replacement_rate` only on
  steps where the mine actually extracts (so mothballed / disrupted
  facilities don't accrue free exploration). Unsold extracted ore
  carries forward in a `pithead_stockpile` and accumulates back into
  next-step supply, conserving mineral mass through the step
  boundary. Subject to random disruptions, geopolitical events, and
  embargoes; embargo stockpiles are released back into supply
  **linearly** over `post_embargo_release_steps` (frozen chunk size =
  initial / N, so the stockpile reaches zero in exactly N steps)
  once the embargo lifts.
- **ProcessorAgent** - Converts ore to processed material; receives
  recycled mineral via a dedicated channel that bypasses the conversion
  stage. **Capacity is the CSV's post-conversion output capacity**;
  input throughput is derived as `output_capacity / efficiency`, so an
  82%-yield 40 kt/yr Li plant can feed ~48.8 kt/yr of contained-Li
  input. Ore purchases are sized by **inventory backpressure**:
  `processor_inventory_cap_weeks` of expected output inventory caps
  the ordered pipeline, which lets the in-flight pipeline fill to
  roughly (cap − safety) weeks of input throughput. Recycled-mineral
  arrivals respect the same cap (recyclers query
  `headroom_for_recycled()` before dispatching, holding any residue
  back in their `recovered_pool` for the next step). Capacity grows
  per step at `processor_capacity_growth_per_year / steps_per_year`
  so refining keeps pace with mining capacity build-out under
  multi-decade demand growth. **Processors can be disrupted by
  geopolitical events** (Indonesian smelter outages, Chinese power
  curtailments, sanctioned operators); during a disruption the
  facility skips purchasing / processing / selling but inbound
  shipments still arrive (ore stockpiles at the gate, matching real
  smelter outage behaviour).
- **TransportAgent** - Real shipment pipeline with route-specific
  lead times (chosen by `src/data/routing.py` per O-D pair, not by
  the agent — typically ship for cross-region, rail for overland
  Asia↔Europe). Disrupted jurisdictions delay any shipment touching
  them and self-clear when the geopolitical window ends. **Deferral
  cap**: shipments stuck behind a closed chokepoint or disrupted
  corridor for more than `transport_max_deferral_steps` (default
  26 wk = 6 months) are dropped and their mineral content booked to
  the model-level `lost_in_transit_mineral` counter — without this
  cap, a permanently-closed chokepoint would accumulate cargo
  indefinitely.
- **ManufacturerAgent** - Produces goods, invests in substitution.
  Target input inventory is sized in mineral tonnes. Each batch's
  **as-built mineral content** is tracked alongside units through the
  output buffer, so EOL deposits use the intensity at manufacture
  time (matters once substitution drifts intensity away from the
  initial value).
- **RetailerAgent** - Manages inventory with an (s, Q) policy that
  **sizes off an EWMA of realised consumer requests** (default
  `retailer_demand_ewma_alpha = 0.05`, ~13-week half-life), not the
  bare 2024 baseline × growth factor. The EWMA tracks both the
  long-run demand trend (volume rising with the demand trajectory)
  and consumer price-elasticity contraction during multi-month
  spikes — without explicitly knowing either. Previously the policy
  was price-blind and inventory built up mid-shock because
  retailers kept ordering at the full anchored rate while consumers
  had cut back. Sources goods from manufacturers **region-
  preferenced**: same-country first, then same-region, then global —
  mirroring real supply relationships and keeping the typical
  shipment lead time short enough for the (s, Q) cycle to keep
  inventory stocked. Multi-order pipeline (up to
  `retailer_max_pending_orders` outstanding). Embedded mineral
  content travels with shipped goods to preserve as-built intensity
  through to consumers.
- **ConsumerAgent** - Generates price-sensitive demand and shops
  retailers in randomized order each step. Deposits the embedded
  mineral content of purchases into the EOL pool with the configured
  product-lifetime delay.
- **RecyclingAgent** - Recovers minerals from end-of-life products.
  Claims a fair share of the **persistent** `available_eol_pool` on
  the model (matured deposits roll into it at the start of each step;
  uncollected scrap stays in the pool and rolls forward — the
  previous behaviour silently dropped uncollected EOL each step,
  which biased recycled supply downward). Per-step intake is capped
  by the facility's nameplate. Material flows through two pools so
  recovery efficiency is applied exactly once even when downstream
  processors are full: collected scrap lives in `storage`; once a
  recycler can profitably ship, it moves through
  `recovery_efficiency` into a `recovered_pool` that holds the
  recovered tonnage until processor inventory headroom appears.
  Dispatch is region-preferenced (same-country processors first,
  then same-region, then global) and respects each processor's
  `headroom_for_recycled()` — so recycled mineral can never push a
  processor past its `inventory_cap`. Shipments traverse the
  routing engine, so a recycler in the USA shipping to a Chinese
  processor gets the route's chokepoint exposure and lead time.

### 📊 Dynamic Mechanisms
- **Demand Trajectory** - Per-mineral demand interpolates between the
  rows in `data/demand.csv`. The default scenario is IEA NetZero
  (Li 0.15→0.43→1.5 Mt/yr over 2024-2030-2050; Ni 3.5→5→7 Mt; Pt
  0.25→0.35→0.6 kt). Consumer base demand and manufacturer effective
  capacity both scale with the same per-step growth factor, so the
  supply chain widens as demand grows instead of bottlenecking on
  static manufacturer capacity.
- **Mine Capacity Expansion + Reserve Replacement** - Each mine's
  `production_capacity` grows by `mine_capacity_growth_per_year /
  steps_per_year` per step (only when operational — a mothballed or
  disrupted shaft doesn't sink capex into expansion). **Growth is
  price-responsive**: under sustained pricing above
  `extraction_cost × mine_expansion_price_threshold` (default 2.0)
  for `mine_expansion_trigger_steps` (default 52 wk), each mine's
  per-step rate flips from baseline to a "high" rate
  (`mine_capacity_growth_per_year_high`, e.g. 15 %/yr Li, 10 %/yr Ni,
  5 %/yr Pt) — modelling the real-world capex response to
  multi-year price spikes that the static knob alone misses. Reverts
  to baseline when the counter relaxes; built capacity persists.
  Reserves are replenished by `output × reserve_replacement_rate`
  on operational steps (was: gross production_capacity each step,
  even during mothball — now scaled to actual extraction so the
  exploration spend tracks the production it's funding). Defaults
  are mineral-specific (Li 7.5%/yr / 70% replacement; Ni 4% / 50%;
  Pt 1% / 30%).
- **Sustained-Pressure Mothball + Warm/Cold Restart** - Real mines
  almost never full-shutter on a single below-cost week. A mine
  accumulates a `low_price_counter` while price is below cash cost (=
  `extraction_cost × mine_cash_cost_fraction`, default 0.65 Li/Ni,
  0.60 Pt) and only mothballs once that counter crosses
  `mothball_trigger_steps` (default 52 weeks across all minerals,
  matching the historical timing of BHP Nickel West / Wyloo / Mt
  Cattlin / Sibanye Stillwater 2024 mothball decisions). The counter
  decrements (net) on price recovery, so transient dips don't add up.
  Restart fires above `extraction_cost × mine_restart_margin` (1.2x);
  if the mine was mothballed within `mine_warm_restart_window_steps`
  (52 wks Li/Ni, 78 wks Pt) the restart uses
  `mine_warm_restart_lag_steps` (12 wks Li/Ni, 26 wks Pt; equipment
  still in place), otherwise it uses the cold `mine_restart_lag_steps`
  (26 wks Li/Ni, 40 wks Pt). The restart aborts if price slips back
  below the trigger mid-counter.
- **Mineral mass conservation** - Each mine carries unsold production
  into a `pithead_stockpile` that is offered again the next step. The
  same path absorbs unsold post-embargo stockpile releases. Retailer
  warm-start inventory is initialised with the embedded mineral
  content of its starting stock, so EOL deposits during the first
  product-lifetime cycle carry the correct as-built mineral content.
  Conservation is **diagnosed every step** by `Mass_Balance_Discrepancy`,
  computed as `(initial_total_mineral + cumulative_reserve_replacement)
  − (Total_Mineral_In_System + cumulative_processor_yield_loss +
  cumulative_recovery_loss + lost_in_transit_mineral)`. Discrepancy
  stays at ~10⁻¹⁵ relative (pure float noise) across every committed
  run; a sustained drift away from zero is the regression signal that
  some new pipeline change has introduced a leak.
- **Tier-Ordered Scheduling** - Within each step, agents are
  activated in supply-chain order: mines → recyclers → processors →
  manufacturers → retailers → consumers → transport. Transport runs
  last so shipments accepted earlier in the same step queue with
  their full lead time before any deliveries take place. Within each
  tier the activation order is shuffled (using the seeded RNG) for
  fairness.
- **Real Transport Pipeline with Routing** - Every cross-border
  shipment (mine→processor, processor→manufacturer,
  manufacturer→retailer, and recycler→processor) is routed through
  `src/data/routing.py`'s region-pair table. Each O-D pair has a
  primary route (chokepoints traversed + lead-time weeks + mode) and
  zero or more alternates. The dispatcher picks the first open route
  at acceptance time; if a chokepoint closes mid-transit, delivery
  is deferred until it reopens, so in-flight shipments queue up at
  the closed chokepoint. Recycled supply is region-preferenced: a
  recycler ships first to processors in the same country, else same
  region, else globally -- with the same lead times and chokepoint
  exposure as primary mineral.
- **Chokepoint Crises** - Five maritime chokepoints (Strait of Hormuz,
  Suez Canal, Malacca Strait, Panama Canal, Cape of Good Hope) can be
  closed for a configurable window via `--chokepoint-crisis`. While
  closed, every route that traverses the chokepoint is unavailable;
  shipments either re-route via a longer alternate (Suez closed → Cape,
  +3-4 weeks typical) or wait for reopening if every alternate is also
  blocked.
- **Market Pricing** - Cost-anchored, flow-driven. Each step the
  model computes the merit-order **marginal cost** (the extraction
  cost of the last operational mine called online to meet demand)
  and the **cheapest active extraction cost**. Price is updated by a
  proportional move (`elasticity × log(supply/demand)`, capped at
  `max_step_pct`) plus a log-linear pull toward marginal cost
  (`anchor_strength × log(marginal_cost / current_price)`). The
  resulting price is bounded by a soft band that *moves with the
  cost curve*: floor = `floor_cost_fraction × cheapest_cost`,
  ceiling = `ceiling_mc_multiple × marginal_cost`. Outer hard
  `price_floor` / `price_ceiling` bounds act as catastrophe limits
  and normally don't bind.
- **Price-Responsive Mine Utilization** - Mines run between
  `mine_min_utilization` and `mine_max_utilization` of nameplate capacity
  depending on the price/extraction-cost ratio. At the anchored normal
  ratio the output matches USGS-reported baseline; above it mines ramp
  up; below it (but above mothball threshold) they ramp down.
- **Random Geopolitical Events** - Stochastic shutdowns of
  jurisdictions (default 1% probability per step, 5–15 steps
  duration). Each event picks a tier first: with probability
  `geopolitical_processor_event_share` (default 0.30) it lands on
  the **refining tier** (smelter/refinery outage — Indonesian RKEF,
  Anglo American Pt smelter, Chinese power curtailment) instead of
  the mining tier; otherwise it disrupts mines as before. In both
  cases transport in/out of the affected country is also disrupted
  (the corridor itself is what's affected). Disrupted jurisdictions
  automatically clear from transport agents when the window ends.
- **Scheduled Political Embargoes** - A country can withhold its
  mine output from the international market starting on a chosen
  step for a fixed duration. Mines keep extracting; production
  accumulates in a domestic stockpile and is unavailable to foreign
  processors while the embargo is active. **On lift, the stockpile
  drains back into available supply** linearly over
  `post_embargo_release_steps` (default 26).
- **Material Substitution (with reversion)** - Manufacturers reduce
  mineral intensity under sustained high prices and partially revert
  it under sustained low prices. Two sticky counters operate in
  opposite price regions: `high_price_counter` triggers a forward
  substitution step (intensity down by `substitution_rate` per cycle)
  when price stays above `substitution_price_threshold` (default
  1.5x initial); `low_price_counter` triggers a reversion step
  (intensity back up by `substitution_revert_rate`) when price stays
  below `substitution_revert_threshold` (default 0.667x initial).
  Both counters decay in the dead zone between thresholds. Reversion
  is intentionally slower than adoption (longer trigger window,
  smaller per-cycle rate) reflecting the real-world cost of switching
  back once new chemistry production lines are committed -- but it
  exists, so the model captures the historical LFP↔NMC and Pt↔Pd
  flips driven by relative-price reversals. As-built intensity
  travels with each batch through the chain so EOL recovery is
  correct even after intensity drifts down or back up.
- **Circular Economy** - Recycling loop with a realistic
  product-lifetime lag (520 weeks for Li/Ni EVs, 624 weeks for Pt
  autocats). Matured EOL deposits land in a persistent
  `available_eol_pool` on the model (uncollected scrap rolls forward
  rather than being silently dropped each step). The pool is
  snapshotted at step start so multiple recyclers split it fairly
  (no compounding shortfall from sequential collection on the same
  shrinking pot).
- **Reproducibility** - All randomness in the model (mine disruptions,
  geopolitical events, agent shuffling, consumer retailer choice) flows
  through a single seeded `random.Random` instance.

### 📈 Comprehensive Outputs
- 6-panel visualization dashboard per mineral
- Time-series CSV with all model state per step (including embargo metrics)
- Summary statistics and validation checks

## Project Structure

```
pyExaMINE/
├── README.md                          # This file
├── INSTALL.md                         # Detailed install / uv guide
├── data/                              # Per-facility + per-country source data
│   ├── lithium_mines.csv              # ~30 individual Li facilities
│   ├── lithium_processors.csv         # ~25 individual Li refiners
│   ├── lithium_recyclers.csv          # ~20 individual Li recyclers
│   ├── lithium_manufacturers.csv      # country-level cell-mfg shares
│   ├── lithium_consumers.csv          # country-level demand shares
│   ├── nickel_*, platinum_*           # same structure for Ni and Pt
│   ├── transport_fleet.csv            # per-country ship/rail/truck fleet
│   ├── country_gdp.csv                # top-30 nominal GDP (drives agent fan-out)
│   └── demand.csv                     # global annual demand by year/scenario
├── pyproject.toml                     # uv project metadata
├── uv.lock                            # Pinned dependency graph (committed)
├── requirements.txt                   # Legacy mirror for non-uv users
├── plans/                             # Architecture documentation
├── src/                               # Source code
│   ├── agents/                        # Agent implementations (all labelled)
│   │   ├── mine_agent.py              # .country, .facility, .label
│   │   ├── processor_agent.py
│   │   ├── transport_agent.py         # carries chokepoints per shipment
│   │   ├── manufacturer_agent.py
│   │   ├── retailer_agent.py
│   │   ├── consumer_agent.py
│   │   └── recycling_agent.py
│   ├── model/
│   │   ├── supply_chain_model.py      # closed_chokepoints, dispatch_shipment
│   │   └── market_mechanism.py
│   ├── data/
│   │   ├── data_loader.py             # reads data/ CSVs
│   │   └── routing.py                 # region-pair routing + chokepoints
│   ├── visualization/
│   │   └── visualizer.py
│   └── config/                        # Mineral-specific tunables
│       ├── lithium_config.py
│       ├── nickel_config.py
│       └── platinum_config.py
├── outputs/                           # Generated results
│   ├── {lithium,nickel,platinum}_*    # canonical 24-yr baselines
│   ├── chile_li/, china_li/, australia_li/, chile_china_li/,
│   │   big3_li_5yr/, sa_pt/                            # embargo scenarios
│   ├── suez_li/, malacca_li/, hormuz_li/,
│   │   malacca_ni/, suez_pt/                           # chokepoint scenarios
│   ├── 2050/                          # 26-yr combined scenarios
│   │   ├── baseline/, asia_crisis_2030/, indonesia_squeeze_2032/,
│   │   ├── sa_pt_crisis_2030/, li_nationalism_2035/, multi_crisis_2040/
│   │   ├── scenarios_2050.png          # per-mineral price-vs-time
│   │   └── scenario_summary.png        # in-window % delta bars
│   └── embargo_comparison.png         # multi-scenario chart
├── scripts/
│   └── regenerate_outputs.py          # One-shot rebuild of outputs/
└── run_simulation.py                  # Main entry point
```

## Installation

pyExaMINE uses [uv](https://docs.astral.sh/uv/) to manage its Python
environment. The project ships with a `pyproject.toml` and a committed
`uv.lock`, so a single `uv sync` reproduces the exact dependency graph
across machines. `uv run` then executes the simulation in the
project's `.venv/` without requiring you to activate it manually.

```bash
# Clone the repository
git clone https://github.com/nugent68/pyExaMINE.git
cd pyExaMINE

# Install uv (macOS)
brew install uv
# or, on any platform:
# curl -LsSf https://astral.sh/uv/install.sh | sh

# Create the venv and install the locked dependency set
uv sync
```

See [INSTALL.md](INSTALL.md) for the legacy `python -m venv venv` path
(uses `requirements.txt`, kept in sync with `pyproject.toml`) if you
cannot use uv.

**Requirements:**
- uv (installs Python interpreter on demand)
- Mesa >= 2.4 (agent-based modeling framework)
- pandas, numpy (data manipulation)
- matplotlib, seaborn (visualization)

## Usage

### Run a Single Mineral Simulation

```bash
# Simulate Lithium supply chain for 200 steps
uv run python run_simulation.py --mineral lithium --steps 200

# Simulate Nickel with custom geopolitical event probability
uv run python run_simulation.py --mineral nickel --steps 300 --geo-prob 0.02

# Run with specific random seed for reproducibility
uv run python run_simulation.py --mineral platinum --steps 250 --seed 42
```

### Run All Three Minerals

```bash
uv run python run_simulation.py --all --steps 200
```

### Regenerate every committed output

`scripts/regenerate_outputs.py` is a one-shot driver that rebuilds the
canonical 24-yr baselines, every embargo / chokepoint scenario, the
`outputs/2050/` combined scenarios, and the comparison plots. Re-run
this any time the model dynamics change.

```bash
uv run python scripts/regenerate_outputs.py
```

### Run paired ensembles (parallel)

Single-seed scenario comparisons mix the embargo / chokepoint signal
with whatever stochastic events fired in the comparison window for
that one seed. To separate the signal from the noise, regenerate with
`--n-seeds N`: every comparison-style scenario (embargoes,
chokepoints, the 2050 combined scenarios) runs N times with seeds
`[seed_base .. seed_base+N-1]`, and the baseline runs with the same
seed set so each per-seed delta is taken on a matched RNG sequence —
shared geopolitical events cancel out and only the scenario-
attributable signal remains.

```bash
# 20-seed ensemble, auto-parallel (= min(cpu_count, 20) workers)
uv run python scripts/regenerate_outputs.py --n-seeds 20

# Explicit worker count (limit parallelism on a busy machine)
uv run python scripts/regenerate_outputs.py --n-seeds 20 --n-workers 8

# Different seed range (handy for split runs)
uv run python scripts/regenerate_outputs.py --n-seeds 20 --seed-base 200
```

For each comparison scenario, the ensemble run produces:

- `outputs/<scenario>/ensemble_summary.csv` — one row per mineral
  with `n_seeds`, `scen_mean`, `scen_std`, `scen_p10`, `scen_p50`,
  `scen_p90`, and (paired with the baseline at matching seeds)
  `delta_mean_pct`, `delta_std_pct`, `delta_p10_pct`,
  `delta_p50_pct`, `delta_p90_pct`.
- `outputs/<scenario>/<mineral>_ensemble_band.png` — median +
  p10–p90 band time-series with the comparison window shaded; the
  baseline ensemble is drawn underneath in grey for visual diff.

Per-seed full model CSVs land in `ensemble_runs/` (gitignored). With
N=20, expect ~270 MB of per-seed CSVs vs ~30 MB of committed
ensemble artefacts.

Parallelism is near-linear up to `n_seeds` (each seed-run is fully
independent; the single ProcessPoolExecutor is reused across every
scenario so import overhead is paid once). The N=4 smoke test shows
~3.8× speedup on 4 workers; on an 8-core machine an N=20 full regen
takes roughly 25–30 minutes vs ~3.5 hours sequential.

A worked example of why this matters: at single-seed=42 some
chokepoint scenarios appear to lower price (e.g. `malacca_li` shows
−9 % in the in-window comparison), but the paired N=4 ensemble
shows `−2.5 % ± 10 %` — the band straddles zero, so the −9 %
single-seed reading was within-window noise, not a real effect.
Robust signals like `big3_li_5yr` come back at `+70 % ± 5 %` — a
clean, high-significance deviation. N=20 tightens those bands
further.

### Run a Political Embargo Scenario

The `--embargo` flag schedules an export embargo: a country withholds its
mine output from the international market starting at `START_STEP` for
`DURATION` steps. Mines in that country still extract; the would-be
exports accumulate in a domestic stockpile. The flag accepts the form
`COUNTRY:START_STEP:DURATION` and may be repeated.

```bash
# Chile + China both embargo Li exports for 1 year, mid-simulation
uv run python run_simulation.py --mineral lithium --steps 1248 --seed 42 \
    --embargo "Chile:624:52" --embargo "China:624:52"

# 5-year combined embargo from the three biggest Li producers
uv run python run_simulation.py --mineral lithium --steps 1248 --seed 42 \
    --embargo "Chile:624:260" --embargo "China:624:260" \
    --embargo "Australia:624:260"
```

The country name must match the `country` column in
`data/{mineral}_mines.csv` (case-sensitive).

### Run a Chokepoint Crisis Scenario

The `--chokepoint-crisis` flag closes a named maritime chokepoint for a
window. Any in-transit shipment whose route uses the closed chokepoint
is delayed until it reopens; new shipments dispatched while it's closed
re-route via an alternate (typically Cape of Good Hope at +3-4 weeks).

```bash
# Suez Canal closed for 8 weeks at step 624
uv run python run_simulation.py --mineral lithium --steps 1248 --seed 42 \
    --chokepoint-crisis "Suez Canal:624:8"

# Malacca Strait closed for 8 weeks (large impact on Indonesia->China Ni)
uv run python run_simulation.py --mineral nickel --steps 1248 --seed 42 \
    --chokepoint-crisis "Malacca Strait:624:8"

# Combined: SA embargo + Suez closure for Pt
uv run python run_simulation.py --mineral platinum --steps 1248 --seed 42 \
    --embargo "South Africa:624:52" \
    --chokepoint-crisis "Suez Canal:624:26"
```

Known chokepoints: `Strait of Hormuz`, `Suez Canal`, `Malacca Strait`,
`Panama Canal`, `Cape of Good Hope`. The country/route mappings live in
`src/data/routing.py`. You can also schedule these in config:

```python
"political_embargoes": [
    {"country": "Chile",     "start_step": 624, "duration": 52},
],
"chokepoint_crises": [
    {"chokepoint": "Suez Canal",   "start_step": 624, "duration": 8},
],
```

### Command-Line Options

```
--mineral {lithium,nickel,platinum}  # Mineral to simulate
--all                                # Run all three minerals
--steps N                            # Number of simulation steps (default: 200)
--geo-prob P                         # Random geopolitical event probability (default: 0.01)
--seed N                             # Random seed for reproducibility
--output-dir DIR                     # Output directory (default: outputs/)
--no-viz                             # Skip generating PNG dashboards (faster)
--embargo SPEC                       # Schedule a political embargo (repeatable)
--chokepoint-crisis SPEC             # Close a maritime chokepoint (repeatable)
```

## Output Files

Each simulation generates:

1. **Visualization**: `{mineral}_supply_chain_analysis.png`
   - 6-panel dashboard showing price, inventory, supply, demand, disruptions, substitution

2. **Time-Series Data**: `{mineral}_model_data.csv`
   - Step-by-step metrics for all tracked variables. Includes
     mass-balance diagnostic columns:
     `Total_Mineral_In_System`, `Cumulative_Processor_Yield_Loss`,
     `Cumulative_Recovery_Loss`, `Lost_In_Transit_Mineral`,
     `Cumulative_Reserve_Replacement`, `Mass_Balance_Discrepancy`
     (should hover at ~10⁻¹⁵ relative — float noise);
     and pipeline-throughput columns:
     `Total_Processor_Throughput` (the supply signal driving the
     price update — landed mineral, not pithead-offered),
     `Available_EOL_Pool`, `Disrupted_Processors_Count`,
     `Total_In_Transit_Tons`.

3. **Summary Statistics**: `{mineral}_summary_stats.txt`
   - Key statistics: average price, total production, recycling rate, etc.

4. **Ensemble Outputs** (`--n-seeds N>1` only):
   - `{scenario}/ensemble_summary.csv` — per-mineral mean/std/p10/p50/p90
     of the in-window price and (paired with baseline at matching seeds)
     percentile statistics on the % delta.
   - `{scenario}/{mineral}_ensemble_band.png` — median + p10–p90 band
     vs baseline ensemble.

## Performance

A single 1352-step (2050-horizon) Lithium run with one political embargo
takes **~18 seconds** on a single core. A 20-seed ensemble of the same
scenario runs in **~53 seconds** on a 10-physical-core machine using
`xargs -P 10` or the parallel runner in
[scripts/regenerate_outputs.py](scripts/regenerate_outputs.py).

```bash
# 20-seed ensemble of the canonical scenarios (auto-parallel by seed)
uv run python scripts/regenerate_outputs.py --n-seeds 20

# Single-seed regeneration of all committed outputs (sequential)
uv run python scripts/regenerate_outputs.py --n-seeds 1
```

Per-seed paths through the model are stochastic (random scheduler order,
disruption rolls, region-tier shuffles). For analysis use the ensemble
mean/percentile output rather than reading a single seed; published
canonical outputs under `outputs/2050/` use N=20.

The hot path is now dominated by the `random.shuffle` calls that
implement the load-balancing across manufacturers / processors / retailers
(intentional model behavior, not a bottleneck to remove). All
high-cost per-step bookkeeping (in-transit shipment scans, repeated
config lookups, redundant DataCollector walks, list reordering) has been
indexed or cached on the model side. The result is roughly **3.7×
faster per-run** and **3.4× faster ensembles** versus an earlier code
path that walked transport / processor / manufacturer lists from
scratch each step.

## Model Behavior

### Demand Trajectory
At each step the model interpolates per-mineral annual demand between the
rows in `data/demand.csv` (default scenario `NetZero`). The 2024 row is
the anchor; later rows (e.g. 2030_NetZero, 2050_NetZero) place additional
knots that the model linearly interpolates between. The resulting
`demand_growth_factor(step)` is applied to:
- consumer `current_demand` (base demand × growth × price elasticity), and
- manufacturer `effective_capacity` (so manufacturer capacity grows with
  demand and isn't artificially the binding constraint).

### Consumer Price Elasticity
Elasticity is applied to the *product* price:
`(consumer_product_base_price + intensity × current_mineral_price)` /
`(consumer_product_base_price + intensity × initial_mineral_price)`.
For Li at 8 kg/EV and a $40 k product base, doubling the mineral price
adds <1 % to product price -- so consumer demand barely moves on
mineral-only spikes (correct -- consumers buy EVs, not Li carbonate).
The hard demand-cut threshold is also evaluated against the product
price so it doesn't trip on mineral-only excursions.

### Price Dynamics (cost-anchored, flow-driven)
At each step the model computes
- `supply_flow`  = `Σ processor.processed_this_step + recycled_received_this_step`
  (mineral actually arriving in the post-mine pipeline this step,
  not what mines are *offering* at the pithead). The previous
  upstream-only signal saw "ample supply" during chokepoint crises
  even though processors were running dry; the throughput-based
  signal collapses correctly when transit is blocked, which is the
  actual mechanism by which spot prices spike during chokepoint
  events.
- `demand_flow`  = consumer product demand × live manufacturer intensity
- `marginal_cost` = merit-order short-run marginal cost (extraction
  cost of the last operational mine that would have to be called
  online to meet `demand_flow`). When operational capacity falls
  short, MC = the highest-cost active mine (the binding marginal
  producer at full system stretch).
- `cheapest_active_cost` = cheapest extraction cost among
  operational mines.

`supply_flow` and `demand_flow` are smoothed over the rolling
`price_signal_window_steps` (default 8). The price is then updated as

```
move        = clip(-elasticity * log(supply_flow / demand_flow),
                   -max_step_pct, +max_step_pct)
anchor_pull = anchor_strength * log(marginal_cost / current_price)
new_price   = current_price * exp(move + anchor_pull)
```

and bounded by the soft cost-curve band:

```
soft_floor   = floor_cost_fraction * cheapest_active_cost
soft_ceiling = ceiling_mc_multiple * marginal_cost
new_price    = clip(new_price, soft_floor, soft_ceiling)
```

with the outer config `price_floor` / `price_ceiling` as catastrophe
backstops. The default knobs (`elasticity = 0.25`,
`max_step_pct = 0.08`, `anchor_strength = 0.10`,
`ceiling_mc_multiple = 8`, `floor_cost_fraction = 0.6`) produce
realistic price/MC ratios: ~1.2–1.5× MC in normal conditions,
2–3× MC during shortages, near MC under structural surplus.

Embargoed production goes to a domestic stockpile and is excluded
from `supply_flow`, so the price signal automatically reflects
political export-withholding. The `Marginal_Cost` and
`Cheapest_Active_Cost` series are emitted in the per-mineral CSVs
so users can see the dynamic price band against the price line.

The proportional response separates scenarios by severity (a 30 %
shortage moves the price meaningfully more than a 5 % shortage),
and the cost anchor pulls the price toward merit-order marginal cost
in steady state, providing long-run mean reversion that the bare
imbalance signal alone wouldn't.

### Random Geopolitical Events
- **Probability**: 1% per step (configurable via `--geo-prob`)
- **Duration**: 5–15 steps (random)
- **Effect**: All mines and transport in the affected jurisdiction shut down
- **Impact**: Supply shortfall → price rises → demand destruction

### Scheduled Political Embargoes
- **Trigger**: An entry in `config['political_embargoes']` (or repeated
  `--embargo` flags) fires at its specified `start_step`.
- **Duration**: Fixed `duration` steps (then automatically lifts).
- **Effect on mines**: Production continues and reserves are debited as
  normal, but output is routed to `MineAgent.domestic_stockpile` instead of
  being made available to international processors.
- **Tracked metrics**: `Embargoed_Mines_Count`,
  `Total_Embargoed_Production`, `Total_Domestic_Stockpile` (per step in the
  CSV time-series output).

### Material Substitution (with reversion)
- **Forward trigger**: Sticky `high_price_counter` increments on
  steps with price > `substitution_price_threshold` (default 1.5×
  initial price), decrements otherwise. Substitution fires when the
  counter reaches `substitution_trigger_steps` (default 10 Li/Ni,
  12 Pt), then resets.
- **Reversion trigger**: Sticky `low_price_counter` increments on
  steps with price < `substitution_revert_threshold` (default 0.667×
  initial price), decrements otherwise. Reversion fires when the
  counter reaches `substitution_revert_trigger_steps` (default 26
  Li/Ni, 39 Pt — intentionally longer than the forward trigger).
- **Forward effect**: Manufacturers reduce `mineral_intensity` by
  `substitution_rate` per cycle (5% Li/Ni, 3% Pt).
- **Reversion effect**: Manufacturers raise `mineral_intensity` by
  `substitution_revert_rate` per cycle (3% Li/Ni, 2% Pt — slower
  than the forward step, capturing the higher cost of switching
  back to the original chemistry once production is committed).
- **Bounds**: `substitution_investment` ∈ [0, `max_substitution`]
  (max 30% Li/Ni, 20% Pt). Reversion stops at 0 (i.e. intensity
  returns to its original value); forward stops at the cap.
- **Real-world analog**: LFP took share from NMC during the 2022-23
  Li price spike; cheap-Li markets see some NMC re-adoption (already
  visible in 2024 with Li back near $15k/t). Pd-rich autocats have
  flipped back to Pt-rich and back again multiple times in 2000-2024
  driven by the Pt/Pd price ratio.

### Recycling Loop
- **Product lifetime**: 520 weeks (~10 yr) for Li/Ni EV batteries,
  624 weeks (~12 yr) for Pt autocatalysts
- **Collection rate**: 30–75% of EOL products (mineral-specific)
- **Recovery efficiency**: 70–85% of collected material
- **Profitability gate**: Only process if market price > processing cost
- **As-built intensity**: each EOL deposit carries the mineral content
  that was actually built into the product, not the current intensity at
  retire time, so substitution drift doesn't mis-count legacy stock

## Validation (canonical 1248-step baseline, seed 42)

24 weekly years of simulation per mineral on the worldwide model
(per-facility mines/processors/recyclers, country-level manufacturers/
consumers, region-pair routing through the 5-chokepoint network) under
the IEA NetZero demand trajectory. Numbers regenerated from scratch by
`scripts/regenerate_outputs.py` and committed under `outputs/`. (For
ensemble medians + bands, run `--n-seeds 20`.)

| | Avg price | Volatility | Recycling rate | Substitution | Avg mothballed | Fulfillment |
|---|---:|---:|---:|---:|---:|---:|
| Lithium  | $14,895 / t        | ±$4,501     |  6.6% |  0%   | 0 / 30  | 91.2% |
| Nickel   | $11,406 / t        | ±$4,941     |  3.2% |  0%   | 3 / 29  | 90.3% |
| Platinum | $39,888,260 / t    | ±$4,786,485 |  2.4% | 20%   | 0 / 18  | 63.5% |

Mothballed counts are out of the per-mineral mine total (Li 30, Ni 29,
Pt 18 facilities). The narrative under NetZero demand:

- **Lithium** runs near baseline price most of the time
  (~$14.7 k/t avg, well below the $17 k/t initial). The
  price-responsive capacity-expansion mechanism kicks the per-step
  growth from 7.5 %/yr to 15 %/yr during the early-2030s spike
  windows, and by 2050 nameplate has expanded enough to keep
  fulfillment at ~89 %. The price drift never sustains long enough
  to trigger substitution under these knobs.
- **Nickel** is structurally oversupplied for most of the run
  (avg ~$11.6 k/t, near cheapest extraction cost). High-cost
  Australian / Canadian Ni operations run at the
  `mine_min_utilization` floor for long stretches and the
  sustained-pressure mothball trigger now fires on ~3 of 29 mines
  on average — mirroring the real 2024 wave of Australian Ni
  shutdowns under Indonesian oversupply.
- **Platinum** is supply-constrained early but the price-responsive
  expansion mechanic (1 %/yr → 8 %/yr above the threshold) closes
  the gap by mid-run. The price hits an **analytical equilibrium**
  at $46.7 M/t for the first ~7 years (this is the imbalance-
  saturated equilibrium, not the soft ceiling: when the per-step
  imbalance push is capped at +8 % and the anchor pulls toward the
  highest-cost active mine's $21 M/t, the equilibrium settles at
  `MC × exp(max_step/anchor) = $21M × exp(0.8) = $46.74 M`).
  After ~year 7 the cumulative 8 %/yr capacity expansion (~$180 →
  $565 t/yr nameplate by 2050) catches the demand curve and the
  price descends in steps to ~$33–35 M/t by 2050. Substitution
  saturates at the 20 % cap during the early plateau. Bumping the
  high-growth knob from 5 %/yr to 8 %/yr (the previous value left
  the plateau binding through year 22) is the single change that
  most reshapes Pt's trajectory.

Recycling rates emerge in the second half of the run as the EOL
stream from the realistic 10–12 yr product-lifetime delay starts to
land (Pt at 20 % mirrors the high real-world PGM autocatalyst
recovery rate; Li at 6 % and Ni at 5 % are still ramping at the end
of the 24-yr window).

**Fulfillment rate** ≈ 67–77 % across minerals. The gap to 100 %
reflects realistic supply-chain friction:
- transport lead times (1–3 wks ship to Asia, 3–6 wks ship to
  Europe, 1 wk truck domestic) leave the retailer pipeline
  partially empty during the brief stockout windows in the (s, Q)
  cycle -- each retailer typically spends ~1 step per cycle empty
  before the next shipment arrives;
- random geopolitical events shut producing jurisdictions for 5–15
  steps each, removing supply temporarily;
- per-step demand growth runs ahead of capacity build-out for some
  windows, especially mid-2040s under the NetZero ramp.

Real-world retailers smooth out the (s, Q) cycle with vendor-managed
inventory, demand forecasting, and just-in-time delivery, none of
which the model represents -- so the per-cycle stockout windows
that show up here are slightly larger than they would be in a
typical commercial supply chain.

## Political-embargo scenarios (Lithium, seed 42)

`--embargo` flags re-route affected mine output into per-facility
`MineAgent.domestic_stockpile` buckets for the configured duration. The
price signal reflects the loss because `supply_flow` excludes embargoed
production. When the embargo lifts, each mine's stockpile drains back
into supply over `post_embargo_release_steps` (default 26 weeks) so
recovery is gradual rather than instant. All scenarios run for 1248
steps with the embargo firing at step 624 (year 12). Window is the
embargo duration itself.

N=20 paired ensemble (seeds 42–61), each scenario's per-seed delta
taken against the baseline at the matching seed so shared
geopolitical events cancel out. Reported as
`mean Δ ± std [p10, p90]`.

| Scenario | In-window avg ($/t) | Δ vs baseline |
|----------|--------------------:|--------------:|
| Baseline (no embargo)         | $14,506 ± 1,096 | — |
| China only, 1 yr              | $16,543 ± 1,031 | **+15.1% ± 6.4%** [+8.3, +23.6] |
| Australia only, 1 yr          | $17,487 ± 1,076 | **+21.7% ± 6.4%** [+13.5, +29.1] |
| Chile only, 1 yr              | $18,177 ± 1,234 | **+26.4% ± 6.0%** [+19.9, +34.2] |
| **Chile + China, 1 yr**       | $21,548 ± 1,315 | **+49.8% ± 4.7%** [+43.8, +54.8] |
| **Chile + China + AUS, 5 yr** | $22,048 ± 314   | **+71.3% ± 4.3%** [+66.7, +77.5] |

Severity ordering: China alone < Australia < Chile alone <
Chile+China < big-3 5 yr. The big-3 5-year embargo (+71% ± 4%) is
clearly the largest signal — removing all three cheapest producers
for five years pushes the marginal mine to the highest-cost
operational tier and the price-responsive capacity-expansion
mechanism doesn't have time to fully fill the gap. Chile+China at
+50% ± 5% is the second most decisive shock; either single-country
embargo lands in the +15-26% range with overlapping confidence
bands. The narrow ±4% std on the big-3 scenario reflects how
robustly the cheapest-producer-removal signal dominates other
sources of seed-to-seed variance.

For Platinum, a 1-year South Africa embargo barely moves the
in-window price because the Pt baseline is already pinned at the
structural soft ceiling (8 × marginal-cost) for most of the run.
The supply gap is so binding that removing the largest producer
doesn't push price any higher; an SA embargo *during the post-2045
window where the price finally comes off the ceiling* would show
a much larger response.

| Scenario | In-window avg ($/t) | Δ vs baseline |
|----------|--------------------:|--------------:|
| Pt baseline             | $38,386,440 ± 729,210 | — |
| Pt SA embargo, 1 yr     | $38,227,412 ± 714,996 | -0.4% ± 3.2% (off-plateau, noise) |

The Pt SA embargo at year 12 now lands AFTER the early plateau has
broken (capacity has caught demand by ~year 7 under the 8 %/yr
high-growth rate), so the embargo hits a roughly-balanced market
where removing the largest producer can be partially absorbed by
remaining capacity + substitution + recycling. In-window deltas
straddle zero. Embargo scenarios firing during years 0–7 (when the
price is still at the analytical plateau) would still show ~0 %
deltas because the system is already at the imbalance-saturation
equilibrium.

## Chokepoint-crisis scenarios (seed 42, 8-week closure at step 624)

A chokepoint closure delays in-transit shipments using that route until
it reopens, and re-routes new shipments via the alternate (Cape of Good
Hope, +3–4 weeks) when one exists. Effects are smaller than embargoes
because the goods aren't lost, just delayed, and most major routes have
an alternate.

In-window average price (24 weeks from step 624, covering the closure
plus the lead-time tail) vs the no-crisis baseline at the same window:

N=20 paired ensemble (seeds 42–61), in-window = 24 weeks from
step 624. Reported as `mean Δ ± std [p10, p90]`.

| Mineral | Chokepoint closed (8 wk) | In-window avg | Δ vs baseline |
|---------|--------------------------|---------------:|--------------:|
| Li      | (baseline)               | $14,506 ± 1,096 | — |
| Li      | Suez Canal               | $14,699 ± 1,390 | +1.6% ± 8.3% [-4.5, +7.9] |
| Li      | Malacca Strait           | $14,771 ± 1,630 | +2.0% ± 8.2% [-5.8, +11.1] |
| Li      | Strait of Hormuz         | $14,465 ± 1,545 | -0.2% ± 6.6% [-7.0, +4.7] |
| Ni      | (baseline)               | $8,855 ± 218    | — |
| Ni      | Malacca Strait           | $9,389 ± 341    | **+7.0% ± 4.2%** [+4.2, +9.1] |
| Pt      | (baseline)               | $38,553,200 ± 1,090,500 | — |
| Pt      | Suez Canal               | $38,068,486 ± 1,075,611 | -1.3% ± 3.3% (off-plateau, noise) |

Short 8-week chokepoint closures produce small impacts that mostly
straddle zero at N=20. **Only Ni/Malacca shows a statistically
robust signal** (+7% ± 4%, p10/p90 both positive) — Indonesia →
China Ni transits the strait so the Cape re-route adds meaningful
lead time. The Li chokepoint scenarios all have confidence bands
overlapping zero, confirming the single-seed regen's apparent
"effects" were noise. Pt/Suez is unchanged because the Pt baseline
is already at the structural soft ceiling.

The 26-week and 52-week 2050 chokepoint scenarios (combined with
embargoes) produce much larger impacts; see the next section.

Visualization: [`outputs/embargo_comparison.png`](outputs/embargo_comparison.png).

## 2050 combined scenarios (1352 steps, seed 42)

`outputs/2050/` contains six combined embargo + chokepoint scenarios
that fire at different points along the 2024-2050 NetZero ramp. Window
is the longest event in each scenario. Plots:
[`outputs/2050/scenarios_2050.png`](outputs/2050/scenarios_2050.png),
[`outputs/2050/scenario_summary.png`](outputs/2050/scenario_summary.png).

N=20 paired ensemble (seeds 42–61). 2050 baseline averages: Li
$14,328 ± 114, Ni $11,256 ± 78, Pt $39,298,152 ± 216,962.

| Mineral | Scenario | In-window avg | Δ vs baseline |
|---------|---------|---------------:|--------------:|
| Li | asia_crisis_2030       | $24,255 ± 696       | **+17.2% ± 5.3%** [+11.3, +23.6] |
| Li | li_nationalism_2035    | $23,067 ± 522       | **+56.8% ± 6.9%** [+49.5, +68.5] |
| Li | multi_crisis_2040      | $11,203 ± 431       | -0.9% ± 4.5% [-6.2, +3.4] |
| Ni | asia_crisis_2030       | $17,927 ± 695       | **+6.3% ± 4.9%** [+1.5, +12.7] |
| Ni | indonesia_squeeze_2032 | $15,001 ± 471       | **+12.8% ± 5.6%** [+5.8, +19.7] |
| Ni | multi_crisis_2040      | $8,086 ± 680        | +13.8% ± 11.6% [+6.5, +30.7] |
| Pt | asia_crisis_2030       | $46,556,617 ± 277,945 | +0.0% ± 0.7% (still in-plateau window) |
| Pt | sa_pt_crisis_2030      | $46,659,174 ± 219,156 | +0.1% ± 0.7% (still in-plateau window) |
| Pt | multi_crisis_2040      | $35,311,292 ± 447,392 | -0.5% ± 2.0% (off-plateau, noise) |

**Robust signals** (p10 and p90 both positive, narrow band):
- `li_nationalism_2035`: +56.8% ± 6.9% — Chile + Australia 2-year
  embargo plus 1-year Suez closure removes the cheapest Li supply
  for long enough that the price-responsive capacity expansion
  can't catch up.
- `asia_crisis_2030` Li: +17.2% ± 5.3% — China embargo + Malacca/
  Suez closures hit lithium less than they hit nickel because Li
  demand is more elastic and substitutes have time to enter.
- `indonesia_squeeze_2032`: +12.8% ± 5.6% — Indonesian Ni embargo
  + 6-month Malacca closure; the second-most decisive single-source
  Ni scenario after multi_crisis_2040.
- `multi_crisis_2040` Ni: +13.8% ± 11.6% — wide variance because
  the scenario fires deep in the run when the system has more
  capacity built out, but mean and lower percentile are clearly
  positive.

**Pt scenarios**:
- `asia_crisis_2030` and `sa_pt_crisis_2030` both fire at step 312
  (year 6), inside the early plateau window where Pt sits at the
  imbalance-saturation equilibrium ($46.7 M/t). Incremental shocks
  during this window can't push the price higher because the
  per-step move is already at its `+max_step_pct` cap.
- `multi_crisis_2040` fires at step 832 (year 16), well after the
  plateau breaks under the 8 %/yr high-growth knob. Embargo +
  chokepoint hits a roughly-balanced market here and the deltas
  straddle zero (-0.5% ± 2.0%).
- See the **Platinum** bullet in the canonical-baseline narrative
  for the plateau / equilibrium derivation.

**Inconclusive**:
- `multi_crisis_2040` Li: -0.9% ± 4.5% — confidence band straddles
  zero. Russia and Indonesia aren't significant Li producers, so
  the embargo doesn't bind; the chokepoint pieces are short
  enough to be within seed noise.

## Documentation

- **[Architecture Plan](plans/architecture_plan.md)** - Detailed technical design
- **[Quick Reference](plans/quick_reference.md)** - Lookup guide for agents and parameters
- **[Implementation Roadmap](plans/implementation_roadmap.md)** - Build sequence and timeline

## Model Parameters by Mineral

| Parameter | Lithium | Nickel | Platinum |
|-----------|---------|--------|----------|
| Initial Price ($/ton) | 17,000 | 18,000 | 30,000,000 |
| Mineral intensity (t / product unit) | 0.008 (8 kg Li/EV) | 0.04 (40 kg Ni/EV) | 3 × 10⁻⁶ (3 g Pt/catalyst) |
| Product lifetime (steps) | 520 (~10 yr) | 520 (~10 yr) | 624 (~12 yr) |
| Conversion Efficiency (per facility, avg) | 0.80 | 0.78 | 0.85 |
| Collection Rate (aggregate) | 0.30 | 0.60 | 0.75 |
| Recovery Efficiency (per facility, avg) | 0.93 | 0.91 | 0.91 |

## Agent counts (worldwide model, default `agents_per_gdp_billion = 500`)

| Agent type        | Lithium | Nickel | Platinum |
|-------------------|--------:|-------:|---------:|
| MineAgent         |   30    |   29   |   18     |
| ProcessorAgent    |   25    |   28   |   11     |
| RecyclingAgent    |   20    |   25   |   15     |
| ManufacturerAgent |  142    |  146   |  152     |
| RetailerAgent     |  175    |  178   |  178     |
| ConsumerAgent     |  175    |  178   |  178     |
| TransportAgent    |   85    |   85   |   85     |
| **Total**         | **652** | **669** | **637** |

Mines, processors, and recyclers come from the per-facility CSVs in
`data/`. Manufacturer / retailer / consumer counts come from
country-level share CSVs (~12 countries for Li manufacturers, ~25
for consumers) **fanned out by 2024 GDP** via
`data/country_gdp.csv`: each (country, share) entry becomes
`max(1, round(gdp_billion / agents_per_gdp_billion))` agents (USA
56, China 36, Germany 9, ..., top-30 cutoff 1; Hungary, Vietnam,
"Other countries" etc. stay at 1 since they're not in the top-30
table). Per-country aggregate share is unchanged; only the
within-country granularity increases. Transport agents are the same
global fleet across all minerals, defined in `data/transport_fleet.csv`
(per-country ship/rail/truck split, ~85 agents over ~26 countries).

Set `agents_per_gdp_billion` to a larger value (e.g. 2000) to halve
the agent count for faster smoke runs without changing aggregate
behaviour, or smaller (e.g. 100) for a much higher resolution
within-country population.

### Tunable knobs (with sensible defaults)

| Knob | Default | Effect |
|------|---------|--------|
| `mine_restart_margin` | 1.2 | A mothballed mine reopens when price > extraction_cost × this. |
| `mine_restart_lag_steps` | 26 (40 Pt) | Cold-restart delay: steps a mothballed mine takes to re-open if it has been offline for longer than `mine_warm_restart_window_steps`. Aborts if price slips back below the trigger mid-counter. |
| `mine_warm_restart_lag_steps` | 12 (26 Pt) | Warm-restart delay if the mine was mothballed within `mine_warm_restart_window_steps`. Equipment + crews still in place; restart is faster. |
| `mine_warm_restart_window_steps` | 52 (78 Pt) | How recently a mine had to be mothballed to qualify for the warm-restart lag. |
| `mothball_trigger_steps` | 52 | Number of consecutive (net) below-cash-cost steps a mine accumulates before mothballing. Sticky counter: decrements on price recovery. |
| `mine_cash_cost_fraction` | 0.65 (0.60 Pt) | Cash cost = `extraction_cost × this`. The mothball trigger only fires below cash cost (not below extraction cost), reflecting that mines stay open at reduced rates above cash cost. |
| `mine_baseline_utilization` | 0.75 | Anchor: when ratio of price/cost lies on the curve such that the linear interpolation gives this value, output equals the USGS-reported baseline. |
| `mine_min_utilization` / `mine_max_utilization` | 0.5 / 1.0 | Floor and ceiling on per-step output as fraction of nameplate. |
| `mine_max_utilization_ratio` | 2.5 | price/extraction-cost ratio at which utilization saturates at `mine_max_utilization`. |
| `mine_capacity_growth_per_year` | 0.075 Li / 0.04 Ni / 0.01 Pt | Baseline per-year growth applied to each mine's `production_capacity`, only on operational steps (mothballed / disrupted facilities don't expand). |
| `mine_capacity_growth_per_year_high` | 0.15 Li / 0.10 Ni / 0.05 Pt | Per-year growth used while the price-responsive expansion counter is tripped. Reflects the real-world capex response to multi-year price spikes that a static baseline rate misses. |
| `mine_expansion_price_threshold` | 2.0 | Per-step price > `extraction_cost × this` increments the capex counter; below it the counter decrements. |
| `mine_expansion_trigger_steps` | 52 (~1 yr) | Counter level at which baseline growth flips to the high rate. Roughly matches greenfield permit-to-pour lead time. |
| `reserve_replacement_rate` | 0.70 Li / 0.50 Ni / 0.30 Pt | Fraction of *this-step extraction* added back to reserves on operational steps (was: fraction of nameplate per step regardless of operational status). |
| `post_embargo_release_steps` | 26 | Steps over which a lifted embargo drains the domestic stockpile back into supply. |
| `processor_safety_stock_weeks` | 2.0 | Buffer (in weeks of *output* capacity) below which a processor won't sell. |
| `processor_inventory_cap_weeks` | 8.0 | Ceiling: a processor stops buying ore once expected post-processing inventory (current + (raw_ore + in_transit) × efficiency) would exceed this. Sole constraint on ore purchasing — the in-flight pipeline naturally fills to roughly (cap − safety) weeks of input throughput. |
| `processor_warmstart_safety_multiplier` | 2.0 | Initial processed inventory as a multiple of safety stock (avoids dead pipeline during transport warmup). |
| `processor_capacity_growth_per_year` | 0.075 Li / 0.04 Ni / 0.01 Pt | Per-year growth applied to each processor's `output_capacity` per step. Mirrors the mining capacity-expansion knob so refining doesn't bottleneck multi-decade demand growth. |
| `manufacturer_target_inventory_weeks` | 4 | Manufacturer's target input buffer in weeks of full-capacity production. |
| `manufacturer_order_rate` | 0.5 | Per-step fraction of the target gap a manufacturer orders. |
| `manufacturer_capacity_headroom` | 1.5 | Aggregate manufacturer capacity vs. baseline product demand. Capacity also scales with the demand-trajectory growth factor. |
| `manufacturer_warmstart_input_fraction` | 0.5 | Initial input inventory as a fraction of target. |
| `agents_per_gdp_billion` | 500.0 | Agent fan-out density: each (country, share) entry in the consumer / manufacturer share CSVs becomes `max(1, round(country_gdp_billion / this))` agents (USA → 56, China → 36, ..., top-30 cutoff → 1). Larger value = fewer agents (faster runs); smaller = more granular within-country populations. Aggregate per-country share / demand is preserved regardless. Countries not in `data/country_gdp.csv` get 1 agent. |
| `retailer_reorder_point_multiplier` | 4.0 (5.0 Pt) | Reorder point in *weeks of EWMA-tracked realised demand*. Default covers a 3-week ship lead time + 1 week safety stock; Pt uses 5 weeks for the higher transport-security buffer. |
| `retailer_order_quantity_multiplier` | 3.0 (3.5 Pt) | Order quantity in weeks of EWMA-tracked demand. |
| `retailer_demand_ewma_alpha` | 0.05 | Smoothing weight for realised consumer requests (~13-week half-life). Tracks both the long-run trend and consumer price-elasticity contraction during multi-month spikes; a higher alpha makes the policy more reactive (and noisier). |
| `retailer_max_pending_orders` | 3 | Max simultaneous outstanding shipments per retailer. |
| `price_signal_window_steps` | 8 | Smoothing window for the supply/demand price signal. |
| `price_elasticity` | 0.25 | Per-step price move per unit of `log(supply/demand)`. A 30 % shortage moves the price ~7 %/step (capped at `price_max_step_pct`). |
| `price_max_step_pct` | 0.08 | Hard cap on per-step move magnitude. |
| `price_anchor_strength` | 0.10 | Per-step log-pull toward marginal cost. 0.10 closes ~10 % of the log-gap each step. |
| `price_ceiling_mc_multiple` | 8.0 | Soft ceiling = N × marginal cost. Lets a true crisis show as a price level proportional to the cost curve. |
| `price_floor_cost_fraction` | 0.6 | Soft floor = f × cheapest-active extraction cost. Allows brief dips below cash cost but bounds them. |
| `transport_max_deferral_steps` | 26 (~6 mo) | Max steps a single shipment can be deferred behind a closed chokepoint or disrupted corridor. After this, the shipment is dropped and its mineral content booked to `Lost_In_Transit_Mineral` (so a permanently-closed chokepoint doesn't accumulate cargo indefinitely). Per-shipment lead times come from `src/data/routing.py`'s route table, not from config. |
| `geopolitical_processor_event_share` | 0.30 | Probability that a random geopolitical event hits the refining tier (smelter / refinery outage) instead of the mining tier. Disrupted processors skip purchasing / processing / selling for the duration; inbound shipments still arrive (ore stockpiles at the gate). |
| `consumer_product_base_price` | $40k Li/Ni, $30k Pt | Non-mineral component of finished-product price. Consumer elasticity is applied to (base + intensity × mineral_price), not the bare mineral price -- so a 50 % mineral spike adds <1 % to the perceived product price for Li/Ni and the demand response is modest, matching how end consumers actually react to upstream commodity moves. |
| `demand_scenario` | `NetZero` | Which scenario column in `data/demand.csv` to interpolate against between the 2024 baseline row and any future-year rows. |
| `substitution_price_threshold` | 1.5 × initial | Price above which the forward-substitution counter accumulates. |
| `substitution_trigger_steps` | 10 (12 Pt) | Consecutive (net) high-price steps before substitution fires. |
| `substitution_rate` | 0.05 (0.03 Pt) | Per-cycle reduction in `mineral_intensity`. |
| `max_substitution` | 0.30 (0.20 Pt) | Cumulative cap on intensity reduction. |
| `substitution_revert_threshold` | 0.667 × initial | Price below which the reversion counter accumulates. Default is the symmetric dual of the forward 1.5x threshold (in log-space). |
| `substitution_revert_trigger_steps` | 26 (39 Pt) | Consecutive (net) low-price steps before reversion fires. Intentionally longer than the forward trigger -- switching back to the original chemistry has higher activation cost than initial adoption. |
| `substitution_revert_rate` | 0.03 (0.02 Pt) | Per-cycle increase in `mineral_intensity` (slower than `substitution_rate`). Reversion stops at 0 (intensity returns to initial). |

## Contributing

Contributions are welcome! Areas for extension:
- Additional minerals (cobalt, copper, rare earths) — add per-facility
  CSVs in `data/` (`<mineral>_mines.csv`, `_processors.csv`,
  `_recyclers.csv`, `_manufacturers.csv`, `_consumers.csv`), append the
  global demand to `data/demand.csv`, register the mineral prefix in
  `src/data/data_loader.py:_PREFIX`, and add a `<mineral>_config.py`.
- Refine the curated facility data (capacity, cost, recovery efficiency)
  from primary sources (USGS Minerals Yearbook, IEA, S&P).
- Add new chokepoints (Bab el-Mandeb, Bosporus, Danish Straits, etc.)
  to `src/data/routing.py:CHOKEPOINTS` and the route table.
- Per-jurisdiction risk weights for random geopolitical events
  (currently uniform across producing countries).
- Trade-policy scenarios (tariffs, export quotas, friend-shoring)
  building on the embargo primitive.
- Heterogeneous consumers (price sensitivity drawn from a distribution
  rather than identical aggregates per country).
- New-mine onset (greenfield projects coming online over time) rather
  than only growing existing facilities. (The price-responsive growth
  on existing facilities is implemented; a discrete greenfield-onset
  channel would capture the lumpier real-world capacity additions.)
- Bilateral trade-flow constraints (today every manufacturer can source
  from every processor; in reality there are long-term contracts).
- Multi-mineral coupling for substitution (today substitution is a
  per-manufacturer intensity ratchet; LFP↔NMC and Pt↔Pd flips also
  shift the demand for the *other* mineral, which would couple
  Li/Ni/Pt markets).
- Financial accounting downstream of the mine (processor / manufacturer
  margins, capital constraints, bankruptcy under sustained losses).
- Climate impact on mining operations.
- Technology learning curves on extraction cost.

## License

MIT License - See LICENSE file for details

## Citation

If you use this model in your research, please cite:

```bibtex
@software{pyexamine2026,
  title={pyExaMINE: ExaScale Minerals \& Infrastructure Network Evaluation},
  subtitle={Agent-Based Modeling of Critical Minerals Supply Chains},
  author={Peter Nugent},
  year={2026},
  url={https://github.com/nugent68/pyExaMINE}
}
```

## Contact

- **Issues**: [GitHub Issues](https://github.com/nugent68/pyExaMINE/issues)
- **Email**: penugent@lbl.gvo

## Acknowledgments

- USGS Critical Minerals Mapping Initiative for data
- Mesa development team for the ABM framework
- Critical minerals research community

---

**Status**: ✅ Implemented, validated, and mass-balance-verified
end-to-end. Worldwide per-facility model with per-country
manufacturers/retailers/consumers, labelled agents, region-pair
routing through five maritime chokepoints, CLI-schedulable
embargoes + chokepoint crises, and an IEA-NetZero demand trajectory
wired through to consumer demand and manufacturer + processor
capacity. The price model is **cost-anchored**: a proportional
log-linear move (driven by *processor throughput* — the mineral
actually arriving in the pipeline this step, not what mines are
offering at the pithead) plus a pull toward merit-order marginal
cost, bounded by a soft band that scales with the live cost curve.
**Mineral mass is conserved end-to-end**: every storage point and
loss bucket is summed each step into a `Mass_Balance_Discrepancy`
diagnostic that holds at ~10⁻¹⁵ relative across every committed run
(EOL roll-forward, recycler→processor backpressure, post-embargo
stockpile bleed, transport deferral cap with a `Lost_In_Transit_
Mineral` counter for the rare cases where chokepoint closures
exceed the cap). Mines have **price-responsive capacity growth**
(baseline rate flips to a high rate under sustained price >
2 × extraction cost), a **sustained-pressure mothball** decision
(52 weeks of price below cash cost) with **warm/cold restart
lags**, and reserve replacement scaled to actual extraction.
Refining capacity grows in lock-step with mines, and **refineries
can be disrupted** by geopolitical events. Retailer (s,Q) sizing
tracks an **EWMA of realised consumer demand**, capturing both the
long-run trend and consumer price-elasticity contraction during
multi-month spikes. Material substitution is **reversible** with
asymmetric forward/reverse triggers.

Canonical 24-year baselines for Li/Ni/Pt and 13 scenario runs are
committed under `outputs/` (embargoes: `chile_li`, `china_li`,
`australia_li`, `chile_china_li`, `big3_li_5yr`, `sa_pt`;
chokepoint closures: `suez_li`, `malacca_li`, `hormuz_li`,
`malacca_ni`, `suez_pt`). Six 26-year combined embargo + chokepoint
scenarios are committed under `outputs/2050/` (`asia_crisis_2030`,
`indonesia_squeeze_2032`, `sa_pt_crisis_2030`,
`li_nationalism_2035`, `multi_crisis_2040`). All outputs are
reproducible from `scripts/regenerate_outputs.py`. For
ensemble-mean ± std comparison statistics, run
`scripts/regenerate_outputs.py --n-seeds 20` (~25 min on an 8-core
machine via the parallel ProcessPoolExecutor backend).
