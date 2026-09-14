"""Conformalized Quantile Regression (CQR) surrogates.

Three LightGBM boosters per target -- ``q_lo``, ``q_med``, ``q_hi`` --
trained with LightGBM's native ``objective=quantile`` plus a held-out
calibration set that gives a conformal offset. The resulting interval
``[q_lo - Q, q_hi + Q]`` carries a distribution-free marginal-coverage
guarantee:

    P(y_true in [q_lo(x) - Q,  q_hi(x) + Q]) >= 1 - alpha

independent of how well or badly the underlying quantile regressors are
calibrated on their own (Romano, Patterson, Candes 2019).

Why this lives next to the Phase-2 mean+std bundle:

* The mean+std bundle predicts a *point* of central tendency plus a
  *point* estimate of seed-to-seed variance. It does not give a
  predictive distribution and is not coverage-calibrated.
* CQR gives shape-aware (asymmetric where the response is asymmetric)
  intervals at modest extra training cost and works on top of the same
  per-mineral ensemble parquet.

The bundle layout mirrors :class:`train_scalar.MineralModelBundle`
closely enough that ``predict.predict`` can dispatch on bundle type
and emit ``<target>_lo`` / ``<target>_med`` / ``<target>_hi`` for
quantile bundles, ``<target>_mean`` / ``<target>_std`` for the
ensemble bundle, or just ``<target>`` for the Phase-1 single-seed
bundle.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import lightgbm as lgb

from . import features as ft
from . import targets as tg
from . import dataset as ds


#: Default LightGBM hyperparameters for the quantile path. Mirror
#: train_scalar.DEFAULT_PARAMS but drop ``objective`` / ``metric``
#: since those are set per-quantile.
DEFAULT_PARAMS: dict[str, Any] = {
    "learning_rate":    0.05,
    "num_leaves":       63,
    "min_data_in_leaf": 10,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.9,
    "bagging_freq":     5,
    "lambda_l2":        0.1,
    "verbose":          -1,
}
DEFAULT_NUM_BOOST_ROUND = 2000
DEFAULT_EARLY_STOPPING_ROUNDS = 50

#: Targets we know are non-negative in nature. The conformal offset is
#: applied symmetrically, which can drag a lower bound below zero on
#: rows near the floor; we clip those at 0 in :func:`predict_quantile`.
NONNEGATIVE_TARGETS: frozenset[str] = frozenset({
    "unfulfilled_fraction_in_window",
    "recovered",
    "recovery_time_if_recovered",
})


# ---------------------------------------------------------------------------
# Per-target dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TargetQuantileMetrics:
    """Held-out test-set metrics for one quantile-regression target."""
    target: str
    alpha: float
    n_train: int
    n_val: int
    n_cal: int
    n_test: int
    conformal_offset: float
    raw_coverage: float
    conformal_coverage: float
    mean_width_raw: float
    mean_width_conformal: float
    median_rmse: float
    median_mae: float
    width_to_range_ratio: float


@dataclass
class MineralQuantileBundle:
    """Per-mineral quantile-regression bundle.

    Layout:
        boosters[target]   = {'q_lo': Booster, 'q_med': Booster, 'q_hi': Booster}
                             (or empty dict if too few finite rows; predict
                             then emits NaN for that target's lo/med/hi).
        conformal[target]  = float (the additive correction Q for that
                             target; predicted interval is
                             [q_lo(x) - Q, q_hi(x) + Q]).
        metrics[target]    = TargetQuantileMetrics.
    """
    mineral: str
    feature_names: list[str]
    target_names: list[str]
    alpha: float
    boosters: dict[str, dict[str, lgb.Booster]]
    conformal: dict[str, float]
    metrics: dict[str, TargetQuantileMetrics]
    n_rows: int
    seed: int


# ---------------------------------------------------------------------------
# Splits
# ---------------------------------------------------------------------------

def split_4way(
    n: int, test_frac: float = 0.10, val_frac: float = 0.10,
    cal_frac: float = 0.10, seed: int = 0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Random 4-way split. Returns sorted (train, val, cal, test) indices."""
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    n_test = int(round(test_frac * n))
    n_val = int(round(val_frac * n))
    n_cal = int(round(cal_frac * n))
    test = np.sort(idx[:n_test])
    val = np.sort(idx[n_test:n_test + n_val])
    cal = np.sort(idx[n_test + n_val:n_test + n_val + n_cal])
    train = np.sort(idx[n_test + n_val + n_cal:])
    return train, val, cal, test


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def _train_quantile_booster(
    X_tr: np.ndarray, y_tr: np.ndarray,
    X_val: np.ndarray, y_val: np.ndarray,
    feature_names: list[str], q: float,
    params: dict | None = None,
    num_boost_round: int = DEFAULT_NUM_BOOST_ROUND,
    early_stopping_rounds: int = DEFAULT_EARLY_STOPPING_ROUNDS,
) -> lgb.Booster:
    p = dict(DEFAULT_PARAMS)
    p.update({"objective": "quantile", "alpha": q, "metric": "quantile"})
    if params:
        p.update(params)
    train_set = lgb.Dataset(X_tr, label=y_tr, feature_name=feature_names)
    val_set = lgb.Dataset(
        X_val, label=y_val, reference=train_set, feature_name=feature_names,
    )
    return lgb.train(
        p, train_set,
        num_boost_round=num_boost_round,
        valid_sets=[val_set], valid_names=["val"],
        callbacks=[
            lgb.early_stopping(
                stopping_rounds=early_stopping_rounds, verbose=False,
            ),
            lgb.log_evaluation(0),
        ],
    )


def train_one_target_quantile(
    X: np.ndarray, y: np.ndarray,
    train_idx: np.ndarray, val_idx: np.ndarray,
    cal_idx: np.ndarray, test_idx: np.ndarray,
    feature_names: list[str],
    target: str, alpha: float,
    params: dict | None = None,
) -> tuple[dict[str, lgb.Booster], float, TargetQuantileMetrics]:
    """Fit q_lo / q_med / q_hi + conformal offset for one target.

    Drops NaN-y rows from each split independently so a target like
    ``recovery_time_if_recovered`` (NaN where no seed recovered) only
    trains on rows with a finite mean.
    """
    def _slice(idx):
        yi = y[idx]
        mask = np.isfinite(yi)
        return X[idx][mask], yi[mask]

    X_tr, y_tr = _slice(train_idx)
    X_val, y_val = _slice(val_idx)
    X_cal, y_cal = _slice(cal_idx)
    X_te, y_te = _slice(test_idx)

    if len(y_tr) < 50 or len(y_cal) < 30 or len(y_te) < 5:
        return {}, float("nan"), TargetQuantileMetrics(
            target=target, alpha=alpha,
            n_train=len(y_tr), n_val=len(y_val),
            n_cal=len(y_cal), n_test=len(y_te),
            conformal_offset=float("nan"),
            raw_coverage=float("nan"), conformal_coverage=float("nan"),
            mean_width_raw=float("nan"), mean_width_conformal=float("nan"),
            median_rmse=float("nan"), median_mae=float("nan"),
            width_to_range_ratio=float("nan"),
        )

    boosters: dict[str, lgb.Booster] = {}
    for q_name, q in [("q_lo", alpha / 2), ("q_med", 0.5),
                      ("q_hi", 1 - alpha / 2)]:
        boosters[q_name] = _train_quantile_booster(
            X_tr, y_tr, X_val, y_val, feature_names, q, params=params,
        )

    def _predict(b: lgb.Booster, X_in: np.ndarray) -> np.ndarray:
        return b.predict(X_in, num_iteration=b.best_iteration)

    pred_lo_cal = _predict(boosters["q_lo"], X_cal)
    pred_hi_cal = _predict(boosters["q_hi"], X_cal)
    scores = np.maximum(pred_lo_cal - y_cal, y_cal - pred_hi_cal)
    n_cal = len(scores)
    # Romano-Patterson-Candes finite-sample correction for marginal cov.
    q_level = min(1.0, np.ceil((1 - alpha) * (n_cal + 1)) / n_cal)
    Q = float(np.quantile(scores, q_level))

    pred_lo_te = _predict(boosters["q_lo"], X_te)
    pred_med_te = _predict(boosters["q_med"], X_te)
    pred_hi_te = _predict(boosters["q_hi"], X_te)
    lo_adj = pred_lo_te - Q
    hi_adj = pred_hi_te + Q

    raw_cov = float(np.mean((y_te >= pred_lo_te) & (y_te <= pred_hi_te)))
    conf_cov = float(np.mean((y_te >= lo_adj) & (y_te <= hi_adj)))
    width_raw = float(np.mean(pred_hi_te - pred_lo_te))
    width_adj = float(np.mean(hi_adj - lo_adj))
    median_rmse = float(np.sqrt(np.mean((pred_med_te - y_te) ** 2)))
    median_mae = float(np.mean(np.abs(pred_med_te - y_te)))
    y_range = float(np.max(y_te) - np.min(y_te))
    width_ratio = width_adj / y_range if y_range > 0 else float("nan")

    metrics = TargetQuantileMetrics(
        target=target, alpha=alpha,
        n_train=len(y_tr), n_val=len(y_val),
        n_cal=n_cal, n_test=len(y_te),
        conformal_offset=Q,
        raw_coverage=raw_cov, conformal_coverage=conf_cov,
        mean_width_raw=width_raw, mean_width_conformal=width_adj,
        median_rmse=median_rmse, median_mae=median_mae,
        width_to_range_ratio=width_ratio,
    )
    return boosters, Q, metrics


def train_mineral_quantile(
    parquet_path: Path, mineral: str,
    alpha: float = 0.10,
    test_frac: float = 0.10, val_frac: float = 0.10, cal_frac: float = 0.10,
    seed: int = 0,
    params: dict | None = None,
) -> MineralQuantileBundle:
    """Load an ensemble parquet and fit q_lo / q_med / q_hi + conformal Q."""
    df = pd.read_parquet(parquet_path)
    if df.empty:
        raise ValueError(f"{parquet_path} is empty")

    X, Y_mean, _Y_std, feature_names = ds.split_xy_ensemble(df, mineral)
    train_idx, val_idx, cal_idx, test_idx = split_4way(
        len(df), test_frac=test_frac, val_frac=val_frac,
        cal_frac=cal_frac, seed=seed,
    )

    boosters: dict[str, dict[str, lgb.Booster]] = {}
    conformal: dict[str, float] = {}
    metrics: dict[str, TargetQuantileMetrics] = {}
    for target in tg.TARGET_NAMES:
        y = Y_mean[target]
        bset, Q, m = train_one_target_quantile(
            X, y, train_idx, val_idx, cal_idx, test_idx,
            feature_names=feature_names, target=target, alpha=alpha,
            params=params,
        )
        boosters[target] = bset
        conformal[target] = Q
        metrics[target] = m
        print(
            f"  [{mineral}/{target:<32s}] "
            f"raw_cov={m.raw_coverage:>5.3f}  "
            f"conformal_cov={m.conformal_coverage:>5.3f}  "
            f"width_ratio={m.width_to_range_ratio:>5.3f}  "
            f"med_rmse={m.median_rmse:>10.3f}  "
            f"N_te={m.n_test}"
        )

    return MineralQuantileBundle(
        mineral=mineral,
        feature_names=feature_names,
        target_names=list(tg.TARGET_NAMES),
        alpha=alpha,
        boosters=boosters,
        conformal=conformal,
        metrics=metrics,
        n_rows=int(len(df)),
        seed=seed,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_bundle(bundle: MineralQuantileBundle, out_path: Path) -> None:
    """Pickle a quantile bundle and write a side-car JSON of metrics."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        pickle.dump(bundle, f)
    side_car = out_path.with_suffix(".metrics.json")
    payload = {
        "mineral": bundle.mineral,
        "alpha": bundle.alpha,
        "n_rows": bundle.n_rows,
        "seed": bundle.seed,
        "feature_dim": len(bundle.feature_names),
        "target_metrics": {t: asdict(m) for t, m in bundle.metrics.items()},
    }
    with side_car.open("w") as f:
        json.dump(payload, f, indent=2)


def load_bundle(path: Path) -> MineralQuantileBundle:
    with path.open("rb") as f:
        return pickle.load(f)


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def predict_quantile(
    bundle: MineralQuantileBundle, scenario: dict,
    n_steps: int = ft.DEFAULT_N_STEPS,
) -> dict[str, Any]:
    """Per-target ``[lo, med, hi]`` interval, conformal-corrected.

    Returns one float per ``<target>_lo`` / ``<target>_med`` /
    ``<target>_hi`` plus ``_warnings`` from
    :func:`features.support_check`. For known-non-negative targets
    (``recovered``, ``recovery_time_if_recovered``,
    ``unfulfilled_fraction_in_window``) the lower bound is clipped at
    zero -- the conformal offset is symmetric and can otherwise drag
    rows near the floor below zero. ``recovered`` upper bound is also
    clipped at 1.0 since it's a probability.
    """
    expected = ft.feature_names(bundle.mineral)
    if expected != bundle.feature_names:
        raise ValueError(
            f"Feature schema mismatch for mineral={bundle.mineral}; "
            f"the loaded bundle was trained on a different version of "
            f"surrogate.features. Retrain or downgrade the bundle."
        )
    X = ft.encode(scenario, n_steps=n_steps).reshape(1, -1)
    out: dict[str, Any] = {}
    for target, bset in bundle.boosters.items():
        lo_key = f"{target}_lo"
        med_key = f"{target}_med"
        hi_key = f"{target}_hi"
        if not bset:
            out[lo_key] = float("nan")
            out[med_key] = float("nan")
            out[hi_key] = float("nan")
            continue
        Q = bundle.conformal[target]
        pred_lo = float(
            bset["q_lo"].predict(X, num_iteration=bset["q_lo"].best_iteration)[0]
        )
        pred_med = float(
            bset["q_med"].predict(X, num_iteration=bset["q_med"].best_iteration)[0]
        )
        pred_hi = float(
            bset["q_hi"].predict(X, num_iteration=bset["q_hi"].best_iteration)[0]
        )
        lo = pred_lo - Q
        hi = pred_hi + Q
        if target in NONNEGATIVE_TARGETS:
            lo = max(0.0, lo)
            pred_med = max(0.0, pred_med)
        if target == "recovered":
            hi = min(1.0, hi)
        out[lo_key] = lo
        out[med_key] = pred_med
        out[hi_key] = hi
    out["_warnings"] = ft.support_check(scenario)
    return out
