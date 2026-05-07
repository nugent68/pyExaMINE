"""Evaluation rubric for the DeepONet trajectory surrogate.

Three lenses on what the trajectory model is doing:

1. **Trajectory-level point error.**  RMSE / MAE / relative RMSE
   between predicted and true ``Global_Price`` arrays, averaged over
   all (test-set scenario, timestep) pairs.  This is what
   ``train_trajectory.py`` already reports.

2. **Derived-scalar agreement vs the GBT scalar surrogate.**  We
   compute the four Global_Price-derived scalar targets
   (``mean_price_in_window``, ``peak_price``, ``recovered``,
   ``recovery_time_if_recovered``) from the *predicted* trajectory and
   compare to:
     * the *true* extracted scalars (apples-to-truth).
     * the *GBT scalar surrogate*'s prediction on the same scenario
       (apples-to-apples surrogate comparison).
   If the trajectory model's integrated outputs match the GBT, the
   trajectory carries the same scalar information; if not, we know
   where it's leaving signal on the floor.  ``unfulfilled_fraction``
   is excluded -- it depends on demand columns the trajectory model
   doesn't predict (yet).

3. **Event-onset alignment.**  For each scenario with at least one
   event, find the predicted peak in a window around the event start
   and compare to the true peak's location.  Reported as median /
   p90 absolute error in steps.  This is the one metric a smooth
   operator basis like DeepONet often misses on, and a strong upgrade
   signal toward the hybrid event-impulse decomposition.

The functions in this module are pure -- they take pre-loaded data
and return metric dicts.  ``scripts/evaluate_trajectory.py`` is the
glue that pulls the inputs together (bundles, scenarios, true CSVs)
and prints / serializes the report.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from src.surrogate import features as ft
from src.surrogate import targets as tg


#: Scalar targets the trajectory surrogate can derive from a pure
#: Global_Price prediction (``unfulfilled_fraction`` is excluded
#: because it depends on the demand columns).
TRAJECTORY_DERIVABLE_TARGETS: list[str] = [
    "mean_price_in_window",
    "peak_price",
    "recovered",
    "recovery_time_if_recovered",
]


# ---------------------------------------------------------------------------
# Trajectory-level metrics
# ---------------------------------------------------------------------------

@dataclass
class TrajectoryError:
    """Per-trajectory point-error stats."""
    rmse: float
    mae: float
    rel_rmse: float
    rel_mae: float


def trajectory_error(pred: np.ndarray, true: np.ndarray) -> TrajectoryError:
    """RMSE / MAE / relative metrics between two 1-D price arrays."""
    pred = np.asarray(pred, dtype=np.float64)
    true = np.asarray(true, dtype=np.float64)
    n = min(len(pred), len(true))
    pred = pred[:n]; true = true[:n]
    err = pred - true
    rmse = float(np.sqrt(np.mean(err ** 2)))
    mae = float(np.mean(np.abs(err)))
    denom = float(np.mean(np.abs(true))) or 1.0
    return TrajectoryError(
        rmse=rmse, mae=mae,
        rel_rmse=rmse / denom, rel_mae=mae / denom,
    )


# ---------------------------------------------------------------------------
# Derived-scalar agreement
# ---------------------------------------------------------------------------

def _extract_price_targets(price: np.ndarray, scenario: dict) -> dict[str, float]:
    """Subset of ``targets.extract_targets`` that needs only Global_Price.

    Builds a stub DataFrame from the price array and reuses the canonical
    target-extraction logic so window selection / recovery-tolerance /
    pre-event baseline stay in lockstep with training-time labelling.
    Returns NaN for ``unfulfilled_fraction_in_window`` since the demand
    columns aren't part of the trajectory model's output.
    """
    n = len(price)
    df = pd.DataFrame({
        "Global_Price": np.asarray(price, dtype=np.float64),
        # Stub the demand columns with zeros so the window math doesn't
        # divide by zero. We discard the unfulfilled target below.
        "Fulfilled_Demand_Units": np.zeros(n),
        "Unfulfilled_Demand_Units": np.zeros(n),
    })
    out = tg.extract_targets(df, scenario)
    out["unfulfilled_fraction_in_window"] = float("nan")
    return out


@dataclass
class ScalarAgreement:
    """Per-target agreement between two scalar streams (vs ground truth)."""
    target: str
    n: int
    rmse: float
    mae: float
    bias: float          # mean(pred - true)
    r2: float
    rel_rmse: float


def _safe_metrics(target: str, pred: np.ndarray, true: np.ndarray) -> ScalarAgreement:
    """Robust per-target metrics; skips NaN rows on either side."""
    mask = np.isfinite(pred) & np.isfinite(true)
    pred = pred[mask]; true = true[mask]
    n = len(true)
    if n == 0:
        return ScalarAgreement(target=target, n=0, rmse=float("nan"),
                               mae=float("nan"), bias=float("nan"),
                               r2=float("nan"), rel_rmse=float("nan"))
    err = pred - true
    rmse = float(np.sqrt(np.mean(err ** 2)))
    mae = float(np.mean(np.abs(err)))
    bias = float(np.mean(err))
    ss_res = float(np.sum(err ** 2))
    mean = float(np.mean(true))
    ss_tot = float(np.sum((true - mean) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    denom = float(np.mean(np.abs(true))) or 1.0
    return ScalarAgreement(
        target=target, n=n, rmse=rmse, mae=mae, bias=bias,
        r2=r2, rel_rmse=rmse / denom,
    )


# ---------------------------------------------------------------------------
# Event-onset alignment
# ---------------------------------------------------------------------------

@dataclass
class PeakAlignment:
    """Per-scenario predicted-peak-step vs true-peak-step alignment."""
    n_scenarios: int            # number of scenarios with at least one event
    median_abs_err_steps: float
    p90_abs_err_steps: float
    mean_abs_err_steps: float
    within_5_frac: float        # fraction inside +/- 5 steps
    within_10_frac: float       # fraction inside +/- 10 steps


def _peak_step(price: np.ndarray, lo: int, hi: int) -> int | None:
    """Argmax over price[lo:hi], returned as an absolute step index."""
    n = len(price)
    lo = max(0, min(n, int(lo)))
    hi = max(lo, min(n, int(hi)))
    if lo == hi:
        return None
    return int(lo + np.argmax(price[lo:hi]))


def peak_alignment(
    preds: Sequence[np.ndarray],
    truths: Sequence[np.ndarray],
    scenarios: Sequence[dict],
) -> PeakAlignment:
    """Align each scenario's predicted peak step to its true peak step.

    The comparison window is the scenario's official scenario_window
    (== union of event windows + recovery tail).  Scenarios without
    events are skipped -- their "peak" is dominated by background
    noise and not a meaningful signal.
    """
    errs: list[int] = []
    for pred, true, scen in zip(preds, truths, scenarios):
        if not (scen.get("embargoes") or scen.get("chokepoint_crises")):
            continue
        win_lo, win_hi = tg.scenario_window(scen)
        p_step = _peak_step(np.asarray(pred), win_lo, win_hi)
        t_step = _peak_step(np.asarray(true), win_lo, win_hi)
        if p_step is None or t_step is None:
            continue
        errs.append(abs(p_step - t_step))
    if not errs:
        return PeakAlignment(0, float("nan"), float("nan"),
                             float("nan"), float("nan"), float("nan"))
    arr = np.asarray(errs, dtype=np.float64)
    return PeakAlignment(
        n_scenarios=len(arr),
        median_abs_err_steps=float(np.median(arr)),
        p90_abs_err_steps=float(np.quantile(arr, 0.90)),
        mean_abs_err_steps=float(np.mean(arr)),
        within_5_frac=float(np.mean(arr <= 5)),
        within_10_frac=float(np.mean(arr <= 10)),
    )


# ---------------------------------------------------------------------------
# Top-level eval
# ---------------------------------------------------------------------------

@dataclass
class TrajectoryEvalReport:
    """Bundle of metrics computed by :func:`evaluate_bundle`."""
    mineral: str
    n_scenarios: int
    avg_trajectory_rmse: float
    avg_trajectory_rel_rmse: float
    # Predicted-trajectory-derived scalars vs *ground truth*.
    derived_vs_true: dict[str, ScalarAgreement]
    # Predicted-trajectory-derived scalars vs *GBT scalar surrogate*.
    derived_vs_gbt: dict[str, ScalarAgreement]
    # GBT scalar surrogate vs ground truth (sanity reference; the
    # numbers here should mirror the GBT bundle's published R^2).
    gbt_vs_true: dict[str, ScalarAgreement]
    peak_alignment: PeakAlignment


def evaluate_bundle(
    mineral: str,
    preds: Sequence[np.ndarray],
    truths: Sequence[np.ndarray],
    scenarios: Sequence[dict],
    gbt_predictions: Sequence[dict] | None = None,
) -> TrajectoryEvalReport:
    """Compute the full eval report for one mineral's trajectory bundle.

    Args:
        mineral: ``"lithium" | "nickel" | "platinum"``.
        preds: predicted trajectories, one ``(T,)`` array per scenario.
        truths: ground-truth trajectories from the simulator.
        scenarios: matching scenario dicts (in the schema ``ft.encode``
            consumes).
        gbt_predictions: optional list of GBT scalar dicts -- one per
            scenario, in the format ``predict.predict()`` returns
            (``<target>_mean`` keys for Phase-2 ensemble bundles).
            If ``None``, the ``derived_vs_gbt`` block of the report is
            populated with NaN-filled stubs.

    Returns:
        :class:`TrajectoryEvalReport`.
    """
    n = len(preds)
    if not (n == len(truths) == len(scenarios)):
        raise ValueError("preds, truths, scenarios must be the same length")

    # 1. Trajectory-level metrics (averaged over scenarios).
    rmses, rel_rmses = [], []
    for p, t in zip(preds, truths):
        e = trajectory_error(p, t)
        rmses.append(e.rmse); rel_rmses.append(e.rel_rmse)
    avg_rmse = float(np.mean(rmses))
    avg_rel_rmse = float(np.mean(rel_rmses))

    # 2. Derived-scalar extraction on both predicted and true trajectories.
    pred_scalars: list[dict] = [
        _extract_price_targets(p, s) for p, s in zip(preds, scenarios)
    ]
    true_scalars: list[dict] = [
        _extract_price_targets(t, s) for t, s in zip(truths, scenarios)
    ]

    derived_vs_true: dict[str, ScalarAgreement] = {}
    derived_vs_gbt: dict[str, ScalarAgreement] = {}
    gbt_vs_true: dict[str, ScalarAgreement] = {}

    for target in TRAJECTORY_DERIVABLE_TARGETS:
        pv = np.asarray([s[target] for s in pred_scalars], dtype=np.float64)
        tv = np.asarray([s[target] for s in true_scalars], dtype=np.float64)
        derived_vs_true[target] = _safe_metrics(target, pv, tv)

        if gbt_predictions is not None:
            # Phase-2 ensemble bundles emit "<target>_mean"; Phase-1
            # bundles emit "<target>" directly.  Try ensemble first.
            gv = np.asarray([
                g.get(f"{target}_mean", g.get(target, float("nan")))
                for g in gbt_predictions
            ], dtype=np.float64)
            derived_vs_gbt[target] = _safe_metrics(target, pv, gv)
            gbt_vs_true[target]    = _safe_metrics(target, gv, tv)
        else:
            stub = _safe_metrics(target,
                                 np.asarray([], dtype=np.float64),
                                 np.asarray([], dtype=np.float64))
            derived_vs_gbt[target] = stub
            gbt_vs_true[target] = stub

    # 3. Peak-onset alignment.
    pa = peak_alignment(preds, truths, scenarios)

    return TrajectoryEvalReport(
        mineral=mineral,
        n_scenarios=n,
        avg_trajectory_rmse=avg_rmse,
        avg_trajectory_rel_rmse=avg_rel_rmse,
        derived_vs_true=derived_vs_true,
        derived_vs_gbt=derived_vs_gbt,
        gbt_vs_true=gbt_vs_true,
        peak_alignment=pa,
    )


# ---------------------------------------------------------------------------
# Pretty-printing
# ---------------------------------------------------------------------------

def format_report(report: TrajectoryEvalReport) -> str:
    """Multi-line human-readable summary of a :class:`TrajectoryEvalReport`."""
    lines: list[str] = []
    lines.append(f"=== {report.mineral} (N={report.n_scenarios} test scenarios) ===")
    lines.append(
        f"trajectory: avg_rmse={report.avg_trajectory_rmse:>10.3g}  "
        f"avg_rel_rmse={report.avg_trajectory_rel_rmse:.4f}"
    )
    lines.append("")

    def _block(name: str, blk: dict[str, ScalarAgreement]) -> None:
        lines.append(f"  {name}")
        lines.append(
            f"    {'target':<32s}{'n':>5s}  {'rmse':>10s}  "
            f"{'r2':>8s}  {'rel_rmse':>10s}  {'bias':>10s}"
        )
        for t in TRAJECTORY_DERIVABLE_TARGETS:
            m = blk[t]
            lines.append(
                f"    {t:<32s}{m.n:>5d}  {m.rmse:>10.3g}  "
                f"{m.r2:>+8.3f}  {m.rel_rmse:>10.4f}  {m.bias:>+10.3g}"
            )
        lines.append("")

    _block("derived (from pred trajectory) vs ground truth", report.derived_vs_true)
    _block("derived (from pred trajectory) vs GBT scalar predictions",
           report.derived_vs_gbt)
    _block("GBT scalar predictions vs ground truth (sanity check)",
           report.gbt_vs_true)

    pa = report.peak_alignment
    lines.append(
        f"  peak_alignment ({pa.n_scenarios} event-bearing scenarios)"
    )
    lines.append(
        f"    median |err| = {pa.median_abs_err_steps:>5.1f} steps  "
        f"p90 = {pa.p90_abs_err_steps:>5.1f}  mean = {pa.mean_abs_err_steps:>5.1f}"
    )
    lines.append(
        f"    within +/-5  steps: {pa.within_5_frac:>5.1%}  "
        f"within +/-10 steps: {pa.within_10_frac:>5.1%}"
    )
    return "\n".join(lines)


def report_to_dict(report: TrajectoryEvalReport) -> dict:
    """Serializable form of a :class:`TrajectoryEvalReport`."""
    return {
        "mineral": report.mineral,
        "n_scenarios": report.n_scenarios,
        "avg_trajectory_rmse": report.avg_trajectory_rmse,
        "avg_trajectory_rel_rmse": report.avg_trajectory_rel_rmse,
        "derived_vs_true": {t: asdict(m) for t, m in report.derived_vs_true.items()},
        "derived_vs_gbt": {t: asdict(m) for t, m in report.derived_vs_gbt.items()},
        "gbt_vs_true": {t: asdict(m) for t, m in report.gbt_vs_true.items()},
        "peak_alignment": asdict(report.peak_alignment),
    }
