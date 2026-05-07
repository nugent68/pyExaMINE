#!/usr/bin/env python3
"""Train a per-mineral DeepONet trajectory surrogate.

Default corpus is the small canonical ``ensemble_runs/2050/`` test set,
useful for smoke-testing the pipeline.  Pass ``--sweep-runs`` to point
at a full sweep directory (per-mineral subdirs of ``NNNNNN.csv`` files
plus a matching ``--sweep-scenarios`` JSON tree).

Outputs one ``<mineral>_trajectory.pkl`` bundle per mineral plus a
metrics side-car JSON, mirroring the GBT scalar pipeline.

Quick smoke-test:

    uv run --extra trajectory python scripts/train_trajectory.py \\
        --canonical-root ensemble_runs/2050 \\
        --out trajectory_models/ \\
        --mineral lithium \\
        --epochs 3 --subsample 50

Full-data run on the sweep (after sweep 52575362 lands):

    uv run --extra trajectory python scripts/train_trajectory.py \\
        --sweep-runs $CFS/.../runs --sweep-scenarios $CFS/.../scenarios \\
        --out trajectory_models/ \\
        --mineral lithium \\
        --epochs 50 --subsample 100
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.surrogate import features as ft        # noqa: E402
from src.trajectory import (                     # noqa: E402
    dataset as td,
    deeponet as dn,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)

    src = p.add_argument_group("data source")
    src.add_argument("--canonical-root", type=Path,
                     default=Path("ensemble_runs/2050"),
                     help="Path to a canonical 2050 ensemble tree "
                          "(default ./ensemble_runs/2050).")
    src.add_argument("--sweep-runs", type=Path, default=None,
                     help="If set, use a sweep runs/ tree instead of the "
                          "canonical one. Pair with --sweep-scenarios.")
    src.add_argument("--sweep-scenarios", type=Path, default=None,
                     help="Scenarios root (with <mineral>.json) for "
                          "--sweep-runs.")
    src.add_argument("--mineral",
                     choices=list(ft.COUNTRIES_BY_MINERAL),
                     action="append",
                     help="Train only specific minerals (repeatable).")
    src.add_argument("--target-column", default=td.DEFAULT_TARGET,
                     help="Per-step CSV column to predict.")

    arch = p.add_argument_group("model")
    arch.add_argument("--arch", choices=[dn.ARCH_DEEPONET,
                                          dn.ARCH_DEEPONET_PHASE,
                                          dn.ARCH_HYBRID],
                      default=dn.ARCH_DEEPONET,
                      help="Model variant. deeponet_phase augments the trunk "
                           "with scenario-derived event-phase features. "
                           "hybrid uses smooth-baseline + per-event impulse "
                           "decomposition.")
    arch.add_argument("--basis-dim", type=int, default=64)
    arch.add_argument("--branch-hidden", type=int, nargs="+",
                      default=[256, 256])
    arch.add_argument("--trunk-hidden",  type=int, nargs="+",
                      default=[128, 128])
    arch.add_argument("--time-embed-dim", type=int, default=64)
    arch.add_argument("--hybrid-variant", default="default",
                      help="Named hyperparam variant for --arch hybrid; one "
                           "of the keys in src.trajectory.hybrid.HYBRID_VARIANTS. "
                           "Used by the architecture-sweep slurm script.")
    arch.add_argument("--no-log-target", action="store_true",
                      help="Train on raw target instead of log-target.")

    train = p.add_argument_group("training")
    train.add_argument("--out", type=Path, required=True,
                       help="Where <mineral>_trajectory.pkl bundles go.")
    train.add_argument("--epochs", type=int, default=20)
    train.add_argument("--batch-size", type=int, default=512)
    train.add_argument("--lr", type=float, default=1e-3)
    train.add_argument("--subsample", type=int, default=100,
                       help="Random timesteps drawn per CSV per epoch "
                            "(0 = full 1352 steps).")
    train.add_argument("--seed", type=int, default=0)
    train.add_argument("--device", default=None,
                       help="cpu | cuda | mps. Auto-detect if omitted.")
    train.add_argument("--num-workers", type=int, default=0)
    train.add_argument("--cache-dir", type=Path, default=None,
                       help="Directory to cache pre-loaded trajectory tensors. "
                            "First run writes a .pt; subsequent runs reload it "
                            "in seconds instead of re-reading every CSV.")
    train.add_argument("--persistent-workers", action="store_true",
                       help="Reuse DataLoader workers between epochs (skips "
                            "re-fork cost).")
    return p.parse_args()


def _pick_device(arg: str | None) -> torch.device:
    if arg is not None:
        return torch.device(arg)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _scan_records(args, mineral: str) -> list[td.TrajectoryRecord]:
    if args.sweep_runs is not None:
        if args.sweep_scenarios is None:
            raise SystemExit("--sweep-runs requires --sweep-scenarios")
        return td._scan_sweep(args.sweep_runs, args.sweep_scenarios,
                              minerals=[mineral])
    return [r for r in td._scan_canonical_2050(args.canonical_root)
            if r.mineral == mineral]


def _train_one_mineral(args, mineral: str, device: torch.device) -> None:
    print(f"\n=== {mineral} ===")
    records = _scan_records(args, mineral)
    if not records:
        print(f"  no records found, skipping")
        return

    train_recs, val_recs, test_recs = td.split_by_unique_scenario(
        records, test_frac=0.10, val_frac=0.10, seed=args.seed,
    )
    print(f"  records: {len(records)} total -> "
          f"{len(train_recs)} train / {len(val_recs)} val / "
          f"{len(test_recs)} test")
    if not train_recs or not val_recs or not test_recs:
        print(f"  too few unique-scenario groups for a clean split; "
              f"using all records for train/val/test")
        train_recs = val_recs = test_recs = records

    with_phase = (args.arch == dn.ARCH_DEEPONET_PHASE)
    with_hybrid = (args.arch == dn.ARCH_HYBRID)
    train_ds = td.TrajectoryDataset(
        train_recs, target_column=args.target_column,
        subsample=args.subsample, seed=args.seed,
        cache_dir=args.cache_dir,
        with_phase=with_phase, with_hybrid=with_hybrid,
    )
    val_ds = td.TrajectoryDataset(
        val_recs, target_column=args.target_column,
        subsample=max(args.subsample, 200), seed=args.seed + 1,
        cache_dir=args.cache_dir,
        with_phase=with_phase, with_hybrid=with_hybrid,
    )
    test_ds = td.TrajectoryDataset(
        test_recs, target_column=args.target_column,
        subsample=0,    # full trajectory at test time
        seed=args.seed + 2,
        cache_dir=args.cache_dir,
        with_phase=with_phase, with_hybrid=with_hybrid,
    )
    feat_dim = train_ds.feature_dim()

    # ---- log-target normalization ----
    log_target = not args.no_log_target
    sample_vals: list[float] = []
    for ri in range(min(64, len(train_ds.records))):
        sample_vals.extend(train_ds._load_values(ri).tolist())
    sample = np.asarray(sample_vals, dtype=np.float64)
    if log_target:
        sample = np.log(np.clip(sample, 1e-9, None))
    target_mean = float(sample.mean())
    target_std = float(sample.std()) or 1.0

    hybrid_config: dict = {}
    if with_hybrid:
        from src.trajectory import hybrid as hb     # local import
        hybrid_config = hb.hybrid_config_for_variant(mineral, args.hybrid_variant)
    bundle = dn.MineralTrajectoryBundle(
        mineral=mineral,
        feature_dim=feat_dim,
        basis_dim=args.basis_dim,
        branch_hidden=list(args.branch_hidden),
        trunk_hidden=list(args.trunk_hidden),
        time_embed_dim=args.time_embed_dim,
        target_column=args.target_column,
        n_steps=ft.DEFAULT_N_STEPS,
        log_target=log_target,
        target_log_mean=target_mean,
        target_log_std=target_std,
        seed=args.seed,
        arch=args.arch,
        hybrid_config=hybrid_config,
    )

    model = bundle.build_model().to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    loss_fn = torch.nn.MSELoss()

    pw = args.persistent_workers and args.num_workers > 0
    train_loader_kwargs = dict(batch_size=args.batch_size,
                               shuffle=True, num_workers=args.num_workers,
                               pin_memory=(device.type == "cuda"),
                               persistent_workers=pw)
    val_loader_kwargs = dict(batch_size=args.batch_size,
                             num_workers=args.num_workers,
                             pin_memory=(device.type == "cuda"),
                             persistent_workers=pw)

    print(f"  feature_dim={feat_dim} basis_dim={args.basis_dim} "
          f"params={sum(p.numel() for p in model.parameters()):,}")
    print(f"  device={device} log_target={log_target} "
          f"target_log_mean={target_mean:.3f} std={target_std:.3f}")

    def _forward(batch):
        """Dispatch by tuple length:
            3-tuple = deeponet (features, t, y)
            4-tuple = deeponet_phase (features, t, phase, y)
            7-tuple = hybrid (knobs, t, ev_id, ev_st, ev_du, ev_ac, y)
        """
        if len(batch) == 7:
            kn, tnorm, ev_id, ev_st, ev_du, ev_ac, y = batch
            kn = kn.to(device); tnorm = tnorm.to(device); y = y.to(device)
            ev_id = ev_id.to(device); ev_st = ev_st.to(device)
            ev_du = ev_du.to(device); ev_ac = ev_ac.to(device)
            return y, model(kn, tnorm, ev_id, ev_st, ev_du, ev_ac)
        if len(batch) == 4:
            feats, tnorm, phase, y = batch
            feats = feats.to(device); tnorm = tnorm.to(device)
            phase = phase.to(device); y = y.to(device)
            return y, model(feats, tnorm, phase)
        feats, tnorm, y = batch
        feats = feats.to(device); tnorm = tnorm.to(device); y = y.to(device)
        return y, model(feats, tnorm)

    best_val = float("inf")
    best_state: dict[str, torch.Tensor] | None = None
    last_train_loss = float("nan")

    for epoch in range(1, args.epochs + 1):
        t0 = time.time()
        train_ds.resample()
        val_ds.resample()
        train_loader = DataLoader(train_ds, **train_loader_kwargs)
        val_loader = DataLoader(val_ds, **val_loader_kwargs)

        model.train()
        train_losses: list[float] = []
        for batch in train_loader:
            y, y_hat_n = _forward(batch)
            y_n = bundle.normalise(y)
            loss = loss_fn(y_hat_n, y_n)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            opt.step()
            train_losses.append(float(loss.detach()))
        sched.step()
        last_train_loss = float(np.mean(train_losses)) if train_losses else float("nan")

        model.eval()
        with torch.no_grad():
            val_losses: list[float] = []
            for batch in val_loader:
                y, y_hat_n = _forward(batch)
                y_n = bundle.normalise(y)
                val_losses.append(float(loss_fn(y_hat_n, y_n)))
            val_loss = float(np.mean(val_losses)) if val_losses else float("nan")

        elapsed = time.time() - t0
        print(f"  epoch {epoch:>3d}/{args.epochs}  "
              f"train_mse={last_train_loss:.4f}  "
              f"val_mse={val_loss:.4f}  "
              f"lr={opt.param_groups[0]['lr']:.2e}  "
              f"elapsed={elapsed:.1f}s")
        if val_loss < best_val:
            best_val = val_loss
            best_state = {k: v.detach().cpu().clone()
                          for k, v in model.state_dict().items()}

    # ---- final evaluation on the test set ----
    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    test_loader = DataLoader(test_ds, **val_loader_kwargs)
    sq_errs, abs_errs, abs_y, sq_norm = [], [], [], []
    with torch.no_grad():
        for batch in test_loader:
            y, y_hat_n = _forward(batch)
            y_n = bundle.normalise(y)
            sq_norm.append(float(torch.mean((y_hat_n - y_n) ** 2)))
            y_hat = bundle.denormalise(y_hat_n)
            sq_errs.append(((y_hat - y) ** 2).cpu().numpy())
            abs_errs.append((y_hat - y).abs().cpu().numpy())
            abs_y.append(y.abs().cpu().numpy())
    sq = np.concatenate(sq_errs); ab = np.concatenate(abs_errs)
    ay = np.concatenate(abs_y)
    test_rmse = float(np.sqrt(sq.mean()))
    test_mae = float(ab.mean())
    rel_rmse = test_rmse / max(1e-9, float(ay.mean()))
    test_loss = float(np.mean(sq_norm))

    bundle.state_dict = best_state or {
        k: v.detach().cpu().clone() for k, v in model.state_dict().items()
    }
    bundle.metrics = dn.TrajectoryMetrics(
        n_train_csvs=len(train_recs),
        n_val_csvs=len(val_recs),
        n_test_csvs=len(test_recs),
        train_loss=last_train_loss,
        val_loss=best_val,
        test_loss=test_loss,
        test_rmse=test_rmse,
        test_mae=test_mae,
        test_relative_rmse=rel_rmse,
    )

    print(f"  TEST -> rmse={test_rmse:.3g}  mae={test_mae:.3g}  "
          f"rel_rmse={rel_rmse:.3f}  (best val_mse={best_val:.4f})")

    out_path = args.out / f"{mineral}_trajectory.pkl"
    dn.save_bundle(bundle, out_path)
    side_car = out_path.with_suffix(".metrics.json")
    payload = {
        "mineral": mineral,
        "arch": args.arch,
        "hybrid_variant": args.hybrid_variant if with_hybrid else None,
        "hybrid_config": hybrid_config if with_hybrid else None,
        "feature_dim": feat_dim,
        "basis_dim": args.basis_dim,
        "branch_hidden": list(args.branch_hidden),
        "trunk_hidden": list(args.trunk_hidden),
        "time_embed_dim": args.time_embed_dim,
        "target_column": args.target_column,
        "n_steps": ft.DEFAULT_N_STEPS,
        "log_target": log_target,
        "metrics": asdict(bundle.metrics) if bundle.metrics else None,
    }
    with side_car.open("w") as f:
        json.dump(payload, f, indent=2)
    print(f"  saved {out_path}")


def main() -> int:
    args = _parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    device = _pick_device(args.device)
    minerals = args.mineral or list(ft.COUNTRIES_BY_MINERAL)
    for mineral in minerals:
        _train_one_mineral(args, mineral, device)
    return 0


if __name__ == "__main__":
    sys.exit(main())
