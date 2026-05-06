#!/usr/bin/env python3
"""Train Conformalized Quantile Regression (CQR) surrogates per mineral.

Companion to ``scripts/train_surrogate.py``: same ensemble parquet
input, but fits three LightGBM boosters per target (q_lo / q_med /
q_hi) plus a held-out conformal calibration offset, giving prediction
intervals with a distribution-free marginal-coverage guarantee.

Outputs one bundle per mineral plus a per-target metrics side-car:

    surrogate_models/lithium_quantile.pkl
    surrogate_models/lithium_quantile.metrics.json
    surrogate_models/nickel_quantile.pkl
    ...

CQR bundles can live in the same directory as the existing
``*_scalar.pkl`` Phase-2 bundles; ``surrogate.predict.load_models``
discovers both and dispatches by bundle type.

Typical workflow:

    uv run python scripts/build_dataset.py \\
        --runs   $CFS/amsc001/www/surrogate_data/runs \\
        --scenarios $CFS/amsc001/www/surrogate_data/scenarios \\
        --out    $CFS/amsc001/www/surrogate_data/datasets

    uv run python scripts/train_quantile.py \\
        --datasets $CFS/amsc001/www/surrogate_data/datasets \\
        --out      surrogate_models/ \\
        --alpha    0.10
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.surrogate import features as ft         # noqa: E402
from src.surrogate import quantile as qt         # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--datasets", type=Path, required=True,
                   help="Directory holding <mineral>.parquet files.")
    p.add_argument("--out", type=Path, required=True,
                   help="Where <mineral>_quantile.pkl bundles get written.")
    p.add_argument("--mineral", choices=list(ft.COUNTRIES_BY_MINERAL),
                   action="append",
                   help="Train only specific minerals (repeatable).")
    p.add_argument("--alpha", type=float, default=0.10,
                   help="Miscoverage rate; intervals target 1-alpha "
                        "marginal coverage.  default 0.10 (90%% intervals).")
    p.add_argument("--seed", type=int, default=0,
                   help="Train/val/cal/test split seed (default 0).")
    p.add_argument("--test-frac", type=float, default=0.10)
    p.add_argument("--val-frac", type=float, default=0.10)
    p.add_argument("--cal-frac", type=float, default=0.10,
                   help="Held-out fraction used for the conformal "
                        "calibration step (default 0.10).")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    minerals = args.mineral or list(ft.COUNTRIES_BY_MINERAL)
    print(f"alpha={args.alpha}  -> target coverage {1 - args.alpha:.0%}")
    print()
    overall: dict = {"alpha": args.alpha, "minerals": {}}
    for mineral in minerals:
        parquet = args.datasets / f"{mineral}.parquet"
        if not parquet.is_file():
            print(f"[{mineral}] no parquet at {parquet}; skipping")
            continue
        print(f"=== {mineral} ===")
        t0 = time.time()
        bundle = qt.train_mineral_quantile(
            parquet, mineral, alpha=args.alpha,
            test_frac=args.test_frac, val_frac=args.val_frac,
            cal_frac=args.cal_frac, seed=args.seed,
        )
        elapsed = time.time() - t0
        out_path = args.out / f"{mineral}_quantile.pkl"
        qt.save_bundle(bundle, out_path)
        print(f"[{mineral}] saved {out_path}  ({elapsed:.1f}s)")
        overall["minerals"][mineral] = {
            t: asdict(m) for t, m in bundle.metrics.items()
        }
        print()
    summary = args.out / "quantile_summary.json"
    with summary.open("w") as f:
        json.dump(overall, f, indent=2)
    print(f"wrote {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
