# pyExaMINE

**Python ExaScale Minerals & Infrastructure Network Evaluation**

An agent-based model (ABM) for simulating critical minerals supply chains using
real-world USGS data. Models the complete lifecycle from mining through
processing, manufacturing, consumption, and recycling with dynamic pricing,
random geopolitical disruptions, scheduled political embargoes, and material
substitution.

## Overview

This project models three critical minerals essential for clean energy
transitions:
- **Lithium** - Battery technology
- **Nickel** - Battery cathodes and stainless steel
- **Platinum** - Catalytic converters and fuel cells

The bundled `USGS_CMM.csv` carries hand-curated 2024-baseline production,
reserves, and demand-forecast (2030 / 2050 NetZero) values aligned with USGS
Mineral Commodity Summaries 2025 and IEA Critical Minerals Outlook estimates.
Country-level coverage focuses on the relevant producers for each mineral
(19 rows; full list visible in the file).

## Key Features

### 🌍 Data-Driven
- Country-level production, reserves, and forecast demand for Lithium, Nickel,
  and Platinum, calibrated to USGS / IEA estimates.
- **Self-describing units**: column headers carry a `[unit]` suffix
  (`Lithium_Production_2024[t/yr]`, `Platinum_Reserves[t]`, etc.); the loader
  parses the suffix and rescales to tonnes / tonnes-per-year so the in-memory
  representation is uniform regardless of source units.
- Calibrated extraction costs and ore grades per country.

### 🤖 7 Agent Types
- **MineAgent** - Extracts raw minerals; mothballs when price < extraction
  cost and reopens above `extraction_cost * mine_restart_margin`. **Output
  flexes with price** via a configurable utilization factor (linear ramp
  between `mine_min_utilization` and `mine_max_utilization` over the
  price/extraction-cost ratio range), rescaled so USGS baseline output
  is preserved at the anchored "normal" multiple of cost. Subject to
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
  claiming a fair share of each step's initial EOL bucket.

### 📊 Dynamic Mechanisms
- **Tier-Ordered Scheduling** - Within each step, agents are activated in
  supply-chain order: mines → recyclers → processors → manufacturers →
  retailers → consumers → transport (last so shipments accepted earlier
  in the step queue with the full lead time, not zero). Within each tier
  the activation order is shuffled (using the seeded RNG) for fairness.
- **Real Transport Pipeline** - Ore and processed mineral move through
  TransportAgent queues with mode-specific lead times before landing in
  the receiving agent's inventory. This means a shock at a mine takes
  the ship lead time to propagate to processors, and recovery from an
  embargo lift drains over the configured release window — not instantly.
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
├── USGS_CMM.csv                       # Source data (Li/Ni/Pt, unit-tagged headers)
├── pyproject.toml                     # Project metadata + dependency declarations (uv)
├── uv.lock                            # Pinned dependency graph (committed)
├── requirements.txt                   # Legacy mirror for non-uv users
├── plans/                             # Architecture documentation
│   ├── architecture_plan.md
│   ├── quick_reference.md
│   └── implementation_roadmap.md
├── src/                               # Source code
│   ├── agents/                        # Agent implementations
│   │   ├── mine_agent.py
│   │   ├── processor_agent.py
│   │   ├── transport_agent.py
│   │   ├── manufacturer_agent.py
│   │   ├── retailer_agent.py
│   │   ├── consumer_agent.py
│   │   └── recycling_agent.py
│   ├── model/                         # Main model logic
│   │   ├── supply_chain_model.py
│   │   └── market_mechanism.py       # Flow-based price update
│   ├── data/
│   │   └── data_loader.py             # Parses [unit] headers
│   ├── visualization/
│   │   └── visualizer.py
│   └── config/                        # Mineral-specific configs
│       ├── lithium_config.py
│       ├── nickel_config.py
│       └── platinum_config.py
├── outputs/                           # Generated results
│   ├── lithium_supply_chain_analysis.png
│   ├── lithium_model_data.csv
│   ├── lithium_summary_stats.txt
│   ├── nickel_*, platinum_*           # canonical 2050 baselines
│   ├── embargo_comparison.png         # multi-scenario chart
│   └── baseline/, chile_china_li/, big3_li_5yr/, …  # scenario subdirs
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

The country name must match the `Country` column in `USGS_CMM.csv`
(case-sensitive). Embargo events are logged when they fire and lift.
You can also schedule embargoes in `src/config/{mineral}_config.py` via:

```python
"political_embargoes": [
    {"country": "Chile",     "start_step": 624, "duration": 52},
    {"country": "China",     "start_step": 624, "duration": 52},
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

24 weekly years of simulation per mineral, run on the model with real
transport lead times, price-responsive mine utilization, processor
inventory backpressure, embargo stockpile release, as-built intensity
tracking through the chain, and corrected mineral intensities (Li
8 kg/EV, Pt 3 g/catalyst). Numbers regenerated from scratch and
committed under `outputs/`.

| | Avg price | Volatility | Recycling rate | Substitution | Avg mothballed |
|---|---:|---:|---:|---:|---:|
| Lithium  | $15,272 / t       | ±$2,193      |  2.0% | 0%    | 0.99 / 10 |
| Nickel   | $18,763 / t       | ±$3,446      |  3.7% | 5%    | 0.02 / 12 |
| Platinum | $31,655,663 / t   | ±$10,774,916 |  3.4% | 18%   | 0.00 / 6  |

Recycling rates are intentionally low because the realistic 10–12 yr
product lifetime means EOL stream only emerges in the second half of
the 24-year run, and the early years' product flow was relatively
small. (Compare to the per-mineral EOL recovery cap
`collection_rate × recovery_efficiency` = 21% / 45% / 64%: those caps
will be approached only after 1–2 product-lifetime cycles.) Platinum
sees substantial 18% substitution because its high price volatility
repeatedly trips the sticky high-price counter; Nickel just barely
trips one substitution cycle (5%); Lithium stays at baseline intensity.

A note on **fulfillment rate** in the per-mineral summary stats: with
realistic intensities, total *product* demand at the consumers (e.g.
~19 M EV-equivalents/year for Li at 8 kg per EV) exceeds what current
mineral supply can support (~150 kt Li/yr ≈ 1.9 M EVs/yr). The
fulfillment rate (~17%) therefore reflects the real-world *mineral
supply constraint* on aspirational product demand, not a model bug.
The mineral-tonnage flow itself stays balanced (mine output ≈ mineral
demand at the manufacturer).

## Political-embargo scenarios (Lithium, seed 42)

`--embargo` flags re-route affected mine output into
`MineAgent.domestic_stockpile` for the configured duration. The price
signal reflects the loss because `supply_flow` excludes embargoed
production. When the embargo lifts, the stockpile drains back into
supply over `post_embargo_release_steps` (default 26 weeks) so the
post-shock recovery is gradual rather than instant. All scenarios below
run for 1248 steps with the embargo firing at step 624.

| Scenario | In-window avg ($/t) | Δ vs baseline |
|----------|--------------------:|--------------:|
| Baseline (no embargo)         | $14,623 | — |
| Chile only, 1 yr              | $16,603 | +13.5% |
| China only, 1 yr              | $16,707 | +14.3% |
| **Australia only, 1 yr**      | **$17,793** | **+21.7%** |
| Chile + China, 1 yr           | $19,652 | +34.4% |
| **Chile + China + AUS, 5 yr** | **$26,939** | **+82.4%** |

Single-country 1-year embargoes do not trigger substitution (price
spike isn't sustained long enough to cross the 10-step counter even
with the new wider price swings). The 5-year big-3 embargo accumulates
pressure over the full window and pushes manufacturers to the **30%
maximum substitution**. Embargoes now show larger price deltas than in
prior runs because mine utilization can no longer ramp freely above
nameplate to compensate — non-embargoed mines are already near their
`mine_max_utilization` ceiling when prices spike, so the supply
shortfall translates more directly into price.

For Platinum, a 1-year South Africa embargo (~72% of global Pt
production) **triples** the in-window price ($23.9 M → $73.4 M,
+207.4%) and triggers the 20% substitution cap.

| Scenario | In-window avg ($/t) | Δ vs baseline |
|----------|--------------------:|--------------:|
| Pt baseline             | $23,888,839 | — |
| **Pt SA embargo, 1 yr** | **$73,431,303** | **+207.4%** |

Visualization: [`outputs/embargo_comparison.png`](outputs/embargo_comparison.png).

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
| Ore Grade (metadata)  | 0.85 | 0.65 | 0.55 |
| Conversion Efficiency | 0.80 | 0.75 | 0.70 |
| Collection Rate | 0.30 | 0.60 | 0.75 |
| Recovery Efficiency | 0.70 | 0.75 | 0.85 |

### Tunable knobs (with sensible defaults)

| Knob | Default | Effect |
|------|---------|--------|
| `mine_restart_margin` | 1.2 | A mothballed mine reopens when price > extraction_cost × this. |
| `mine_baseline_utilization` | 0.75 | Anchor: when ratio of price/cost lies on the curve such that the linear interpolation gives this value, output equals the USGS-reported baseline. |
| `mine_min_utilization` / `mine_max_utilization` | 0.5 / 1.0 | Floor and ceiling on per-step output as fraction of nameplate. |
| `mine_max_utilization_ratio` | 2.5 | price/extraction-cost ratio at which utilization saturates at `mine_max_utilization`. |
| `post_embargo_release_steps` | 26 | Steps over which a lifted embargo drains the domestic stockpile back into supply. |
| `processor_safety_stock_weeks` | 2.0 | Buffer (in weeks of *output* capacity) below which a processor won't sell. |
| `processor_inventory_cap_weeks` | 8.0 | Ceiling: a processor stops buying ore once expected post-processing inventory would exceed this. |
| `processor_warmstart_safety_multiplier` | 2.0 | Initial processed inventory as a multiple of safety stock (avoids dead pipeline during transport warmup). |
| `manufacturer_target_inventory_weeks` | 4 | Manufacturer's target input buffer in weeks of full-capacity production. |
| `manufacturer_order_rate` | 0.5 | Per-step fraction of the target gap a manufacturer orders. |
| `manufacturer_capacity_headroom` | 1.5 | Aggregate manufacturer capacity vs. baseline product demand. |
| `manufacturer_warmstart_input_fraction` | 0.5 | Initial input inventory as a fraction of target. |
| `retailer_max_pending_orders` | 3 | Max simultaneous outstanding orders per retailer. |
| `price_signal_window_steps` | 8 | Smoothing window for the supply/demand price signal. |
| `price_shortage_ratio` / `price_surplus_ratio` | 0.95 / 1.10 | Bands within which price is held flat. |
| `transport_lead_time_ship` / `_rail` / `_truck` | 7 / 4 / 2 | Mode-specific shipment delays in steps. |

## Contributing

Contributions are welcome! Areas for extension:
- Additional minerals (cobalt, copper, rare earths) — extend `USGS_CMM.csv`
  with new `<Mineral>_Production_2024[t/yr]` and `<Mineral>_Reserves[t]`
  columns and add a `<mineral>_config.py` to `src/config/`.
- Per-jurisdiction transport routing (currently mode-only; could pick
  routes by origin–destination pair, with per-route lead times and costs).
- Per-jurisdiction risk weights for random geopolitical events (currently
  uniform across producing countries).
- Trade-policy scenarios (tariffs, export quotas, friend-shoring) building
  on the embargo primitive.
- Heterogeneous consumers (price sensitivity drawn from a distribution
  rather than identical agents).
- Capacity expansion: new mines coming online over time, or existing mines
  expanding nameplate capacity in response to sustained price pressure.
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

**Status**: ✅ Implemented and validated end-to-end. Lithium, Nickel,
and Platinum 24-year baselines run cleanly under the model with real
transport lead times, price-responsive mine utilization, processor
inventory backpressure, embargo stockpile release, as-built intensity
tracking through the chain, and corrected mineral intensities;
canonical CSV / dashboard outputs are committed under `outputs/`.
Political embargoes, random geopolitical disruptions, recycling, and
material substitution are all exercised in scenario tests
(`outputs/{baseline,chile_li,china_li,australia_li,chile_china_li,big3_li_5yr,sa_pt}/`).
