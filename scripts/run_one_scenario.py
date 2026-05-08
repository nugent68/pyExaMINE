#!/usr/bin/env python3
"""Run one pyExaMINE scenario from a JSON description.

This is the Slurm-array workhorse for surrogate-training data generation.
Each Slurm task picks one scenario out of a per-mineral JSON file and
runs the full simulation, writing the model_data trajectory under
``--output-root``.

Usage:
    python scripts/run_one_scenario.py \
        --scenarios scenarios/lithium.json \
        --index 17 \
        --output-root /pscratch/.../surrogate_data \
        --format h5

Output layout (one file per scenario):
    <output-root>/<mineral>/<index>.csv     (--format csv, default)
    <output-root>/<mineral>/<index>.h5      (--format h5)

The output is the full ``model_data`` time series; targets/features
get extracted later by the training pipeline.  We deliberately avoid
writing PNGs / summary text files here -- those add I/O overhead and
the surrogate doesn't use them.

CSV vs H5: at scale (>100k scenarios) the CSV path becomes painful --
each file is ~470 KB ASCII, slow to parse with pandas, and the inode
count starts to bother Lustre and ``tar``.  ``--format h5`` writes
one HDF5 file per scenario with one float32 dataset per column;
~5x smaller on disk, ~10x faster to read in the post-job
compaction step.  Both formats are accepted by
``scripts/compact_csvs_to_hdf5.py`` (autodetects by extension) and
both produce identical per-mineral HDF5 output.
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
        help="If the output already exists, exit 0 without rerunning. "
             "Useful for restarting failed Slurm array jobs.  "
             "Checks both .csv and .h5 in --output-root so a sweep "
             "that switched formats partway through is still resumable.",
    )
    p.add_argument(
        "--format", choices=["csv", "h5"], default="csv",
        help="Output format.  csv = legacy per-row text (default; "
             "back-compatible with everything that reads model_data).  "
             "h5 = per-scenario HDF5 with one float32 dataset per "
             "column; recommended for large sweeps.",
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
    csv_path = out_dir / f"{args.index:06d}.csv"
    h5_path = out_dir / f"{args.index:06d}.h5"
    out_path = h5_path if args.format == "h5" else csv_path

    if args.skip_existing and (csv_path.exists() or h5_path.exists()):
        existing = csv_path if csv_path.exists() else h5_path
        print(f"[skip] {existing} already exists")
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

    if args.format == "h5":
        _write_per_scenario_h5(df, out_path)
    else:
        df.to_csv(out_path, index=False)
    dt = time.time() - t0
    print(f"[done] {out_path}  ({len(df)} rows, {dt:.1f}s)")
    return 0


def _write_per_scenario_h5(df, out_path):
    """Write a single scenario's trajectory to a per-column HDF5.

    Layout:
        /<column_name>     (T,) float32     -- one dataset per column

    This mirrors the per-mineral layout in
    ``scripts/compact_csvs_to_hdf5.py`` (where the per-mineral file
    has ``(N, T)`` arrays); the only difference is the leading
    dimension.  Compaction then becomes a simple stack across N
    individual files, no parsing required.

    Compression: gzip level 4.  Single-scenario files are small
    enough that compression overhead per file dominates; we still
    enable it so per-mineral aggregate disk is ~5x smaller than the
    CSV equivalent.
    """
    import h5py
    import numpy as np

    # Atomic-rename to handle restart after partial writes.
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    with h5py.File(tmp_path, "w") as h:
        for col in df.columns:
            arr = np.asarray(df[col].to_numpy(), dtype=np.float32)
            h.create_dataset(col, data=arr,
                             compression="gzip",
                             compression_opts=4)
    tmp_path.replace(out_path)


if __name__ == "__main__":
    sys.exit(main())
