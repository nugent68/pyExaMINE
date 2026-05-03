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
  cost. Each step also grows `production_capacity` at
  `mine_capacity_growth_per_year / steps_per_year` and replenishes
  reserves at `production_capacity * reserve_replacement_rate`, so a
  multi-decade run actually models capacity build-out and exploration.
  Unsold extracted ore carries forward in a `pithead_stockpile` and
  flows back into next-step supply via `+=` accumulation (mass is
  conserved through the carry). Subject to random disruptions,
  geopolitical events, and embargoes; embargo stockpiles are released
  back into supply **linearly** over `post_embargo_release_steps`
  (frozen chunk size = initial / N, so the stockpile reaches zero in
  exactly N steps) once the embargo lifts.
- **ProcessorAgent** - Converts ore to processed material; receives
  recycled mineral via a dedicated channel that bypasses the conversion
  stage. **Capacity is the CSV's post-conversion output capacity**;
  input throughput is derived as `output_capacity / efficiency`, so an
  82%-yield 40 kt/yr Li plant can feed ~48.8 kt/yr of contained-Li
  input. Ore purchases are sized by **inventory backpressure** alone
  (`processor_inventory_cap_weeks` of expected output inventory caps
  the ordered pipeline). The previous version also imposed a one-step
  throughput cap that double-counted in-transit ore, throttling actual
  processing to ~25-30% of nameplate when ore lead times are 3+ weeks;
  removing it lets the pipeline naturally fill to ~6 weeks of input
  throughput. Capacity grows per step at
  `processor_capacity_growth_per_year / steps_per_year` so refining
  keeps pace with mining capacity build-out under multi-decade demand
  growth.
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
- **RetailerAgent** - Manages inventory with an (s, Q) policy whose `s`
  and `Q` **scale with the demand-trajectory growth factor** (so the
  policy stays correctly sized as demand grows ~10x by 2050). Sources
  goods from manufacturers **region-preferenced**: same-country first,
  then same-region, then global -- mirroring real supply relationships
  and avoiding the long-lead-time fragmentation that otherwise
  collapses fulfillment in high-demand countries. Multi-order pipeline
  (up to `retailer_max_pending_orders` outstanding). Embedded mineral
  content travels with shipped goods to preserve as-built intensity
  through to consumers.
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
- **Market Pricing** - Cost-anchored, flow-driven. Each step the model
  computes the merit-order **marginal cost** (the extraction cost of
  the last operational mine called online to meet demand) and the
  **cheapest active extraction cost**. Price is updated by a
  proportional move (`elasticity × log(supply/demand)`, capped at
  `max_step_pct`) plus a log-linear pull toward marginal cost
  (`anchor_strength × log(marginal_cost / current_price)`). The
  resulting price is bounded by a soft band that *moves with the cost
  curve*: floor = `floor_cost_fraction × cheapest_cost`, ceiling =
  `ceiling_mc_multiple × marginal_cost`. Outer hard
  `price_floor`/`price_ceiling` bounds remain as catastrophe limits
  but normally don't bind. This replaces the older
  ±5%/dead-band rule, which saturated all sustained-shortage
  scenarios at the same speed and pinned multiple distinct scenarios
  to a fixed config ceiling.
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

### Price Dynamics (cost-anchored, flow-driven)
At each step the model computes
- `supply_flow`  = mine output offered to the market + recycled supply
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

Why cost-anchored: a constant ±5% move can't distinguish "tight" from
"crisis" -- both ratchet at the same speed, so multiple distinct
scenarios saturate the configured ceiling and become numerically
identical (e.g. Australia-only and Chile+China embargoes used to
both produce $42,443). The proportional response separates them by
severity, and the cost anchor provides long-run mean reversion.

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

| | Avg price | Volatility | Recycling rate | Substitution | Avg mothballed | Fulfillment |
|---|---:|---:|---:|---:|---:|---:|
| Lithium  | $17,337 / t        | ±$2,624     |  6.2% |  0%   | 0 / 30  | 73.5% |
| Nickel   | $11,412 / t        | ±$2,379     |  5.0% |  0%   | 0 / 29  | 76.5% |
| Platinum | $35,188,109 / t    | ±$7,072,089 | 20.2% | 20%   | 0 / 18  | 66.8% |

Mothballed counts are out of the per-mineral mine total (Li 30, Ni 29,
Pt 18 facilities); zero mothballs across the run is the expected
behavior — under properly-flowing demand the cost-anchored price
keeps every mine above its cash cost. The narrative under NetZero
demand:

- **Lithium** runs near baseline price most of the time. The cost
  anchor keeps the price oscillating around an avg ~1.3× marginal
  cost (a healthy mining-industry margin). Capacity grows ~6× by
  2050 (7.5 %/yr CAGR) but demand grows ~10×; the price drift
  through the 2040s is visible but doesn't trigger substitution
  under these knobs.
- **Nickel** is structurally oversupplied. Indonesian supply growth
  (modelled at 4 %/yr) outpaces NetZero Ni demand (2.0× by 2050),
  pushing price toward the cost-curve floor. Avg price ~$11.4 k/t,
  near the cheapest extraction cost — high-cost Australian / Canadian
  Ni runs at the `mine_min_utilization` floor for long stretches,
  mirroring the real 2024 wave of Australian Ni shutdowns under
  Indonesian oversupply, but with the sustained-pressure mothball
  threshold not quite tripping under these dynamics.
- **Platinum** is constrained. Capacity grows only 1 %/yr while
  NetZero fuel-cell + autocat demand grows 2.4×. Avg price $35.2 M/t
  sustains the 20 % maximum substitution by mid-run as Pt scarcity
  deepens. Volatility ±$7.1 M/t reflects the small producer base
  (only 18 mines) and Pt's lower price elasticity.

Recycling rates emerge in the second half of the run as the EOL
stream from the realistic 10–12 yr product-lifetime delay starts to
land (Pt at 20 % mirrors the high real-world PGM autocatalyst
recovery rate; Li at 6 % and Ni at 5 % are still ramping at the end
of the 24-yr window).

**Fulfillment rate** ≈ 67–77 % across minerals. The previous version
of this README explained low fulfillment as "mineral-supply
constraint on aspirational product demand" — that explanation was
wrong. The earlier ~10–25 % fulfillment was an artefact of the
processor throughput-cap bug (which throttled refining to ~25–30 %
of nameplate when ore lead times exceed 1 step) and a static
retailer (s, Q) policy that didn't scale with demand growth. With
those fixed, fulfillment now reflects realistic friction:
- transport lead times (1–3 wks ship to Asia, 3–6 wks ship to
  Europe, 1 wk truck domestic) leave the retailer pipeline
  partially empty during stockout windows in the (s, Q) cycle;
- random geopolitical events shut producing jurisdictions for 5–15
  steps each, removing supply temporarily;
- per-step demand growth runs ahead of capacity build-out for some
  windows, especially mid-2040s under the NetZero ramp.

The remaining gap to 100 % is mostly the (s, Q) cycle: each retailer
spends ~1 step per cycle stocked out before the next shipment
arrives. Real-world retailers smooth this with vendor-managed
inventory and demand forecasting, neither of which the model
implements.

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
| Baseline (no embargo)         | $18,348 | — |
| China only, 1 yr              | $21,576 | +17.6% |
| Chile only, 1 yr              | $23,086 | +25.8% |
| Australia only, 1 yr          | $26,393 | +43.9% |
| **Chile + China, 1 yr**       | **$26,712** | **+45.6%** |
| **Chile + China + AUS, 5 yr** | **$27,492** | **+49.8%** |

Severity ordering: China alone < Chile alone < Australia alone <
Chile+China ≈ big-3 5 yr. The big-3 5-year embargo is only marginally
larger than Chile+China because once you've removed the cheapest
producers, the price climbs to where high-cost Chinese supply is the
marginal producer — adding Australia on top doesn't change which
mine is at the margin, only how much capacity is required from it.
The 5-year duration also gives substitution and recycling time to
respond, capping the price ceiling.

For Platinum, a 1-year South Africa embargo (~70 % of global Pt
production) lifts the in-window price from ~$30 M/t to ~$47 M/t
(+55 %), reflecting Pt's small producer base and the time required
for the modeled substitution counter to absorb the shock.

| Scenario | In-window avg ($/t) | Δ vs baseline |
|----------|--------------------:|--------------:|
| Pt baseline             | $30,131,764  | — |
| Pt SA embargo, 1 yr     | $46,725,778  | +55.1% |

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
| Li      | (baseline, no crisis)    | $18,268        | — |
| Li      | Suez Canal               | $17,811        | -2.5% |
| Li      | Malacca Strait           | $18,518        | +1.4% |
| Li      | Strait of Hormuz         | $18,084        | -1.0% |
| Ni      | (baseline)               | $11,946        | — |
| Ni      | Malacca Strait           | $11,490        | -3.8% |
| Pt      | (baseline)               | $33,650,546    | — |
| Pt      | Suez Canal               | $34,487,434    | +2.5% |

Short 8-week closures produce small, mostly directionally-correct
impacts now that the supply chain is properly flowing: Malacca Strait
closure on Li (+1.4 %) reflects the lead-time penalty of the Cape
re-route. Hormuz/Li (~−1 %) is essentially noise (Li doesn't transit
the Persian Gulf). Negative deltas on short closures (Li/Suez −2.5 %,
Ni/Malacca −3.8 %) reflect a small RNG-window-positioning floor
combined with extra in-flight inventory absorbing the short-term
price.

Short 8-week closures remain modest in impact — goods are delayed,
not lost, and most major routes have alternates. The 26-week and
52-week 2050 chokepoint scenarios (combined with embargoes) produce
much larger impacts.

Visualization: [`outputs/embargo_comparison.png`](outputs/embargo_comparison.png).

## 2050 combined scenarios (1352 steps, seed 42)

`outputs/2050/` contains six combined embargo + chokepoint scenarios
that fire at different points along the 2024-2050 NetZero ramp. Window
is the longest event in each scenario. Plots:
[`outputs/2050/scenarios_2050.png`](outputs/2050/scenarios_2050.png),
[`outputs/2050/scenario_summary.png`](outputs/2050/scenario_summary.png).

| Mineral | Scenario | In-window avg | Δ vs baseline |
|---------|---------|---------------:|--------------:|
| Li | asia_crisis_2030       | $22,095        | +14.2% |
| Li | li_nationalism_2035    | $28,333        | +51.7% |
| Li | multi_crisis_2040      | $18,133        | +1.0% (Russia/Indo not Li producers) |
| Ni | asia_crisis_2030       | $13,127        | -1.9% (China is processor, not producer) |
| Ni | indonesia_squeeze_2032 | $24,431        | +85.0% |
| Ni | multi_crisis_2040      | $18,735        | +93.9% |
| Pt | asia_crisis_2030       | $36,906,075    | -12.7% (China embargo doesn't bottle Pt) |
| Pt | sa_pt_crisis_2030      | $46,398,714    | +9.8% |
| Pt | multi_crisis_2040      | $31,022,632    | +3.2% |

Indonesia Ni is now the most decisive single-source dependency the
model captures: `indonesia_squeeze_2032` (+85 %) and
`multi_crisis_2040` (+94 %) both push Ni dramatically higher. Lithium's
`li_nationalism_2035` (Chile + Australia 2-year embargoes + Suez
closure) lifts price ~52 % — comparable to the canonical big-3
embargo. Pt scenarios show smaller deltas because (i) Pt baseline is
already running well above marginal cost under NetZero demand growth,
and (ii) the substitution counter is already pinned at the 20 % cap
from baseline, leaving less headroom for further price escalation.
The negative deltas (Pt asia_crisis_2030 −12.7 %, Ni asia_crisis_2030
−1.9 %) reflect that China is a processor / consumer rather than
producer for those minerals — embargoing China removes *demand* for
processed material more than it removes supply, depressing the
in-window price.

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
| `mine_restart_lag_steps` | 26 (40 Pt) | Cold-restart delay: steps a mothballed mine takes to re-open if it has been offline for longer than `mine_warm_restart_window_steps`. Aborts if price slips back below the trigger mid-counter. |
| `mine_warm_restart_lag_steps` | 12 (26 Pt) | Warm-restart delay if the mine was mothballed within `mine_warm_restart_window_steps`. Equipment + crews still in place; restart is faster. |
| `mine_warm_restart_window_steps` | 52 (78 Pt) | How recently a mine had to be mothballed to qualify for the warm-restart lag. |
| `mothball_trigger_steps` | 52 | Number of consecutive (net) below-cash-cost steps a mine accumulates before mothballing. Sticky counter: decrements on price recovery. |
| `mine_cash_cost_fraction` | 0.65 (0.60 Pt) | Cash cost = `extraction_cost × this`. The mothball trigger only fires below cash cost (not below extraction cost), reflecting that mines stay open at reduced rates above cash cost. |
| `mine_baseline_utilization` | 0.75 | Anchor: when ratio of price/cost lies on the curve such that the linear interpolation gives this value, output equals the USGS-reported baseline. |
| `mine_min_utilization` / `mine_max_utilization` | 0.5 / 1.0 | Floor and ceiling on per-step output as fraction of nameplate. |
| `mine_max_utilization_ratio` | 2.5 | price/extraction-cost ratio at which utilization saturates at `mine_max_utilization`. |
| `mine_capacity_growth_per_year` | 0.075 Li / 0.04 Ni / 0.01 Pt | Per-year growth applied to each mine's `production_capacity` per step, modelling capacity expansion under sustained demand growth. |
| `reserve_replacement_rate` | 0.70 Li / 0.50 Ni / 0.30 Pt | Fraction of nameplate per-step output added back to reserves each step (exploration replenishment). |
| `post_embargo_release_steps` | 26 | Steps over which a lifted embargo drains the domestic stockpile back into supply. |
| `processor_safety_stock_weeks` | 2.0 | Buffer (in weeks of *output* capacity) below which a processor won't sell. |
| `processor_inventory_cap_weeks` | 8.0 | Ceiling: a processor stops buying ore once expected post-processing inventory (current + (raw_ore + in_transit) × efficiency) would exceed this. Sole constraint on ore purchasing — the in-flight pipeline naturally fills to roughly (cap − safety) weeks of input throughput. |
| `processor_warmstart_safety_multiplier` | 2.0 | Initial processed inventory as a multiple of safety stock (avoids dead pipeline during transport warmup). |
| `processor_capacity_growth_per_year` | 0.075 Li / 0.04 Ni / 0.01 Pt | Per-year growth applied to each processor's `output_capacity` per step. Mirrors the mining capacity-expansion knob so refining doesn't bottleneck multi-decade demand growth. |
| `manufacturer_target_inventory_weeks` | 4 | Manufacturer's target input buffer in weeks of full-capacity production. |
| `manufacturer_order_rate` | 0.5 | Per-step fraction of the target gap a manufacturer orders. |
| `manufacturer_capacity_headroom` | 1.5 | Aggregate manufacturer capacity vs. baseline product demand. Capacity also scales with the demand-trajectory growth factor. |
| `manufacturer_warmstart_input_fraction` | 0.5 | Initial input inventory as a fraction of target. |
| `retailer_reorder_point_multiplier` | 4.0 (5.0 Pt) | Reorder point in *weeks of current per-step demand* (scales with `demand_growth_factor`). Default covers a 3-week ship lead time + 1 week safety stock; Pt uses 5 weeks for the higher transport-security buffer. |
| `retailer_order_quantity_multiplier` | 3.0 (3.5 Pt) | Order quantity in weeks of current per-step demand (also scales with growth). |
| `retailer_max_pending_orders` | 3 | Max simultaneous outstanding shipments per retailer. |
| `price_signal_window_steps` | 8 | Smoothing window for the supply/demand price signal. |
| `price_elasticity` | 0.25 | Per-step price move per unit of `log(supply/demand)`. A 30 % shortage moves the price ~7 %/step (capped at `price_max_step_pct`). |
| `price_max_step_pct` | 0.08 | Hard cap on per-step move magnitude. |
| `price_anchor_strength` | 0.10 | Per-step log-pull toward marginal cost. 0.10 closes ~10 % of the log-gap each step. |
| `price_ceiling_mc_multiple` | 8.0 | Soft ceiling = N × marginal cost. Lets a true crisis show as a level rather than saturating a fixed config ceiling. |
| `price_floor_cost_fraction` | 0.6 | Soft floor = f × cheapest-active extraction cost. Lets price dip briefly below cash-cost but not arbitrarily low. |
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
demand trajectory wired through to consumer demand and manufacturer +
processor capacity. The price model is **cost-anchored**: a
proportional log-linear move plus a pull toward merit-order marginal
cost, bounded by a soft band that scales with the live cost curve;
total supply collapse pushes the price upward at the per-step cap
instead of flatlining the imbalance signal. Mineral mass is conserved
end-to-end (mine pithead carry-forward via accumulating `+=`, retailer
warm-start mineral content, recycled supply routed through real
transport with chokepoint exposure, per-recycler capacity caps,
linear post-embargo stockpile bleed). Mines have per-mineral
capacity-growth and reserve-replacement rates, and a
**sustained-pressure mothball** decision (52 weeks of price below
cash cost) with **warm/cold restart lags** to match the historical
timing of real-world mothball decisions. Refining capacity grows in
lock-step with mines via a symmetric `processor_capacity_growth_per_year`
knob.

Canonical 24-year baselines for Li/Ni/Pt and 13 scenario runs are
committed under `outputs/` (embargoes: `chile_li`, `china_li`,
`australia_li`, `chile_china_li`, `big3_li_5yr`, `sa_pt`; chokepoint
closures: `suez_li`, `malacca_li`, `hormuz_li`, `malacca_ni`,
`suez_pt`). Six 26-year combined embargo + chokepoint scenarios are
committed under `outputs/2050/` (`asia_crisis_2030`,
`indonesia_squeeze_2032`, `sa_pt_crisis_2030`, `li_nationalism_2035`,
`multi_crisis_2040`). All outputs are reproducible from
`scripts/regenerate_outputs.py`.
