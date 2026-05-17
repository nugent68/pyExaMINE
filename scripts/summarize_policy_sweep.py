#!/usr/bin/env python3
"""Aggregate a US-policy sweep into a one-row-per-scenario summary CSV.

Reads the per-scenario trajectories produced by
``scripts/run_one_scenario.py`` (either CSV or HDF5) and reduces each
to a small set of policy-relevant KPIs computed over the embargo
window. The output is a single CSV with the (policy_name, seed,
mineral) factors as columns plus the KPIs, suitable for direct ingest
into pandas / matplotlib for the policy-comparison plot.

KPIs computed over the embargo window (start_step .. end_step):

  peak_price                $/tonne max of Global_Price
  mean_price                $/tonne mean of Global_Price
  peak_unfulfilled          max of Unfulfilled_Demand_Units
  mean_unfulfilled          mean of Unfulfilled_Demand_Units
  cumulative_unfulfilled    sum  of Unfulfilled_Demand_Units
  reserve_released_total    sum  of Strategic_Reserve_Released
  reserve_min_stock         min  of Strategic_Reserve_Stock
  recovery_steps            steps for price to fall back within 10%
                            of pre-embargo level after embargo ends
                            (np.nan if price never recovers in run)

Usage:
    python scripts/summarize_policy_sweep.py \
        --scenarios scenarios/policy_sweep.json \
        --runs-root $SCRATCH/policy_sweep_<jobid>/scenario_runs \
        --output summary.csv
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def _load_trajectory(run_path: Path) -> pd.DataFrame:
    if run_path.suffix == ".h5":
        import h5py
        with h5py.File(run_path, "r") as h:
            return pd.DataFrame({col: h[col][:] for col in h.keys()})
    return pd.read_csv(run_path)


def _kpis_for_scenario(scenario, trajectory):
    """Reduce one scenario's trajectory to a dict of KPIs."""
    embargoes = scenario.get("embargoes") or []
    if embargoes:
        start = int(embargoes[0]["start_step"])
        duration = int(embargoes[0]["duration"])
    else:
        # No-embargo control arm; reduce over a comparable mid-horizon
        # window so the numbers stay on the same axis as embargo runs.
        start = 312
        duration = 156

    end = start + duration
    # Clamp the window to the trajectory length so very long embargoes
    # (e.g. 1040 wk starting at step 312, with n_steps=1352) don't index
    # past the array end.
    end = min(end, len(trajectory))
    window = trajectory.iloc[start:end]

    out = {
        "peak_price":             float(window["Global_Price"].max()),
        "mean_price":             float(window["Global_Price"].mean()),
        "peak_unfulfilled":       float(window["Unfulfilled_Demand_Units"].max()),
        "mean_unfulfilled":       float(window["Unfulfilled_Demand_Units"].mean()),
        "cumulative_unfulfilled": float(window["Unfulfilled_Demand_Units"].sum()),
    }

    # Strategic reserve diagnostics may or may not be present
    # depending on whether the run had a reserve configured.
    if "Strategic_Reserve_Released" in trajectory.columns:
        out["reserve_released_total"] = float(
            trajectory["Strategic_Reserve_Released"].iloc[start:end].sum()
        )
        out["reserve_min_stock"] = float(
            trajectory["Strategic_Reserve_Stock"].iloc[start:end].min()
        )
    else:
        out["reserve_released_total"] = 0.0
        out["reserve_min_stock"] = 0.0

    # Recovery time: steps after embargo end for the price to fall
    # back within 10% of the pre-embargo (step start-1) level.
    pre_price = float(trajectory["Global_Price"].iloc[max(0, start - 1)])
    threshold = pre_price * 1.10
    post_window = trajectory["Global_Price"].iloc[end:]
    recovered = post_window <= threshold
    if recovered.any():
        out["recovery_steps"] = int(np.argmax(recovered.to_numpy()))
    else:
        out["recovery_steps"] = float("nan")

    return out


def _load_sweep_h5(sweep_h5_path: Path):
    """Open a single-file MPI sweep HDF5 and return ``(h5_file, columns)``.

    Caller is responsible for closing. ``columns`` is the meta-recorded
    list of column names (Step + per-(N,T) trajectory columns), so the
    summarizer can build per-scenario DataFrames by slicing row ``i``.
    """
    import h5py
    f = h5py.File(sweep_h5_path, "r")
    columns_attr = f["meta"].attrs.get("columns", "")
    if isinstance(columns_attr, bytes):
        columns_attr = columns_attr.decode("utf-8")
    columns = [c for c in str(columns_attr).split(",") if c]
    return f, columns


def _trajectory_from_sweep(h5_file, columns, index: int) -> pd.DataFrame:
    """Build a per-scenario DataFrame by slicing row ``index`` of the sweep."""
    data = {}
    for col in columns:
        ds = h5_file[col]
        if ds.ndim == 1:
            data[col] = ds[:]
        else:
            data[col] = ds[index, :]
    return pd.DataFrame(data)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scenarios", type=Path, required=True,
                   help="Scenario JSON used to drive the sweep.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--runs-root", type=Path,
                     help="Directory containing <mineral>/<index>.{csv,h5} "
                          "per-scenario trajectories (legacy non-MPI flow).")
    src.add_argument("--sweep-h5", type=Path,
                     help="Single-file MPI sweep HDF5 (run_sweep_mpi.py "
                          "output). Trajectory for scenario i = row i.")
    p.add_argument("--output", type=Path, required=True,
                   help="Output summary CSV path.")
    args = p.parse_args()

    with args.scenarios.open() as f:
        scenarios = json.load(f)

    sweep_h5 = None
    sweep_cols = None
    if args.sweep_h5 is not None:
        sweep_h5, sweep_cols = _load_sweep_h5(args.sweep_h5)

    rows = []
    missing = 0
    try:
        for i, scenario in enumerate(scenarios):
            mineral = scenario["mineral"]
            if sweep_h5 is not None:
                # Single-file MPI source. Skip rows that the sweep marked
                # incomplete (meta/done bitmap).
                if "meta/done" in sweep_h5 and not bool(sweep_h5["meta/done"][i]):
                    missing += 1
                    continue
                trajectory = _trajectory_from_sweep(sweep_h5, sweep_cols, i)
            else:
                h5_path = args.runs_root / mineral / f"{i:06d}.h5"
                csv_path = args.runs_root / mineral / f"{i:06d}.csv"
                if h5_path.exists():
                    trajectory = _load_trajectory(h5_path)
                elif csv_path.exists():
                    trajectory = _load_trajectory(csv_path)
                else:
                    missing += 1
                    continue

            row = {
                "index":            i,
                "policy_name":      scenario.get("policy_name", "unnamed"),
                "param_name":       scenario.get("param_name", "baseline"),
                "param_value":      scenario.get("param_value"),
                "random_seed":      int(scenario.get("random_seed", -1)),
                "mineral":          mineral,
                "embargo_duration": scenario.get("embargo_duration"),
            }
            row.update(_kpis_for_scenario(scenario, trajectory))
            rows.append(row)
    finally:
        if sweep_h5 is not None:
            sweep_h5.close()

    if missing:
        print(f"WARNING: {missing} scenario(s) had no trajectory file; "
              f"summary will be incomplete.", file=sys.stderr)

    df = pd.DataFrame(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False)
    print(f"Wrote {len(df)} rows to {args.output}")

    # Print a tidy summary for quick eyeballing. Grouping picks up
    # whatever axes were actually varied: policy_name, param_name,
    # embargo_duration. Constant axes collapse to a single group.
    if len(df):
        kpi_cols = [
            "peak_price", "mean_price",
            "peak_unfulfilled", "mean_unfulfilled", "cumulative_unfulfilled",
            "reserve_released_total", "recovery_steps",
        ]
        candidate_grouping = ["policy_name", "param_name", "embargo_duration", "mineral"]
        group_cols = [c for c in candidate_grouping
                      if c in df.columns and df[c].nunique() > 1]
        if not group_cols:
            group_cols = ["policy_name"]
        agg = df.groupby(group_cols)[kpi_cols].mean(numeric_only=True)
        with pd.option_context("display.max_columns", None,
                               "display.width", 200,
                               "display.float_format", "{:,.0f}".format):
            print()
            print(f"Mean across seeds (grouped by {group_cols}):")
            # Truncate if the table is huge (e.g. 49 params x 20 durations).
            if len(agg) > 60:
                print(agg.head(40))
                print(f"... ({len(agg)} rows total) ...")
                print(agg.tail(20))
            else:
                print(agg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
