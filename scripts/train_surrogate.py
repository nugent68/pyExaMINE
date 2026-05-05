#!/usr/bin/env python3
"""Train per-mineral LightGBM scalar surrogates from a built dataset.

Run after ``scripts/build_dataset.py`` has produced
``<datasets_dir>/<mineral>.parquet``. Saves one model bundle per
mineral plus a JSON of held-out test-set metrics next to it:

    surrogate_models/lithium_scalar.pkl
    surrogate_models/lithium_scalar.metrics.json
    surrogate_models/nickel_scalar.pkl
    surrogate_models/nickel_scalar.metrics.json
    surrogate_models/platinum_scalar.pkl
    surrogate_models/platinum_scalar.metrics.json

Typical workflow:

    uv run python scripts/build_dataset.py \
        --runs $CFS/amsc001/www/surrogate_data/runs \
        --scenarios $CFS/amsc001/www/surrogate_data/scenarios \
        --out $CFS/amsc001/www/surrogate_data/datasets

    uv run python scripts/train_surrogate.py \
        --datasets $CFS/amsc001/www/surrogate_data/datasets \
        --out surrogate_models/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.surrogate import features as ft           # noqa: E402
from src.surrogate import train_scalar as ts       # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--datasets", type=Path, required=True,
                   help="Directory holding <mineral>.parquet files.")
    p.add_argument("--out", type=Path, required=True,
                   help="Where <mineral>_scalar.pkl bundles get written.")
    p.add_argument("--mineral", choices=list(ft.COUNTRIES_BY_MINERAL),
                   action="append",
                   help="Train only specific minerals (repeatable).")
    p.add_argument("--seed", type=int, default=0,
                   help="train/val/test split seed (default 0).")
    p.add_argument("--test-frac", type=float, default=0.10)
    p.add_argument("--val-frac", type=float, default=0.10)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    minerals = args.mineral or list(ft.COUNTRIES_BY_MINERAL)
    for mineral in minerals:
        parquet = args.datasets / f"{mineral}.parquet"
        if not parquet.is_file():
            print(f"[{mineral}] no dataset at {parquet}; skipping")
            continue
        print(f"[{mineral}] training on {parquet}")
        bundle = ts.train_mineral(
            parquet, mineral,
            test_frac=args.test_frac,
            val_frac=args.val_frac,
            seed=args.seed,
        )
        out_path = args.out / f"{mineral}_scalar.pkl"
        ts.save_bundle(bundle, out_path)
        print(f"[{mineral}] saved {out_path} (+ metrics side-car)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
