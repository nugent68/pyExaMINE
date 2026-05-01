# Installation and Setup Guide

## Quick Start

### 1. Create Virtual Environment

```bash
# Navigate to project directory
cd pyExaMINE

# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate  # On macOS/Linux
# OR
venv\Scripts\activate  # On Windows
```

### 2. Install Dependencies

```bash
# Install required packages
pip install -r requirements.txt
```

### 3. Run a Simulation

```bash
# Run Lithium simulation (200 steps, default)
python3 run_simulation.py --mineral lithium --steps 200

# Run all three minerals
python3 run_simulation.py --all --steps 200

# Run with custom parameters
python3 run_simulation.py --mineral nickel --steps 300 --geo-prob 0.02 --seed 123
```

## Installation Steps (Detailed)

### Prerequisites

- Python 3.9 or higher
- pip (Python package manager)
- Git (optional, for cloning)

### Step-by-Step Setup

1. **Clone or Download the Repository**
   ```bash
   git clone https://github.com/yourusername/pyExaMINE.git
   cd pyExaMINE
   ```

2. **Create Virtual Environment**
   ```bash
   python3 -m venv venv
   ```
   
   This creates an isolated Python environment in the `venv/` directory.

3. **Activate Virtual Environment**
   
   **On macOS/Linux:**
   ```bash
   source venv/bin/activate
   ```
   
   **On Windows:**
   ```bash
   venv\Scripts\activate
   ```
   
   You should see `(venv)` appear in your terminal prompt.

4. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```
   
   This installs:
   - Mesa 2.x (agent-based modeling framework)
   - pandas (data manipulation)
   - numpy (numerical computing)
   - matplotlib (visualization)
   - seaborn (statistical visualization)

5. **Verify Installation**
   ```bash
   python3 -c "import mesa; print(f'Mesa version: {mesa.__version__}')"
   ```
   
   Should output: `Mesa version: 2.x.x`

### Troubleshooting

**Error: "No module named 'mesa'"**
- Make sure your virtual environment is activated
- Run `pip install -r requirements.txt` again

**Error: "externally-managed-environment"**
- You need to use a virtual environment (see step 2 above)
- Never install with `--break-system-packages` unless you know what you're doing

**Error: "Command 'python' not found"**
- Use `python3` instead of `python`
- On some systems, Python 3 is accessed via `python3`

## Running Simulations

### Basic Usage

```bash
# Activate venv first!
source venv/bin/activate

# Run single mineral
python3 run_simulation.py --mineral lithium --steps 200

# Run all minerals
python3 run_simulation.py --all --steps 200
```

### Command-Line Options

```
--mineral {lithium,nickel,platinum}  # Which mineral to simulate
--all                                # Run all three minerals
--steps N                            # Number of simulation steps (default: 200)
--geo-prob P                         # Geopolitical event probability (default: 0.01)
--seed N                             # Random seed for reproducibility
--output-dir DIR                     # Output directory (default: outputs/)
--no-viz                             # Skip visualization generation (faster)
```

### Examples

```bash
# Quick test run (10 steps, no visualization)
python3 run_simulation.py --mineral lithium --steps 10 --no-viz

# Long simulation with high disruption risk
python3 run_simulation.py --mineral platinum --steps 500 --geo-prob 0.03

# Reproducible run with specific seed
python3 run_simulation.py --mineral nickel --steps 200 --seed 42

# Custom output directory
python3 run_simulation.py --all --steps 300 --output-dir results/scenario1/
```

## Output Files

Each simulation generates three files in the output directory:

1. **`{mineral}_supply_chain_analysis.png`**
   - 6-panel visualization dashboard
   - Shows price, inventory, supply, demand, disruptions, substitution

2. **`{mineral}_model_data.csv`**
   - Time-series data for all metrics
   - Can be used for custom analysis

3. **`{mineral}_summary_stats.txt`**
   - Key statistics summary
   - Average price, fulfillment rate, recycling rate, etc.

## Development Setup

If you want to modify the code:

```bash
# Install development dependencies
pip install pytest black flake8

# Run tests (if available)
pytest tests/

# Format code
black src/

# Check code style
flake8 src/
```

## Deactivating Virtual Environment

When you're done:

```bash
deactivate
```

## Updating Dependencies

To update packages:

```bash
pip install --upgrade mesa pandas numpy matplotlib seaborn
```

To regenerate requirements.txt:

```bash
pip freeze > requirements.txt
```

## Docker Alternative (Advanced)

If you prefer Docker:

```dockerfile
# Create Dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python3", "run_simulation.py", "--all", "--steps", "200"]
```

```bash
# Build and run
docker build -t pyexamine .
docker run -v $(pwd)/outputs:/app/outputs pyexamine
```

## Common Issues

### Issue: Simulation runs but produces no output
- Check that USGS_CMM.csv is in the root directory
- Look for error messages in console output

### Issue: Visualization fails
- Install matplotlib backend: `pip install pyqt5`
- Or run with `--no-viz` and view saved PNG files

### Issue: Out of memory
- Reduce `--steps` parameter
- Reduce number of agents in config files

## Next Steps

1. Read the [Architecture Plan](plans/architecture_plan.md) for technical details
2. See [Quick Reference](plans/quick_reference.md) for agent behaviors
3. Modify config files to test different scenarios
4. Extend the model with additional features

## Getting Help

- Check the [README.md](README.md) for overview
- Review [Implementation Roadmap](plans/implementation_roadmap.md)
- Open an issue on GitHub
- Contact: your.email@example.com

---

**Last Updated**: 2026-05-01  
**Python Version**: 3.9+  
**Mesa Version**: 2.0+
