# Installation and Setup Guide

pyExaMINE uses [uv](https://docs.astral.sh/uv/) to manage its Python
environment. `uv` resolves and installs dependencies into a project-local
`.venv/`, and `uv run` lets you execute the simulation without manually
activating anything.

## Quick Start

### 1. Install uv

**macOS (Homebrew):**
```bash
brew install uv
```

**Other platforms** (one-line installer):
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Create the project environment

```bash
cd pyExaMINE
uv venv
uv pip install -r requirements.txt
```

`uv venv` creates `.venv/` in the project root (using a Python that uv
selects automatically). `uv pip install -r requirements.txt` populates it.

### 3. Run a simulation

```bash
# Single mineral
uv run python run_simulation.py --mineral lithium --steps 200

# All three
uv run python run_simulation.py --all --steps 200 --seed 42
```

`uv run` finds `.venv/` automatically — you do not need to
`source .venv/bin/activate` first.

## Prerequisites

- macOS, Linux, or Windows
- `uv` (installs and manages Python interpreters and dependencies)
- Git (optional, for cloning)

`uv` installs an appropriate Python interpreter on demand if you don't
already have one; you do not need to install Python separately.

## Verify installation

```bash
uv run python -c "import mesa; print(f'Mesa version: {mesa.__version__}')"
```

Expected output: `Mesa version: 2.4.x` (or any 2.x release).

## Command-line options

```
--mineral {lithium,nickel,platinum}  # Which mineral to simulate
--all                                # Run all three minerals
--steps N                            # Number of simulation steps (default: 200)
--geo-prob P                         # Geopolitical event probability (default: 0.01)
--seed N                             # Random seed for reproducibility
--output-dir DIR                     # Output directory (default: outputs/)
--no-viz                             # Skip visualization generation (faster)
```

## Examples

```bash
# Quick smoke test (no images)
uv run python run_simulation.py --mineral lithium --steps 50 --no-viz

# Long simulation with elevated geopolitical risk
uv run python run_simulation.py --mineral platinum --steps 500 --geo-prob 0.03

# Reproducible run with explicit seed
uv run python run_simulation.py --mineral nickel --steps 200 --seed 42

# Custom output directory
uv run python run_simulation.py --all --steps 300 --output-dir results/scenario1/
```

## Output files

Each simulation generates three files per mineral in the output directory:

1. **`{mineral}_supply_chain_analysis.png`** — 6-panel dashboard
   (price, inventory, supply, demand, disruptions, substitution).
2. **`{mineral}_model_data.csv`** — per-step time series of all metrics.
3. **`{mineral}_summary_stats.txt`** — averages, fulfillment rate,
   recycling rate, intensity reduction, etc.

## Updating dependencies

```bash
# Refresh against requirements.txt
uv pip install -r requirements.txt

# Or upgrade a single package
uv pip install --upgrade mesa
```

To regenerate `requirements.txt` from the current environment:
```bash
uv pip freeze > requirements.txt
```

## Troubleshooting

**`No module named 'mesa'`**
You ran `python` directly instead of `uv run python`, or `.venv/` is
missing. Run `uv pip install -r requirements.txt` and use `uv run`.

**Visualization backend errors**
Install a Qt backend or run with `--no-viz`:
```bash
uv pip install pyqt5
```

**Out of memory on long runs**
Reduce `--steps` or the agent counts in `src/config/{mineral}_config.py`.

**Simulation runs but produces no output**
Confirm `USGS_CMM.csv` is in the project root.

## Optional: legacy venv flow

If you cannot use `uv` (e.g., locked-down environment) the project still
works with stock Python:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python run_simulation.py --all --steps 200
```

You then need `source venv/bin/activate` in every shell that runs the
simulation; `uv run` exists specifically to remove that step.

## Development setup

```bash
uv pip install pytest black flake8

uv run pytest tests/    # if tests exist
uv run black src/
uv run flake8 src/
```

## Docker alternative (advanced)

```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN pip install uv
COPY requirements.txt .
RUN uv pip install --system -r requirements.txt
COPY . .
CMD ["python", "run_simulation.py", "--all", "--steps", "200"]
```

```bash
docker build -t pyexamine .
docker run -v $(pwd)/outputs:/app/outputs pyexamine
```

## Next steps

1. Read the [Architecture Plan](plans/architecture_plan.md) for technical details.
2. See the [Quick Reference](plans/quick_reference.md) for agent behaviors.
3. Modify config files in `src/config/` to test different scenarios.
4. Extend the model with additional features.

## Getting help

- Overview: [README.md](README.md)
- Roadmap: [plans/implementation_roadmap.md](plans/implementation_roadmap.md)
- Issues: GitHub issue tracker

---

**Python:** managed by uv (3.10+ recommended)
**Mesa:** 2.4+
