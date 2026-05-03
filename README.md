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
- Each agent labelled with its country and facility name (e.g.
  `Australia/Greenbushes`, `China/Tianqi-Sichuan`); labels surface in
  outputs and plots.
- Calibrated extraction costs per facility, regional energy costs, and
  recovery / conversion efficiencies per facility.

### 🤖 7 Agent Types
- **MineAgent** - Extracts raw minerals; mothballs when price < extraction
  cost and reopens above `extraction_cost * mine_restart_margin` after
  a multi-step `restart_counter` lag. **Output flexes with price** via
  a configurable utilization factor (linear ramp between
  `mine_min_utilization` and `mine_max_utilization`), rescaled so USGS
  baseline output is preserved at the anchored "normal" multiple of
  cost. Each step also grows `production_capacity` at
  `mine_capacity_growth_per_year / steps_per_year` and replenishes
  reserves at `production_capacity * reserve_replacement_rate`, so a
  multi-decade run actually models capacity build-out and exploration.
  Unsold extracted ore carries forward in a `pithead_stockpile`
  instead of being silently destroyed at step boundaries. Subject to
  random disruptions, geopolitical events, and embargoes; embargo
  stockpiles are released back into supply over `post_embargo_release_steps`
  once the embargo lifts.
- **ProcessorAgent** - Converts ore to processed material; receives
  recycled mineral via a dedicated channel that bypasses the conversion
  stage. **Inventory backpressure**: ore purchases halt when expected
  post-processing inventory would exceed `processor_inventory_cap_weeks`,
  so processed inventory can't grow without bound when downstream demand
  collapses.
- **TransportAgent** - Real shipment pipeline with mode-specific lead
  times. Mine→processor uses `ship` (default 7 weeks); processor→
  manufacturer uses `rail` (default 4 weeks). Disrupted jurisdictions
  delay any shipment touching them and self-clear when the geopolitical
  window ends.
- **ManufacturerAgent** - Produces goods, invests in substitution. Target
  input inventory is correctly sized in mineral tonnes. Each batch's
  **as-built mineral content** is tracked alongside units through the
  output buffer, so EOL deposits use the intensity at manufacture time
  instead of re-reading current intensity at retire time (matters after
  substitution events).
- **RetailerAgent** - Manages inventory with (s,Q) policy and a multi-order
  pipeline (up to `retailer_max_pending_orders` outstanding). Embedded
  mineral content travels with shipped goods to preserve as-built
  intensity through to consumers.
- **ConsumerAgent** - Generates price-sensitive demand and shops retailers
  in randomized order each step (no first-retailer monopoly). Deposits
  the embedded mineral content of purchases into the EOL pool with the
  configured product-lifetime delay.
- **RecyclingAgent** - Recovers minerals from end-of-life products,
  claiming a fair share of each step's initial EOL bucket capped by
  the facility's per-step capacity. Recycled mineral is dispatched
  through the routing engine (region-preferenced: same-country
  processors first, then same-region, then global) so it incurs real
  lead times and chokepoint exposure rather than teleporting.

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
  steps_per_year` per step, and reserves are replenished by
  `production_capacity * reserve_replacement_rate`. Defaults are
  mineral-specific (Li 7.5%/yr / 70% replacement; Ni 4% / 50%; Pt
  1% / 30%) -- so a 24-yr run actually models the build-out.
- **Mine Restart Lag** - A mothballed mine no longer reopens on the
  first good price tick. When price exceeds the restart threshold a
  multi-step `restart_counter` ticks down (default 26 weeks Li/Ni,
  40 weeks Pt) before extraction resumes; the restart aborts if
  price slips back below the threshold mid-counter.
- **Mineral mass conservation** - Unsold mine output is no longer
  silently destroyed at step boundaries. Each mine carries unsold
  production into a `pithead_stockpile` that is offered again the
  next step. The same path absorbs unsold post-embargo stockpile
  releases. Retailer warm-start inventory is initialised with the
  embedded mineral content of its starting stock, so the first
  product-lifetime cycle of EOL deposits is no longer biased to zero.
- **Tier-Ordered Scheduling** - Within each step, agents are activated in
  supply-chain order: mines → recyclers → processors → manufacturers →
  retailers → consumers → transport (last so shipments accepted earlier
  in the step queue with the full lead time, not zero). Within each tier
  the activation order is shuffled (using the seeded RNG) for fairness.
- **Real Transport Pipeline with Routing** - Every cross-border shipment
  (mine→processor, processor→manufacturer, manufacturer→retailer, and
  recycler→processor) is routed through `src/data/routing.py`'s
  region-pair table. Each O-D pair has a primary route (chokepoints
  traversed + lead-time weeks + mode) and zero or more alternates. The
  dispatcher picks the first open route at acceptance time; if a
  chokepoint closes mid-transit, delivery is deferred until it reopens
  (so existing in-flight shipments pile up rather than vanish). Recycled
  supply is region-preferenced: a recycler ships first to processors in
  the same country, else same region, else globally -- with the same
  lead times and chokepoint exposure as primary mineral.
- **Chokepoint Crises** - Five maritime chokepoints (Strait of Hormuz,
  Suez Canal, Malacca Strait, Panama Canal, Cape of Good Hope) can be
  closed for a configurable window via `--chokepoint-crisis`. While
  closed, every route that traverses the chokepoint is unavailable;
  shipments either re-route via a longer alternate (Suez closed → Cape,
  +3-4 weeks typical) or wait for reopening if every alternate is also
  blocked.
- **Market Pricing** - Flow-based signal: smoothed (supply / demand) ratio over
  a rolling window (default 8 steps) drives ±5% price moves; ratio < 0.95 →
  shortage → price up, ratio > 1.10 → surplus → price down.
- **Price-Responsive Mine Utilization** - Mines run between
  `mine_min_utilization` and `mine_max_utilization` of nameplate capacity
  depending on the price/extraction-cost ratio. At the anchored normal
  ratio the output matches USGS-reported baseline; above it mines ramp
  up; below it (but above mothball threshold) they ramp down.
- **Random Geopolitical Events** - Stochastic shutdowns of jurisdictions
  (default 1% probability per step, 5–15 steps duration). Disrupted
  jurisdictions automatically clear from transport agents when the
  window ends.
- **Scheduled Political Embargoes** - A country can withhold its mine output
  from the international market starting on a chosen step for a fixed
  duration. Mines keep extracting; production accumulates in a domestic
  stockpile rather than reaching foreign processors. **On lift, the
  stockpile drains back into available supply** over
  `post_embargo_release_steps` (default 26) instead of being lost.
- **Material Substitution** - Manufacturers reduce mineral intensity under
  sustained high prices. The trigger counter is "sticky": brief dips
  decrement rather than reset, so substitution responds to sustained
  pressure rather than requiring an unbroken streak. As-built intensity
  travels with each batch through the chain so EOL recovery is correct
  even after intensity drifts down.
- **Circular Economy** - Recycling loop with a realistic product-lifetime
  lag (520 weeks for Li/Ni EVs, 624 weeks for Pt autocats). Each step's
  EOL bucket is snapshotted before any recycler runs, so multiple
  recyclers split it fairly (no compounding shortfall from sequential
  collection on the same shrinking pot).
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
│   ├── baseline/, chile_li/, big3_li_5yr/, sa_pt/, …   # embargo scenarios
│   ├── suez_li/, malacca_ni/, hormuz_li/, suez_pt/     # chokepoint scenarios
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
   - Step-by-step metrics for all tracked variables

3. **Summary Statistics**: `{mineral}_summary_stats.txt`
   - Key statistics: average price, total production, recycling rate, etc.

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

### Price Dynamics (flow-based)
At each step the model computes
- `supply_flow`  = mine output offered to the market + recycled supply
- `demand_flow`  = consumer product demand × live manufacturer intensity

then smooths both over a rolling window (default 8 steps) and updates the
global price:
- `ratio < 0.95` → price up 5%
- `ratio > 1.10` → price down 5%
- otherwise unchanged

Price is bounded between `price_floor` (40% of initial) and `price_ceiling`
(300% of initial). Embargoed production goes to a domestic stockpile and is
excluded from `supply_flow`, so the price signal automatically reflects
political export-withholding.

Tunable knobs (per-config): `price_signal_window_steps`,
`price_shortage_ratio`, `price_surplus_ratio`.

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

### Material Substitution
- **Trigger**: Sticky counter — increments on high-price steps,
  decrements (but does not reset) on dips. Investment fires once the
  counter reaches `substitution_trigger_steps` (default 10), then resets.
- **Effect**: Manufacturers reduce `mineral_intensity` by 5% per cycle
- **Max reduction**: 30% (Li/Ni), 20% (Pt)
- **Irreversible**: Once invested, intensity stays reduced

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
`scripts/regenerate_outputs.py` and committed under `outputs/`.

| | Avg price | Volatility | Recycling rate | Substitution | Avg mothballed |
|---|---:|---:|---:|---:|---:|
| Lithium  | $17,240 / t       | ±$4,034     |  1.8% | 15%   | 1.80 / 30 |
| Nickel   | $11,538 / t       | ±$2,548     |  3.7% |  0%   | 7.89 / 29 |
| Platinum | $89,176,100 / t   | ±$5,486,679 |  5.7% | 20%   | 0.00 / 18 |

Mothballed counts are out of the per-mineral mine total (Li 30, Ni 29,
Pt 18 facilities). The narrative under NetZero demand is:

- **Lithium** is supply-constrained from the late 2030s onward.
  Capacity grows ~6× by 2050 (7.5 %/yr CAGR) but demand grows ~10×, so
  prices drift up, fewer mines mothball, and substitution ratchets to
  15 %.
- **Nickel** is the opposite story. Indonesian supply growth (modelled
  at 4 %/yr) outpaces NetZero Ni demand (2.0× by 2050), pushing price
  *below* the high-cost mines' breakeven. Australia, USA, and Canada
  facilities (extraction cost $10.8k–$12.6k vs avg $11.8k price)
  mothball for long stretches -- mirroring the real 2024 closures of
  Australian Ni operations under Indonesian oversupply. Substitution
  doesn't trigger because price stays moderate.
- **Platinum** is severely constrained. Capacity grows only 1 %/yr
  (Pt mining is mature) while NetZero fuel-cell + autocat demand grows
  2.4×. Price hits the $90 M/t ceiling in the back half of the run and
  trips the 20 % maximum substitution.

Recycling rates remain modest because the realistic 10–12 yr product
lifetime means the EOL stream only emerges in the second half of the
24-year run -- and the denominator (mine output) is itself growing.
The per-mineral recovery cap `collection_rate × recovery_efficiency` =
21 % / 45 % / 64 % is approached only after 1–2 product-lifetime
cycles.

A note on **fulfillment rate** in the per-mineral summary stats: with
realistic intensities, total *product* demand at the consumers exceeds
what current mineral supply can support (`baseline_product_demand =
mineral_demand / intensity`, where intensity is per-EV / per-autocat).
Fulfillment rates (~12 % Li, ~15 % Ni, ~18 % Pt) reflect this
mineral-supply constraint on aspirational product demand, not a model
bug. The mineral-tonnage flow itself stays balanced (mine output
≈ mineral demand at the manufacturer).

## Political-embargo scenarios (Lithium, seed 42)

`--embargo` flags re-route affected mine output into per-facility
`MineAgent.domestic_stockpile` buckets for the configured duration. The
price signal reflects the loss because `supply_flow` excludes embargoed
production. When the embargo lifts, each mine's stockpile drains back
into supply over `post_embargo_release_steps` (default 26 weeks) so
recovery is gradual rather than instant. All scenarios run for 1248
steps with the embargo firing at step 624 (year 12). Window is the
embargo duration itself.

| Scenario | In-window avg ($/t) | Δ vs baseline |
|----------|--------------------:|--------------:|
| Baseline (no embargo)         | $18,438 | — |
| Chile only, 1 yr              | $25,274 | +37.1% |
| China only, 1 yr              | $25,688 | +39.3% |
| **Australia only, 1 yr**      | **$42,443** | **+130.2%** |
| Chile + China, 1 yr           | $42,443 | +130.2% |
| **Chile + China + AUS, 5 yr** | **$49,414** | **+168.0%** |

By step 624 the system is already running tight under NetZero demand
growth (mine capacity has grown ~2.4× while demand has grown ~5.5×),
so single-country embargoes produce 30–40% in-window deltas where the
old static-demand baseline produced ~5–15%. Australia (the largest
single Li producer in the data) and Chile+China combined both push the
price to the configured ceiling of $51 k/t for most of the embargo
window -- which is why their averages converge. The 5-year big-3
embargo pushes manufacturers to the 30 % substitution cap and pins
the price at the ceiling for years.

> **Note on price-ceiling saturation.** Under NetZero demand growth
> several embargo and 2050 scenarios drive Li and Pt prices to their
> configured ceilings ($51 k/t, $90 M/t). Once at the ceiling a more-
> severe scenario can't show as a higher number, so e.g. Australia
> alone and Chile+China both produce identical $42,443 averages.
> Raising the per-mineral `price_ceiling` would let the model
> distinguish them; we keep the current ceiling because real spot
> markets don't quote unbounded prices either.

For Platinum, by step 624 the price is already pinned to the ceiling
under NetZero demand, so a 1-year South Africa embargo only adds a
~6 % in-window delta. The interesting Pt scenario in this regime is
the *2030* version (see "2050 combined scenarios" below): when the
embargo fires before the ceiling has been reached, SA's withdrawal
saturates the ceiling immediately.

| Scenario | In-window avg ($/t) | Δ vs baseline |
|----------|--------------------:|--------------:|
| Pt baseline             | $84,978,048 | — |
| Pt SA embargo, 1 yr     | $90,000,000 | +5.9% (at ceiling) |

## Chokepoint-crisis scenarios (seed 42, 8-week closure at step 624)

A chokepoint closure delays in-transit shipments using that route until
it reopens, and re-routes new shipments via the alternate (Cape of Good
Hope, +3–4 weeks) when one exists. Effects are smaller than embargoes
because the goods aren't lost, just delayed, and most major routes have
an alternate.

In-window average price (24 weeks from step 624, covering the closure
plus the lead-time tail) vs the no-crisis baseline at the same window:

| Mineral | Chokepoint closed (8 wk) | In-window avg | Δ vs baseline |
|---------|--------------------------|---------------:|--------------:|
| Li      | (baseline, no crisis)    | $18,438        | — |
| Li      | Suez Canal               | $18,438        |  0.0% |
| Li      | Malacca Strait           | $18,438        |  0.0% |
| Li      | Strait of Hormuz         | $19,339        | +4.9% |
| Ni      | (baseline)               | $11,582        | — |
| Ni      | Malacca Strait           | $10,354        | -10.6% |
| Pt      | (baseline)               | $79,119,103    | — |
| Pt      | Suez Canal               | $78,595,298    | -0.7% (at ceiling) |

Most short closures look like noise once the routing alt-route logic
kicks in. The Hormuz/Li delta (+4.9 %) is the surprising one and
reflects how routing fallbacks shift inbound timing for Australian and
Chilean Li headed to Saudi/UAE manufacturing -- the Cape alternate
adds ~5 weeks. The Malacca/Ni delta is negative simply because the
nickel routing table maps Indonesia→China to a no-chokepoint route
(direct from Sulawesi to Shanghai), so closure of Malacca only
re-routes Indonesia→India / Indonesia→EU shipments and the resulting
slight RNG-order shift happens to land below baseline in this window.
The Pt/Suez run is at the price ceiling for the entire window so the
delta is just rounding.

The model now distinguishes *short* chokepoint events (modest impact,
mostly absorbed by alt-routing) from *long* ones (the 26-week and
52-week 2050 chokepoint scenarios below produce large impacts when
combined with embargoes).

Visualization: [`outputs/embargo_comparison.png`](outputs/embargo_comparison.png).

## 2050 combined scenarios (1352 steps, seed 42)

`outputs/2050/` contains six combined embargo + chokepoint scenarios
that fire at different points along the 2024-2050 NetZero ramp. Window
is the longest event in each scenario. Plots:
[`outputs/2050/scenarios_2050.png`](outputs/2050/scenarios_2050.png),
[`outputs/2050/scenario_summary.png`](outputs/2050/scenario_summary.png).

| Mineral | Scenario | In-window avg | Δ vs baseline |
|---------|---------|---------------:|--------------:|
| Li | asia_crisis_2030       | $22,457        | +28.9% |
| Li | li_nationalism_2035    | $48,356        | +166.0% (at ceiling) |
| Li | multi_crisis_2040      | $17,587        | -0.3% (Russia/Indo aren't Li) |
| Ni | asia_crisis_2030       | $14,845        |  0.0% (China is processor not producer) |
| Ni | indonesia_squeeze_2032 | $47,224        | +245.8% |
| Ni | multi_crisis_2040      | $41,594        | +304.6% |
| Pt | asia_crisis_2030       | $90,000,000    | at ceiling |
| Pt | sa_pt_crisis_2030      | $90,000,000    | at ceiling |
| Pt | multi_crisis_2040      | $90,000,000    | at ceiling |

Indonesia Ni is the most decisive single-source dependency the model
captures: the 2-yr `indonesia_squeeze_2032` and the Russia+Indonesia
`multi_crisis_2040` produce 200-300 % in-window price moves. Pt is
already pinned to its ceiling under NetZero demand by 2030, which is
why all three Pt scenarios show "at ceiling" -- the interesting
contrast is the *trajectory* of the spike, visible in
`scenarios_2050.png`.

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

## Agent counts (worldwide model)

| Agent type        | Lithium | Nickel | Platinum |
|-------------------|--------:|-------:|---------:|
| MineAgent         |   30    |   29   |   18     |
| ProcessorAgent    |   25    |   28   |   11     |
| RecyclingAgent    |   20    |   25   |   15     |
| ManufacturerAgent |   12    |   15   |   13     |
| RetailerAgent     |   25    |   25   |   25     |
| ConsumerAgent     |   25    |   25   |   25     |
| TransportAgent    |   85    |   85   |   85     |
| **Total**         | **222** | **232** | **192** |

Mines, processors, and recyclers come from the per-facility CSVs in
`data/`. Manufacturer counts equal the producing-country count from
`{mineral}_manufacturers.csv`. Retailers and consumers are one per
country in `{mineral}_consumers.csv` (~25 countries cover ~95% of
world demand). Transport agents are the same global fleet across all
minerals, defined in `data/transport_fleet.csv` (per-country ship/
rail/truck split, ~85 agents over ~26 countries).

### Tunable knobs (with sensible defaults)

| Knob | Default | Effect |
|------|---------|--------|
| `mine_restart_margin` | 1.2 | A mothballed mine reopens when price > extraction_cost × this. |
| `mine_restart_lag_steps` | 26 (40 Pt) | Steps a mothballed mine takes to actually re-open after the price triggers a restart. The restart aborts if price slips back below the trigger mid-counter. |
| `mine_baseline_utilization` | 0.75 | Anchor: when ratio of price/cost lies on the curve such that the linear interpolation gives this value, output equals the USGS-reported baseline. |
| `mine_min_utilization` / `mine_max_utilization` | 0.5 / 1.0 | Floor and ceiling on per-step output as fraction of nameplate. |
| `mine_max_utilization_ratio` | 2.5 | price/extraction-cost ratio at which utilization saturates at `mine_max_utilization`. |
| `mine_capacity_growth_per_year` | 0.075 Li / 0.04 Ni / 0.01 Pt | Per-year growth applied to each mine's `production_capacity` per step, modelling capacity expansion under sustained demand growth. |
| `reserve_replacement_rate` | 0.70 Li / 0.50 Ni / 0.30 Pt | Fraction of nameplate per-step output added back to reserves each step (exploration replenishment). |
| `post_embargo_release_steps` | 26 | Steps over which a lifted embargo drains the domestic stockpile back into supply. |
| `processor_safety_stock_weeks` | 2.0 | Buffer (in weeks of *output* capacity) below which a processor won't sell. |
| `processor_inventory_cap_weeks` | 8.0 | Ceiling: a processor stops buying ore once expected post-processing inventory would exceed this. |
| `processor_warmstart_safety_multiplier` | 2.0 | Initial processed inventory as a multiple of safety stock (avoids dead pipeline during transport warmup). |
| `manufacturer_target_inventory_weeks` | 4 | Manufacturer's target input buffer in weeks of full-capacity production. |
| `manufacturer_order_rate` | 0.5 | Per-step fraction of the target gap a manufacturer orders. |
| `manufacturer_capacity_headroom` | 1.5 | Aggregate manufacturer capacity vs. baseline product demand. Capacity also scales with the demand-trajectory growth factor. |
| `manufacturer_warmstart_input_fraction` | 0.5 | Initial input inventory as a fraction of target. |
| `retailer_max_pending_orders` | 3 | Max simultaneous outstanding orders per retailer. |
| `price_signal_window_steps` | 8 | Smoothing window for the supply/demand price signal. |
| `price_shortage_ratio` / `price_surplus_ratio` | 0.95 / 1.10 | Bands within which price is held flat. |
| `transport_lead_time_ship` / `_rail` / `_truck` | 7 / 4 / 2 | Mode-specific shipment delays in steps. |
| `consumer_product_base_price` | $40k Li/Ni, $30k Pt | Non-mineral component of finished-product price. Consumer elasticity is applied to (base + intensity × mineral_price), not the bare mineral price -- so a 50 % mineral spike adds <1 % to product price for Li/Ni instead of 28 % demand destruction. |
| `demand_scenario` | `NetZero` | Which scenario column in `data/demand.csv` to interpolate against between the 2024 baseline row and any future-year rows. |

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
- Endogenous, price-responsive capacity expansion (today's growth is a
  fixed CAGR -- a real model would invest under sustained high prices
  and pause under low prices, with multi-year construction lags).
- New-mine onset (greenfield projects coming online over time) rather
  than only growing existing facilities.
- Bilateral trade-flow constraints (today every manufacturer can source
  from every processor; in reality there are long-term contracts).
- Reversible substitution (current substitution is a monotonic
  ratchet; real chemistry choices revert when the substitute's
  feedstock becomes more expensive).
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

**Status**: ✅ Implemented and validated end-to-end. Worldwide
per-facility model with per-country manufacturers/retailers/consumers,
labelled agents, region-pair routing through five maritime chokepoints,
CLI-schedulable embargoes + chokepoint crises, and an IEA-NetZero
demand trajectory wired through to consumer demand and manufacturer
capacity. Mineral mass is conserved end-to-end (mine pithead carry-
forward, retailer warm-start mineral content, recycled supply routed
through real transport with chokepoint exposure, per-recycler capacity
caps). Mines have per-mineral capacity-growth and reserve-replacement
rates, and a multi-step restart lag for mothballed operations.

Canonical 24-year baselines for Li/Ni/Pt and 13 scenario runs are
committed under `outputs/` (embargoes: `chile_li`, `china_li`,
`australia_li`, `chile_china_li`, `big3_li_5yr`, `sa_pt`; chokepoint
closures: `suez_li`, `malacca_li`, `hormuz_li`, `malacca_ni`,
`suez_pt`). Six 26-year combined embargo + chokepoint scenarios are
committed under `outputs/2050/` (`asia_crisis_2030`,
`indonesia_squeeze_2032`, `sa_pt_crisis_2030`, `li_nationalism_2035`,
`multi_crisis_2040`). All outputs are reproducible from
`scripts/regenerate_outputs.py`.
