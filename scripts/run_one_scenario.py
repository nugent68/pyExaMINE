#!/usr/bin/env python3
"""Run one pyExaMINE scenario from a JSON description.

This is the Slurm-array workhorse for surrogate-training data generation.
Each Slurm task picks one scenario out of a per-mineral JSON file and
runs the full simulation, writing a single CSV under ``--output-root``.

Usage:
    python scripts/run_one_scenario.py \
        --scenarios scenarios/lithium.json \
        --index 17 \
        --output-root /global/cfs/projectdirs/amsc001/www/surrogate_data

Output layout (one CSV per scenario):
    <output-root>/<mineral>/<index>.csv

The CSV is the full ``model_data`` time series; targets/features get
extracted later by the training pipeline. We deliberately avoid writing
PNGs / summary text files here -- those add I/O overhead and the
surrogate doesn't use them.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.config.lithium_config import LITHIUM_CONFIG       # noqa: E402
from src.config.nickel_config import NICKEL_CONFIG         # noqa: E402
from src.config.platinum_config import PLATINUM_CONFIG     # noqa: E402
from src.model.supply_chain_model import MineralSupplyChainModel  # noqa: E402

_BASE_CONFIGS = {
    "lithium": LITHIUM_CONFIG,
    "nickel":  NICKEL_CONFIG,
    "platinum": PLATINUM_CONFIG,
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--scenarios", type=Path, required=True,
        help="Path to a per-mineral JSON file (list of scenario dicts) "
             "produced by sample_scenarios.py.",
    )
    p.add_argument(
        "--index", type=int, required=True,
        help="0-based index into the scenarios list.",
    )
    p.add_argument(
        "--output-root", type=Path, required=True,
        help="Directory under which <mineral>/<index>.csv is written. "
             "Typically $SCRATCH/surrogate_data or "
             "/global/cfs/projectdirs/amsc001/www/surrogate_data on NERSC.",
    )
    p.add_argument(
        "--skip-existing", action="store_true",
        help="If the output CSV already exists, exit 0 without rerunning. "
             "Useful for restarting failed Slurm array jobs.",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    with args.scenarios.open() as f:
        scenarios = json.load(f)
    if not isinstance(scenarios, list):
        raise SystemExit(f"{args.scenarios}: top level must be a list")
    if args.index < 0 or args.index >= len(scenarios):
        raise SystemExit(
            f"index {args.index} out of range [0, {len(scenarios)})"
        )
    scenario = scenarios[args.index]
    mineral = scenario["mineral"]
    if mineral not in _BASE_CONFIGS:
        raise SystemExit(f"unknown mineral '{mineral}' in scenario {args.index}")

    out_dir = args.output_root / mineral
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.index:06d}.csv"

    if args.skip_existing and out_path.exists():
        print(f"[skip] {out_path} already exists")
        return 0

    # Build the model config: defaults from the per-mineral config,
    # overridden by anything in scenario['config_overrides'], plus the
    # scenario's events and seed.
    cfg = dict(_BASE_CONFIGS[mineral])
    cfg.update(scenario.get("config_overrides", {}) or {})
    cfg["random_seed"] = int(scenario.get("random_seed", 42))
    cfg["n_steps"] = int(scenario.get("n_steps", cfg.get("n_steps", 1352)))
    if scenario.get("embargoes"):
        cfg["political_embargoes"] = list(scenario["embargoes"])
    if scenario.get("chokepoint_crises"):
        cfg["chokepoint_crises"] = list(scenario["chokepoint_crises"])

    t0 = time.time()
    model = MineralSupplyChainModel(cfg)
    model.run_model(cfg["n_steps"])
    df = model.get_model_data()
    df.to_csv(out_path, index=False)
    dt = time.time() - t0
    print(f"[done] {out_path}  ({len(df)} rows, {dt:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
