#!/usr/bin/env python3
"""Walk the per-mineral run CSVs + scenarios and build a tabular dataset.

Output: one parquet per mineral plus a top-level index.json.

Typical use (after Slurm sample job has completed):

    uv run python scripts/build_dataset.py \
        --runs $CFS/amsc001/www/surrogate_data/runs \
        --scenarios $CFS/amsc001/www/surrogate_data/scenarios \
        --out $CFS/amsc001/www/surrogate_data/datasets

The parquet files are small (a few MB per mineral) -- they only carry
encoded features + extracted scalar targets, not the raw 1352-step
trajectories. Downstream training / inference reads only the parquet.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.surrogate import features as ft         # noqa: E402
from src.surrogate import dataset as ds           # noqa: E402
from src.surrogate import targets as tg           # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runs", type=Path, default=None,
                   help="Directory holding per-mineral subdirs of run CSVs / "
                        "H5 files.  Mutually exclusive with --runs-h5.")
    p.add_argument("--runs-h5", type=Path, default=None,
                   help="Directory containing per-mineral aggregate HDF5 "
                        "files (output of compact_csvs_to_hdf5.py).  "
                        "Faster than --runs at scale (>100k sims).")
    p.add_argument("--scenarios", type=Path, required=True,
                   help="Directory containing <mineral>.json scenario lists.")
    p.add_argument("--out", type=Path, required=True,
                   help="Where to write <mineral>.parquet files.")
    p.add_argument("--mineral", choices=list(ft.COUNTRIES_BY_MINERAL),
                   action="append",
                   help="Restrict to specific minerals (repeatable; default all).")
    p.add_argument("--n-steps", type=int, default=ft.DEFAULT_N_STEPS,
                   help="Simulation horizon used to normalize start_step. "
                        "Must match what was used to sample scenarios.")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    # Detect single-seed vs ensemble mode from the scenarios index.json.
    seeds_per_scenario = 1
    scen_index = args.scenarios / "index.json"
    if scen_index.is_file():
        try:
            with scen_index.open() as f:
                idx = json.load(f)
            seeds_per_scenario = int(idx.get("seeds_per_scenario", 1))
        except (json.JSONDecodeError, ValueError):
            pass
    print(f"seeds_per_scenario = {seeds_per_scenario} "
          f"(from {scen_index if scen_index.is_file() else 'default'})")

    if args.runs is None and args.runs_h5 is None:
        raise SystemExit("must pass either --runs or --runs-h5")
    if args.runs is not None and args.runs_h5 is not None:
        raise SystemExit("--runs and --runs-h5 are mutually exclusive")

    minerals = args.mineral or list(ft.COUNTRIES_BY_MINERAL)
    summary: dict = {"seeds_per_scenario": seeds_per_scenario, "minerals": {}}
    for mineral in minerals:
        scen_path = args.scenarios / f"{mineral}.json"
        if not scen_path.is_file():
            print(f"[{mineral}] no scenarios at {scen_path}; skipping")
            continue

        if args.runs_h5 is not None:
            h5_path = args.runs_h5 / f"{mineral}.h5"
            if not h5_path.is_file():
                print(f"[{mineral}] no h5 at {h5_path}; skipping")
                continue
            if seeds_per_scenario > 1:
                df = ds.build_ensemble_dataset_from_h5(
                    mineral, h5_path, scen_path,
                    seeds_per_scenario=seeds_per_scenario,
                    n_steps=args.n_steps,
                )
            else:
                raise SystemExit(
                    "--runs-h5 path supports seeds_per_scenario>1 only; "
                    "single-seed builds still iterate the per-CSV "
                    "tree via --runs"
                )
        else:
            runs_dir = args.runs / mineral
            if not runs_dir.is_dir():
                print(f"[{mineral}] no runs dir at {runs_dir}; skipping")
                continue
            if seeds_per_scenario > 1:
                df = ds.build_ensemble_dataset(
                    mineral, runs_dir, scen_path,
                    seeds_per_scenario=seeds_per_scenario,
                    n_steps=args.n_steps,
                )
            else:
                df = ds.build_mineral_dataset(
                    mineral, runs_dir, scen_path, n_steps=args.n_steps,
                )

        if df.empty:
            print(f"[{mineral}] empty dataset; skipping")
            continue
        out_path = args.out / f"{mineral}.parquet"
        df.to_parquet(out_path, index=False)
        print(f"[{mineral}] wrote {out_path} ({len(df)} rows, "
              f"{len(df.columns)} columns)")
        summary["minerals"][mineral] = {
            "n_rows": int(len(df)),
            "feature_dim": ft.feature_dim(mineral),
            "target_names": list(tg.TARGET_NAMES),
            "parquet": str(out_path),
        }
    with (args.out / "index.json").open("w") as f:
        json.dump(summary, f, indent=2)
    print(f"wrote {args.out / 'index.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
