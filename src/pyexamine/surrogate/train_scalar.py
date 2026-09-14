"""Train per-mineral, per-target LightGBM scalar surrogates.

For each (mineral, target) pair we fit one LightGBM regressor with
sensible defaults + early stopping on a held-out validation split.
The trained models + their scaler-style metadata go into a single
``.pkl`` file per mineral, keyed by target name.

Held-out test metrics (RMSE, MAE, MAPE) are reported and saved
alongside the model so downstream "is the surrogate trustworthy?"
questions are easy to answer.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import lightgbm as lgb

from . import features as ft
from . import targets as tg
from . import dataset as ds


#: Default LightGBM hyperparameters. Tuned to be reasonable for the
#: ~2k-rows-per-mineral / ~100-features regime; can be overridden via
#: ``train_scalar(... params=...)`` later.
DEFAULT_PARAMS: dict[str, Any] = {
    "objective":         "regression",
    "metric":            "rmse",
    "learning_rate":     0.05,
    "num_leaves":        63,
    "min_data_in_leaf":  10,
    "feature_fraction":  0.9,
    "bagging_fraction":  0.9,
    "bagging_freq":      5,
    "lambda_l2":         0.1,
    "verbose":           -1,
}

#: How many boosting rounds to allow before early stopping fires.
DEFAULT_NUM_BOOST_ROUND = 2000
#: Stop if validation RMSE doesn't improve for this many rounds.
DEFAULT_EARLY_STOPPING_ROUNDS = 50


@dataclass
class TargetMetrics:
    """Held-out test-set metrics for one trained model."""
    rmse: float
    mae: float
    mape: float           # mean abs pct error; nan-safe (excludes zeros)
    r2: float
    n_train: int
    n_val: int
    n_test: int
    best_iteration: int


def _safe_mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    mask = np.abs(y_true) > 1e-9
    if not mask.any():
        return float("nan")
    return float(np.mean(np.abs((y_pred[mask] - y_true[mask]) / y_true[mask])) * 100)


def _r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    mean = float(np.mean(y_true))
    ss_tot = float(np.sum((y_true - mean) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")


def train_one_target(
    X_train: np.ndarray, y_train: np.ndarray,
    X_val: np.ndarray, y_val: np.ndarray,
    X_test: np.ndarray, y_test: np.ndarray,
    feature_names: list[str],
    params: dict | None = None,
    num_boost_round: int = DEFAULT_NUM_BOOST_ROUND,
    early_stopping_rounds: int = DEFAULT_EARLY_STOPPING_ROUNDS,
) -> tuple[lgb.Booster, TargetMetrics]:
    """Fit one LightGBM regressor; return (booster, test-set metrics)."""
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    train_set = lgb.Dataset(X_train, label=y_train, feature_name=feature_names)
    val_set = lgb.Dataset(X_val, label=y_val, reference=train_set,
                          feature_name=feature_names)
    booster = lgb.train(
        p,
        train_set,
        num_boost_round=num_boost_round,
        valid_sets=[val_set],
        valid_names=["val"],
        callbacks=[
            lgb.early_stopping(stopping_rounds=early_stopping_rounds, verbose=False),
            lgb.log_evaluation(0),
        ],
    )
    y_pred = booster.predict(X_test, num_iteration=booster.best_iteration)
    metrics = TargetMetrics(
        rmse=float(np.sqrt(np.mean((y_pred - y_test) ** 2))),
        mae=float(np.mean(np.abs(y_pred - y_test))),
        mape=_safe_mape(y_test, y_pred),
        r2=_r2(y_test, y_pred),
        n_train=int(len(y_train)),
        n_val=int(len(y_val)),
        n_test=int(len(y_test)),
        best_iteration=int(booster.best_iteration or 0),
    )
    return booster, metrics


@dataclass
class MineralModelBundle:
    """Everything needed to serve predictions for one mineral.

    Pickled as a single file. ``predict.py`` loads this and calls each
    booster on the encoded scenario.

    Phase-1 (single-seed) layout:
        boosters    = {target_name: Booster}            # one per target
        metrics     = {target_name: TargetMetrics}

    Phase-2 (ensemble) layout:
        boosters    = {target_name: {'mean': Booster,
                                     'std':  Booster}}  # two per target
        metrics     = {target_name: {'mean': TargetMetrics,
                                     'std':  TargetMetrics}}
    The bundle ``ensemble`` flag tells ``predict.py`` which layout to
    expect. Backwards-compat: old single-seed pickles default
    ``ensemble=False`` and predict.py treats ``boosters[t]`` as the
    raw mean predictor.
    """
    mineral: str
    feature_names: list[str]
    target_names: list[str]
    boosters: dict
    metrics: dict
    train_test_split_seed: int
    n_rows: int
    ensemble: bool = False
    seeds_per_scenario: int = 1


def train_mineral(
    parquet_path: Path,
    mineral: str,
    test_frac: float = 0.10,
    val_frac: float = 0.10,
    seed: int = 0,
    params: dict | None = None,
) -> MineralModelBundle:
    """Load a per-mineral parquet and train one model per scalar target."""
    df = pd.read_parquet(parquet_path)
    if df.empty:
        raise ValueError(f"{parquet_path} is empty")

    X, Y, feature_names, target_names = ds.split_xy(df, mineral)

    train_idx, val_idx, test_idx = ds.train_test_split_indices(
        n=len(df), test_frac=test_frac, val_frac=val_frac, seed=seed,
    )
    X_tr, X_val, X_te = X[train_idx], X[val_idx], X[test_idx]
    Y_tr, Y_val, Y_te = Y[train_idx], Y[val_idx], Y[test_idx]

    boosters: dict[str, lgb.Booster] = {}
    metrics: dict[str, TargetMetrics] = {}
    for j, target in enumerate(target_names):
        booster, m = train_one_target(
            X_tr, Y_tr[:, j],
            X_val, Y_val[:, j],
            X_te, Y_te[:, j],
            feature_names=feature_names,
            params=params,
        )
        boosters[target] = booster
        metrics[target] = m
        print(
            f"  [{mineral}/{target:<32s}] "
            f"rmse={m.rmse:>10.3f}  mae={m.mae:>10.3f}  "
            f"mape={m.mape:>6.2f}%  r2={m.r2:>+0.3f}  "
            f"iters={m.best_iteration}"
        )

    return MineralModelBundle(
        mineral=mineral,
        feature_names=feature_names,
        target_names=target_names,
        boosters=boosters,
        metrics=metrics,
        train_test_split_seed=seed,
        n_rows=int(len(df)),
    )


def train_mineral_ensemble(
    parquet_path: Path,
    mineral: str,
    seeds_per_scenario: int,
    test_frac: float = 0.10,
    val_frac: float = 0.10,
    seed: int = 0,
    params: dict | None = None,
) -> MineralModelBundle:
    """Train Phase-2 ensemble surrogates: per-target mean + std boosters.

    Reads an ensemble-aggregated parquet (one row per *underlying*
    scenario, with ``<target>_mean`` and ``<target>_std`` columns) and
    fits two LightGBM regressors per target -- one predicting the
    expected value, one predicting the seed-to-seed standard deviation.
    The pair lets ``predict.py`` return ``(predicted_mean,
    predicted_std)`` for every target on a brand-new scenario.

    Special handling per target:
      * ``recovered``: the per-scenario mean is a recovery rate in
        [0, 1]; LightGBM regression on it is fine.
      * ``recovery_time_if_recovered``: rows where no seed recovered
        have NaN in the mean column. We mask those rows out of the
        training / validation / test splits for this target only, so
        the mean booster is never asked to learn from a NaN.
    """
    df = pd.read_parquet(parquet_path)
    if df.empty:
        raise ValueError(f"{parquet_path} is empty")

    X, Y_mean, Y_std, feature_names = ds.split_xy_ensemble(df, mineral)

    train_idx, val_idx, test_idx = ds.train_test_split_indices(
        n=len(df), test_frac=test_frac, val_frac=val_frac, seed=seed,
    )
    X_tr, X_val, X_te = X[train_idx], X[val_idx], X[test_idx]

    boosters: dict[str, dict[str, lgb.Booster]] = {}
    metrics: dict[str, dict[str, TargetMetrics]] = {}

    for target in tg.TARGET_NAMES:
        boosters[target] = {}
        metrics[target] = {}
        for kind in ("mean", "std"):
            y_full = Y_mean[target] if kind == "mean" else Y_std[target]
            # Drop NaN rows from each split independently. With
            # default split this only affects recovery_time_if_recovered
            # (rows where no seed recovered).
            def _slice(idx):
                yi = y_full[idx]
                mask = np.isfinite(yi)
                return X[idx][mask], yi[mask]
            X_tr_t, y_tr_t = _slice(train_idx)
            X_val_t, y_val_t = _slice(val_idx)
            X_te_t,  y_te_t  = _slice(test_idx)
            if len(y_tr_t) < 50 or len(y_te_t) < 5:
                # Not enough finite rows to fit / evaluate. Skip silently
                # but note in metrics.
                metrics[target][kind] = TargetMetrics(
                    rmse=float("nan"), mae=float("nan"),
                    mape=float("nan"), r2=float("nan"),
                    n_train=int(len(y_tr_t)), n_val=int(len(y_val_t)),
                    n_test=int(len(y_te_t)), best_iteration=0,
                )
                continue
            booster, m = train_one_target(
                X_tr_t, y_tr_t, X_val_t, y_val_t, X_te_t, y_te_t,
                feature_names=feature_names, params=params,
            )
            boosters[target][kind] = booster
            metrics[target][kind] = m
            print(
                f"  [{mineral}/{target:<32s}/{kind:<4s}] "
                f"rmse={m.rmse:>10.3f}  mae={m.mae:>10.3f}  "
                f"mape={m.mape:>6.2f}%  r2={m.r2:>+0.3f}  "
                f"iters={m.best_iteration}  N_te={m.n_test}"
            )

    return MineralModelBundle(
        mineral=mineral,
        feature_names=feature_names,
        target_names=list(tg.TARGET_NAMES),
        boosters=boosters,
        metrics=metrics,
        train_test_split_seed=seed,
        n_rows=int(len(df)),
        ensemble=True,
        seeds_per_scenario=seeds_per_scenario,
    )


def save_bundle(bundle: MineralModelBundle, out_path: Path) -> None:
    """Persist a trained bundle to disk + write a side-car JSON of metrics."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        pickle.dump(bundle, f)
    side_car = out_path.with_suffix(".metrics.json")

    # Layout is target -> TargetMetrics  (single-seed)
    #             or target -> {'mean': TargetMetrics, 'std': TargetMetrics}
    if bundle.ensemble:
        target_metrics = {
            name: {kind: asdict(m) for kind, m in d.items()}
            for name, d in bundle.metrics.items()
        }
    else:
        target_metrics = {name: asdict(m) for name, m in bundle.metrics.items()}

    metrics_payload = {
        "mineral": bundle.mineral,
        "n_rows": bundle.n_rows,
        "ensemble": bundle.ensemble,
        "seeds_per_scenario": bundle.seeds_per_scenario,
        "train_test_split_seed": bundle.train_test_split_seed,
        "feature_dim": len(bundle.feature_names),
        "target_metrics": target_metrics,
    }
    with side_car.open("w") as f:
        json.dump(metrics_payload, f, indent=2)


def load_bundle(path: Path) -> MineralModelBundle:
    with path.open("rb") as f:
        return pickle.load(f)
