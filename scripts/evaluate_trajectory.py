#!/usr/bin/env python3
"""Evaluate a trained DeepONet trajectory bundle against ground truth.

Three lenses on quality (see :mod:`src.trajectory.eval` for the
details):

  1. trajectory-level RMSE / relative RMSE,
  2. derived-scalar agreement vs the GBT scalar surrogate AND vs
     ground truth,
  3. event-onset peak alignment.

Inputs are pulled from disk, not from a Slurm-allocated tensor cache,
so this can run on a Mac against a small subset of CSVs without
needing the full sweep tree mounted.

Typical use:

    # 1. From a Mac, after pulling bundles + a few hundred CSVs:
    uv run --extra trajectory python scripts/evaluate_trajectory.py \\
        --trajectory-bundles trajectory_models/ \\
        --gbt-bundles surrogate_models/ \\
        --runs-root local_eval_data/runs \\
        --scenarios-root local_eval_data/scenarios \\
        --n-per-mineral 100 \\
        --out trajectory_models/eval.json

    # 2. From a NERSC login / GPU node (full data on /pscratch):
    uv run --extra trajectory python scripts/evaluate_trajectory.py \\
        --trajectory-bundles /pscratch/.../trajectory_models \\
        --gbt-bundles        /pscratch/.../surrogate_models \\
        --runs-root          /pscratch/.../surrogate_data/runs \\
        --scenarios-root     /pscratch/.../surrogate_data/scenarios \\
        --n-per-mineral 500 \\
        --out trajectory_models/eval.json
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.surrogate import features as ft               # noqa: E402
from src.surrogate import predict as sp                # noqa: E402
from src.trajectory import deeponet as dn              # noqa: E402
from src.trajectory import eval as te                  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trajectory-bundles", type=Path, required=True,
                   help="Directory holding <mineral>_trajectory.pkl bundles.")
    p.add_argument("--gbt-bundles", type=Path, default=None,
                   help="Optional directory holding the Phase-2 GBT bundles "
                        "(_scalar.pkl).  If omitted, GBT comparison block "
                        "in the report is skipped.")
    p.add_argument("--runs-root", type=Path, required=True,
                   help="Per-mineral subdirs of <NNNNNN>.csv ground-truth "
                        "trajectories.")
    p.add_argument("--scenarios-root", type=Path, required=True,
                   help="Directory holding <mineral>.json scenario lists.")
    p.add_argument("--mineral", choices=list(ft.COUNTRIES_BY_MINERAL),
                   action="append",
                   help="Evaluate only specific minerals (repeatable).")
    p.add_argument("--n-per-mineral", type=int, default=200,
                   help="Random subsample of test scenarios per mineral.")
    p.add_argument("--seed", type=int, default=0,
                   help="RNG seed for the subsample selection.")
    p.add_argument("--device", default=None,
                   help="cpu | cuda | mps. Auto-detect if omitted.")
    p.add_argument("--out", type=Path, default=None,
                   help="Optional JSON output path.")
    return p.parse_args()


def _pick_device(arg: str | None) -> torch.device:
    if arg is not None:
        return torch.device(arg)
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def _load_traj_bundle(path: Path) -> dn.MineralTrajectoryBundle:
    with path.open("rb") as f:
        return pickle.load(f)


def _scenario_records(
    runs_dir: Path, scenarios_root: Path, mineral: str, n: int, seed: int,
) -> list[tuple[int, dict]]:
    """Pick ``n`` random ``(flat_index, scenario_dict)`` pairs.

    Filters to flat indices whose ground-truth CSV is actually present
    on disk -- so we don't pick scenarios we'll have to skip later.
    """
    scen_path = scenarios_root / f"{mineral}.json"
    with scen_path.open() as f:
        scenarios = json.load(f)
    # Indices for which a CSV exists in runs_dir (subset == eligible).
    eligible: list[int] = []
    for csv in sorted(runs_dir.glob("*.csv")):
        try:
            i = int(csv.stem)
        except ValueError:
            continue
        if 0 <= i < len(scenarios):
            eligible.append(i)
    if not eligible:
        return []
    rng = np.random.default_rng(seed)
    n = min(n, len(eligible))
    chosen = rng.choice(len(eligible), size=n, replace=False)
    out_idx = sorted(eligible[c] for c in chosen)
    return [(int(i), scenarios[i]) for i in out_idx]


def _evaluate_mineral(
    mineral: str,
    args: argparse.Namespace,
    device: torch.device,
    gbt_models: dict | None,
) -> te.TrajectoryEvalReport | None:
    bundle_path = args.trajectory_bundles / f"{mineral}_trajectory.pkl"
    if not bundle_path.is_file():
        print(f"[{mineral}] no trajectory bundle at {bundle_path}; skipping")
        return None
    runs_dir = args.runs_root / mineral
    if not runs_dir.is_dir():
        print(f"[{mineral}] no runs dir at {runs_dir}; skipping")
        return None

    bundle = _load_traj_bundle(bundle_path)
    print(f"[{mineral}] loaded {bundle_path.name} "
          f"(feature_dim={bundle.feature_dim}, "
          f"basis_dim={bundle.basis_dim}, "
          f"target={bundle.target_column})")

    pairs = _scenario_records(runs_dir, args.scenarios_root, mineral,
                              args.n_per_mineral, args.seed)
    print(f"[{mineral}] sampled {len(pairs)} scenarios for eval")

    # Load ground-truth trajectories that exist on disk.  If a flat
    # index is missing (gap-fill never landed), we silently skip it.
    truths: list[np.ndarray] = []
    scenarios: list[dict] = []
    flat_idx_kept: list[int] = []
    for flat_idx, scen in pairs:
        csv = runs_dir / f"{flat_idx:06d}.csv"
        if not csv.is_file():
            continue
        df = pd.read_csv(csv, usecols=[bundle.target_column])
        truths.append(df[bundle.target_column].to_numpy(dtype=np.float64))
        scenarios.append(scen)
        flat_idx_kept.append(flat_idx)
    if not scenarios:
        print(f"[{mineral}] no ground-truth CSVs found; skipping")
        return None

    # Predict trajectories.  Encode all features once, then call the
    # vectorised helper which evaluates the full horizon in one shot.
    # Phase bundles also need the per-scenario event timeline.
    X = np.stack([ft.encode(s, n_steps=bundle.n_steps).astype(np.float32)
                  for s in scenarios])
    needs_scenarios = bundle.is_phase() or bundle.is_hybrid()
    preds = dn.predict_trajectory(
        bundle, X, device=device,
        scenarios=scenarios if needs_scenarios else None,
    )                                                          # (B, T)
    preds_list = [preds[i] for i in range(preds.shape[0])]

    # GBT scalar predictions on the same scenarios (optional).
    gbt_preds: list[dict] | None = None
    if gbt_models is not None and mineral in gbt_models:
        gbt_preds = [sp.predict(s, gbt_models) for s in scenarios]

    report = te.evaluate_bundle(
        mineral=mineral,
        preds=preds_list,
        truths=truths,
        scenarios=scenarios,
        gbt_predictions=gbt_preds,
    )
    print()
    print(te.format_report(report))
    print()
    return report


def main() -> int:
    args = _parse_args()
    device = _pick_device(args.device)
    minerals = args.mineral or list(ft.COUNTRIES_BY_MINERAL)

    gbt_models: dict | None = None
    if args.gbt_bundles is not None and args.gbt_bundles.is_dir():
        # Prefer the Phase-2 ensemble (point) bundles for the comparison
        # because they emit the same "<target>_mean" key the eval rubric
        # expects.
        gbt_models = sp.load_models(args.gbt_bundles, kind="point")
        print(f"loaded GBT bundles for: {sorted(gbt_models)}")

    reports: list[te.TrajectoryEvalReport] = []
    for mineral in minerals:
        report = _evaluate_mineral(mineral, args, device, gbt_models)
        if report is not None:
            reports.append(report)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "n_per_mineral_requested": args.n_per_mineral,
            "seed": args.seed,
            "reports": [te.report_to_dict(r) for r in reports],
        }
        with args.out.open("w") as f:
            json.dump(payload, f, indent=2)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
