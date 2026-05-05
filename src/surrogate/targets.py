"""Scalar targets the surrogate predicts.

For each scenario we pick a "shock window" -- the steps where the
embargo / chokepoint disruption is active, plus a 24-week recovery
tail. All targets are computed inside this window. For baseline
scenarios with no events, we fall back to a fixed mid-simulation
window so the surrogate has consistent training labels.

The five targets (post-Phase-2 reframing):

  ``mean_price_in_window``           mean Global_Price over the window ($/t)
  ``peak_price``                     max Global_Price over the window ($/t)
  ``unfulfilled_fraction_in_window`` sum(unfulfilled) / sum(fulfilled +
                                     unfulfilled), as a fraction in [0, 1]
  ``recovered``                      1.0 if price returned to within 5% of
                                     the pre-event mean before the run
                                     ended; 0.0 otherwise. Trained as a
                                     classification (or rate-regression
                                     when aggregated across seeds).
  ``recovery_time_if_recovered``     If recovered: number of steps after
                                     the last event ended before recovery.
                                     If not recovered: NaN. Aggregating
                                     across an ensemble takes the mean
                                     over the recovered subset only;
                                     the ``recovered`` rate captures the
                                     "what fraction of seeds recovered"
                                     dimension separately.

Phase-1 targets that were dropped:

  ``delta_pct_vs_baseline`` (was ratio (in_window - pre_window) / pre_window
  * 100) -- straddled zero and inflated MAPE without adding signal beyond
  what ``mean_price_in_window`` already provides. Inference callers that
  want the percent shift can compute it from mean_price + a separately-
  computed pre-event baseline.

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
#: used as the reference for the recovery threshold check.
_PRE_EVENT_LOOKBACK = 26

#: Fraction of pre-event mean defining "recovered" for the recovery
#: target. Price returns within +/- this fraction => recovered.
_RECOVERY_TOLERANCE = 0.05

#: Legacy Phase-1 sentinel ("did not recover before run ended").
#: Phase-2 splits recovery into a binary ``recovered`` flag plus a
#: conditional ``recovery_time_if_recovered`` regression target,
#: making this constant unused by the current target schema.
RECOVERY_NEVER: int = -1


#: Names of the scalar targets the surrogate learns, in stable order.
#: Used by ``train_scalar.py`` to fit one model per target. The
#: ``recovered`` target is a probability / fraction in [0, 1]; the
#: others are real-valued.
TARGET_NAMES: list[str] = [
    "mean_price_in_window",
    "peak_price",
    "unfulfilled_fraction_in_window",
    "recovered",
    "recovery_time_if_recovered",
]


#: Subset of TARGET_NAMES that are real-valued regression targets the
#: surrogate predicts mean + std for (across-seed ensemble).
REGRESSION_TARGETS: list[str] = [
    "mean_price_in_window",
    "peak_price",
    "unfulfilled_fraction_in_window",
    "recovery_time_if_recovered",
]


#: Subset of TARGET_NAMES that are bounded-rate targets (in [0, 1]).
#: For these the per-seed value is 0.0 / 1.0 and the cross-seed mean
#: is the recovery rate -- the surrogate's mean prediction on these
#: is the predicted probability.
RATE_TARGETS: list[str] = [
    "recovered",
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

    Used by ``extract_targets`` as the reference for the recovery
    threshold check (price within +/-5% of this window's mean ==
    recovered).

    For baseline scenarios with no events, returns a window of equal
    length placed just before scenario_window so the recovery test
    still has a sensible denominator. Intentionally NOT the same as
    scenario_window -- we want the *pre-event* state to serve as the
    reference, not the in-shock state.
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
    else:
        mean_pre = float("nan")

    # Recovery: did the price return to within +/- _RECOVERY_TOLERANCE
    # of the pre-event mean before the run ended?
    #   recovered = 1.0 if yes, 0.0 if no
    #   recovery_time_if_recovered = steps after last event end (or NaN
    #     when not recovered; aggregation across seeds takes the mean
    #     over the recovered subset only)
    # Baseline scenarios with no events are flagged "recovered=1.0,
    # time=0" by convention so the targets stay finite.
    last_end = _last_event_end(scenario)
    if last_end is None or not np.isfinite(mean_pre) or mean_pre <= 0:
        recovered = 1.0
        rec_time = 0.0
    else:
        tail = _safe_segment(df, last_end, len(df), "Global_Price")
        recovered_idx = np.where(
            np.abs(tail - mean_pre) / mean_pre < _RECOVERY_TOLERANCE
        )[0]
        if recovered_idx.size > 0:
            recovered = 1.0
            rec_time = float(recovered_idx[0])
        else:
            recovered = 0.0
            rec_time = float("nan")

    # Unfulfilled fraction: integrate over the same window.
    fulfilled = _safe_segment(df, win_lo, win_hi, "Fulfilled_Demand_Units")
    unfulfilled = _safe_segment(df, win_lo, win_hi, "Unfulfilled_Demand_Units")
    total = float(fulfilled.sum() + unfulfilled.sum())
    unfulfilled_frac = (
        float(unfulfilled.sum() / total) if total > 0 else 0.0
    )

    return {
        "mean_price_in_window":           mean_in,
        "peak_price":                     peak_in,
        "unfulfilled_fraction_in_window": unfulfilled_frac,
        "recovered":                      recovered,
        "recovery_time_if_recovered":     rec_time,
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
