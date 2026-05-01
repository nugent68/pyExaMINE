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
- **MineAgent** - Extracts raw minerals, subject to disruptions and embargoes
- **ProcessorAgent** - Converts ore to processed material
- **TransportAgent** - Moves materials with realistic delays
- **ManufacturerAgent** - Produces goods, invests in substitution
- **RetailerAgent** - Manages inventory with (s,Q) policy
- **ConsumerAgent** - Generates price-sensitive demand
- **RecyclingAgent** - Recovers minerals from end-of-life products

### 📊 Dynamic Mechanisms
- **Market Pricing** - Flow-based signal: smoothed (supply / demand) ratio over
  a rolling window (default 8 steps) drives ±5% price moves; ratio < 0.95 →
  shortage → price up, ratio > 1.10 → surplus → price down.
- **Random Geopolitical Events** - Stochastic shutdowns of jurisdictions
  (default 1% probability per step, 5–15 steps duration).
- **Scheduled Political Embargoes** - A country can withhold its mine output
  from the international market starting on a chosen step for a fixed
  duration. Mines keep extracting; production accumulates in a domestic
  stockpile rather than reaching foreign processors.
- **Material Substitution** - Manufacturers reduce mineral intensity under
  sustained high prices.
- **Circular Economy** - Recycling loop with a 25-step product-lifetime lag.

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
├── requirements.txt                   # Python dependencies
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
environment. `uv run` executes the simulation in the project's `.venv/`
without requiring you to activate it manually.

```bash
# Clone the repository
git clone https://github.com/yourusername/pyExaMINE.git
cd pyExaMINE

# Install uv (macOS)
brew install uv
# or, on any platform:
# curl -LsSf https://astral.sh/uv/install.sh | sh

# Create the project environment and install dependencies
uv venv
uv pip install -r requirements.txt
```

See [INSTALL.md](INSTALL.md) for the legacy `python -m venv venv` path
if you cannot use uv.

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
- **Trigger**: Price above threshold for 10+ consecutive steps
- **Effect**: Manufacturers reduce `mineral_intensity` by 5% per cycle
- **Max reduction**: 30% (Li/Ni), 20% (Pt)
- **Irreversible**: Once invested, intensity stays reduced

### Recycling Loop
- **Product lifetime**: 25 steps
- **Collection rate**: 30–75% of EOL products (mineral-specific)
- **Recovery efficiency**: 70–85% of collected material
- **Profitability gate**: Only process if market price > processing cost

## Validation (canonical 2050 baseline, seed 42)

| | Avg price | Avg recycling | Substitution | Reserves drawdown over 24 yr |
|---|---|---|---|---|
| Lithium  | $17,431/t   | 13% | 0%  | ≈ 9%  |
| Nickel   | $15,999/t   | 23% | 5%  | ≈ 60% |
| Platinum | $29,420,000/t | 34% | 6%  | ≈ 11% |

- Prices cluster around their initial values (no ceiling/floor pinning).
- Recycling contributions track the configured collection × recovery rates.
- Reserves drawdowns match supply/demand expectations through 2050.

## Example: political-embargo response (Lithium)

| Scenario | Avg price during embargo | Recovery |
|---|---|---|
| Baseline (no embargo) | $17,431/t | — |
| Chile + China, 1 year | $30,718/t (+85%) | back to ~$19K within ~1 yr |
| Chile + China + Australia, 5 years | ~$31,000/t sustained | back to ~$19.5K within ~1 yr |

Visualization: [`outputs/embargo_comparison.png`](outputs/embargo_comparison.png).

## Documentation

- **[Architecture Plan](plans/architecture_plan.md)** - Detailed technical design
- **[Quick Reference](plans/quick_reference.md)** - Lookup guide for agents and parameters
- **[Implementation Roadmap](plans/implementation_roadmap.md)** - Build sequence and timeline

## Model Parameters by Mineral

| Parameter | Lithium | Nickel | Platinum |
|-----------|---------|--------|----------|
| Initial Price ($/ton) | 17,000 | 18,000 | 30,000,000 |
| Ore Grade | 0.85 | 0.65 | 0.55 |
| Conversion Efficiency | 0.80 | 0.75 | 0.70 |
| Collection Rate | 0.30 | 0.60 | 0.75 |
| Recovery Efficiency | 0.70 | 0.75 | 0.85 |

## Contributing

Contributions are welcome! Areas for extension:
- Additional minerals (cobalt, copper, rare earths) — extend `USGS_CMM.csv`
  with new `<Mineral>_Production_2024[t/yr]` and `<Mineral>_Reserves[t]`
  columns and add a `<mineral>_config.py` to `src/config/`.
- Smarter post-embargo behavior (release stockpiles into the market over time
  rather than holding them as strategic reserve).
- More sophisticated transport networks (origin-destination routing).
- Trade-policy scenarios (tariffs, export quotas) building on the embargo
  primitive.
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

**Status**: ✅ Implemented and validated end-to-end. Lithium, Nickel, and
Platinum baselines run cleanly through 2050; political embargoes, random
geopolitical disruptions, recycling, and material substitution all
exercised in scenario tests under `outputs/`.
