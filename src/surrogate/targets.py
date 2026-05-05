"""Scalar targets the surrogate predicts.

For each scenario we pick a "shock window" -- the steps where the
embargo / chokepoint disruption is active, plus a 24-week recovery
tail. All five targets are computed inside this window. For baseline
scenarios with no events, we fall back to a fixed mid-simulation
window so the surrogate has consistent training labels.

The five targets:

  ``mean_price_in_window``           mean Global_Price over the window ($/t)
  ``delta_pct_vs_baseline``          (in_window_mean - pre_window_mean) /
                                     pre_window_mean * 100
  ``peak_price``                     max Global_Price over the window ($/t)
  ``recovery_time_steps``            steps after the last event ends until
                                     price returns within 5% of pre-event mean.
                                     If the run ends before recovery, this is
                                     set to ``n_steps`` (the simulation horizon)
                                     -- a numeric upper bound rather than a
                                     ``-1`` sentinel, which makes the column
                                     well-behaved as a regression target.
                                     Callers who want to know "did it recover?"
                                     should test ``recovery_time_steps < n_steps``.
  ``unfulfilled_fraction_in_window`` sum(unfulfilled) / sum(fulfilled +
                                     unfulfilled), as a fraction in [0, 1]

These are deliberately self-consistent: each scenario provides its own
pre-event baseline (so we don't need a paired no-shock run), and the
window is determined entirely from the scenario spec (so two runs of
the same scenario with different seeds use the same window).
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


#: Fixed mid-simulation window for scenarios with no events. Centered
#: at horizon/2 with a 52-week half-width.
_BASELINE_WINDOW_HALFWIDTH = 26

#: Tail added after the last event ends so the window captures the
#: recovery dynamics, not just the shock itself.
_WINDOW_RECOVERY_TAIL = 24

#: Pre-event baseline window: the 26 steps before the first event,
#: used as the reference for delta_pct_vs_baseline and recovery_time_steps.
_PRE_EVENT_LOOKBACK = 26

#: Fraction of pre-event mean defining "recovered" for recovery_time_steps.
_RECOVERY_TOLERANCE = 0.05

#: Legacy sentinel for "did not recover before run ended". Retained as a
#: named constant for any downstream code that may still test against it,
#: but ``extract_targets`` no longer emits it -- unrecovered runs now get
#: ``n_steps`` as a numeric cap so the surrogate's regression target
#: stays well-behaved.
RECOVERY_NEVER: int = -1


#: Names of the five scalar targets, in stable order. Used by
#: ``train_scalar.py`` to fit one model per target.
TARGET_NAMES: list[str] = [
    "mean_price_in_window",
    "delta_pct_vs_baseline",
    "peak_price",
    "recovery_time_steps",
    "unfulfilled_fraction_in_window",
]


# ---------------------------------------------------------------------------
# Window selection
# ---------------------------------------------------------------------------

def scenario_window(scenario: dict) -> tuple[int, int]:
    """Return (window_start, window_end) for this scenario's targets.

    With events: window covers from the earliest event start to the
    latest event end + ``_WINDOW_RECOVERY_TAIL`` steps. With no events:
    a fixed 52-step window centered at the simulation midpoint, so
    baseline scenarios still have a meaningful mean / peak.
    """
    events: list[tuple[int, int]] = []
    for e in scenario.get("embargoes", []) or []:
        events.append((int(e["start_step"]), int(e["duration"])))
    for c in scenario.get("chokepoint_crises", []) or []:
        events.append((int(c["start_step"]), int(c["duration"])))

    n_steps = int(scenario.get("n_steps", 1352))

    if not events:
        mid = n_steps // 2
        return (
            max(0, mid - _BASELINE_WINDOW_HALFWIDTH),
            min(n_steps, mid + _BASELINE_WINDOW_HALFWIDTH),
        )

    starts = [s for s, _ in events]
    ends = [s + d for s, d in events]
    return (
        max(0, min(starts)),
        min(n_steps, max(ends) + _WINDOW_RECOVERY_TAIL),
    )


def pre_event_window(scenario: dict) -> tuple[int, int]:
    """Return the 26-step window immediately before the first event.

    For baseline scenarios with no events, returns a window of equal
    length placed just before scenario_window so the "delta vs pre"
    metric still has a denominator. This is intentionally NOT the
    same as scenario_window -- we want the *pre-event* state to
    serve as the reference price.
    """
    events_starts = []
    for e in scenario.get("embargoes", []) or []:
        events_starts.append(int(e["start_step"]))
    for c in scenario.get("chokepoint_crises", []) or []:
        events_starts.append(int(c["start_step"]))

    n_steps = int(scenario.get("n_steps", 1352))
    if not events_starts:
        win_start, _ = scenario_window(scenario)
        return (max(0, win_start - _PRE_EVENT_LOOKBACK), max(0, win_start))

    first_start = min(events_starts)
    return (
        max(0, first_start - _PRE_EVENT_LOOKBACK),
        max(0, first_start),
    )


# ---------------------------------------------------------------------------
# Target extraction
# ---------------------------------------------------------------------------

def _safe_segment(
    df: pd.DataFrame, lo: int, hi: int, col: str,
) -> np.ndarray:
    """Return df[col].iloc[lo:hi] as a 1-D numpy array, clipping to bounds."""
    n = len(df)
    lo = max(0, min(n, lo))
    hi = max(lo, min(n, hi))
    if lo == hi:
        return np.empty(0, dtype=np.float64)
    return df[col].iloc[lo:hi].to_numpy(dtype=np.float64)


def _last_event_end(scenario: dict) -> int | None:
    """Return the step at which the last event finishes, or None if no events."""
    ends: list[int] = []
    for e in scenario.get("embargoes", []) or []:
        ends.append(int(e["start_step"]) + int(e["duration"]))
    for c in scenario.get("chokepoint_crises", []) or []:
        ends.append(int(c["start_step"]) + int(c["duration"]))
    return max(ends) if ends else None


def extract_targets(df: pd.DataFrame, scenario: dict) -> dict[str, float]:
    """Compute the five scalar targets from a model_data DataFrame + scenario.

    Args:
        df: full ``model_data`` DataFrame (one row per simulation step;
            columns include Global_Price, Fulfilled_Demand_Units,
            Unfulfilled_Demand_Units, etc.).
        scenario: the scenario dict that produced this DataFrame.

    Returns:
        dict keyed by ``TARGET_NAMES``. ``recovery_time_steps`` may be
        ``RECOVERY_NEVER`` (-1) if the run ended before price returned
        within tolerance.
    """
    win_lo, win_hi = scenario_window(scenario)
    pre_lo, pre_hi = pre_event_window(scenario)

    in_window_price = _safe_segment(df, win_lo, win_hi, "Global_Price")
    pre_window_price = _safe_segment(df, pre_lo, pre_hi, "Global_Price")

    if in_window_price.size == 0:
        # Pathological scenario (window outside the run). Return NaNs;
        # the dataset builder will drop these.
        return {name: float("nan") for name in TARGET_NAMES}

    mean_in = float(np.mean(in_window_price))
    peak_in = float(np.max(in_window_price))

    if pre_window_price.size > 0:
        mean_pre = float(np.mean(pre_window_price))
        delta_pct = ((mean_in - mean_pre) / mean_pre * 100.0) if mean_pre > 0 else float("nan")
    else:
        mean_pre = float("nan")
        delta_pct = float("nan")

    # Recovery time: how long after the last event end before price
    # returns within +/-5% of the pre-event mean. If the run ends before
    # the price recovers, we cap at ``n_steps`` (the simulation horizon
    # in the scenario) instead of emitting a -1 sentinel, so the column
    # stays well-behaved as a regression target. Callers can detect
    # "didn't recover" by testing ``recovery_time_steps >= n_steps``.
    last_end = _last_event_end(scenario)
    n_steps = int(scenario.get("n_steps", 1352))
    if last_end is None or not np.isfinite(mean_pre) or mean_pre <= 0:
        recovery = 0
    else:
        tail = _safe_segment(df, last_end, len(df), "Global_Price")
        recovered = np.where(np.abs(tail - mean_pre) / mean_pre < _RECOVERY_TOLERANCE)[0]
        recovery = int(recovered[0]) if recovered.size > 0 else n_steps

    # Unfulfilled fraction: integrate over the same window.
    fulfilled = _safe_segment(df, win_lo, win_hi, "Fulfilled_Demand_Units")
    unfulfilled = _safe_segment(df, win_lo, win_hi, "Unfulfilled_Demand_Units")
    total = float(fulfilled.sum() + unfulfilled.sum())
    unfulfilled_frac = (
        float(unfulfilled.sum() / total) if total > 0 else 0.0
    )

    return {
        "mean_price_in_window":           mean_in,
        "delta_pct_vs_baseline":          delta_pct,
        "peak_price":                     peak_in,
        "recovery_time_steps":            float(recovery),
        "unfulfilled_fraction_in_window": unfulfilled_frac,
    }


def extract_targets_batch(
    pairs: Iterable[tuple[pd.DataFrame, dict]],
) -> tuple[np.ndarray, list[dict]]:
    """Stack ``extract_targets`` for many (df, scenario) pairs.

    Returns:
        Y: (N, len(TARGET_NAMES)) float32 array, in TARGET_NAMES order.
        rows: per-row diagnostic dicts (same content as Y but keyed by
            target name + scenario index for debugging).
    """
    rows: list[dict] = []
    for df, scen in pairs:
        rows.append(extract_targets(df, scen))
    Y = np.array(
        [[r[name] for name in TARGET_NAMES] for r in rows],
        dtype=np.float32,
    )
    return Y, rows
