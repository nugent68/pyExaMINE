# Installation and Setup Guide

pyExaMINE uses [uv](https://docs.astral.sh/uv/) to manage its Python
environment. Dependencies are declared in `pyproject.toml` and pinned
in the committed `uv.lock`, so `uv sync` reproduces the exact same
dependency graph on every machine. `uv run` lets you execute the
simulation without manually activating anything.

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

### 2. Sync the project environment

```bash
cd pyExaMINE
uv sync
```

`uv sync` reads `pyproject.toml` and `uv.lock`, creates `.venv/` in the
project root (using a Python that uv selects automatically), and
installs the locked dependency set. Re-running it is idempotent and
fast.

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
--embargo SPEC                       # Schedule a political embargo (repeatable)
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
# Pull the latest versions allowed by pyproject.toml constraints
# and rewrite uv.lock accordingly:
uv lock --upgrade

# Add a new dependency (also writes to pyproject.toml + uv.lock):
uv add some-package

# Remove one:
uv remove some-package

# Reinstall exactly what uv.lock specifies (no resolution, no upgrade):
uv sync
```

After upgrading, regenerate the legacy `requirements.txt` mirror so the
non-uv flow stays in step:

```bash
uv export --no-hashes --format requirements.txt > requirements.txt
```

## Troubleshooting

**`No module named 'mesa'`**
You ran `python` directly instead of `uv run python`, or `.venv/` is
missing. Run `uv sync` and use `uv run`.

**Visualization backend errors**
Install a Qt backend or run with `--no-viz`:
```bash
uv add pyqt5
```

**Out of memory on long runs**
Reduce `--steps` or the agent counts in `src/config/{mineral}_config.py`.

**Simulation runs but produces no output**
Confirm `USGS_CMM.csv` is in the project root.

## Optional: legacy venv flow

If you cannot use `uv` (e.g., locked-down environment) the project still
works with stock Python and a regular `requirements.txt`:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python run_simulation.py --all --steps 200
```

You then need `source venv/bin/activate` in every shell that runs the
simulation; `uv run` exists specifically to remove that step. Note
that `requirements.txt` is a *mirror* of the canonical `pyproject.toml`
declarations — if you want pinned versions identical to the lock,
prefer the uv flow.

## Development setup

```bash
# Add dev tools to the project (writes to pyproject.toml under
# [dependency-groups]):
uv add --group dev pytest black flake8

uv run pytest tests/    # if tests exist
uv run black src/
uv run flake8 src/
```

## Docker alternative (advanced)

```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN pip install uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .
CMD ["uv", "run", "python", "run_simulation.py", "--all", "--steps", "200"]
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

**Python:** managed by uv (3.10+ required)
**Mesa:** 2.4+
