#!/usr/bin/env python3
"""Stage 3 paradox-inversion visualisation.

Reads the Stage 3 summary.csv + the scenarios JSON, joins on the
``index`` column to recover each scenario's ``cell`` dict
(``substitution_trigger_steps``, ``max_substitution``,
``price_spread``), and plots:

  1. For each fixed embargo_duration in a configurable slice list,
     a 4 x 4 grid of heatmaps (substitution_trigger_steps rows ×
     max_substitution columns), each heatmap coloured by mean
     cumulative_unfulfilled with price_spread on the x-axis.

  2. The 'paradox curve': for each price_spread value, plot
     cumulative_unfulfilled vs max_substitution holding
     substitution_trigger_steps fixed at the baseline (6). If the
     paradox inverts as price_spread rises, the curves should rotate
     from monotonic-increasing (at spread=0) toward
     monotonic-decreasing.

Usage:
    python scripts/plot_stage3_factorial.py \
        --summary  runs/sweep_mpi_<jobid>/summary.csv \
        --scenarios scenarios/sweep_stage3.json \
        --outdir   runs/sweep_mpi_<jobid>/figures_stage3
"""
import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--summary",   required=True)
    p.add_argument("--scenarios", required=True)
    p.add_argument("--outdir",    required=True)
    p.add_argument(
        "--embargo-slices", type=int, nargs="+",
        default=[52, 312, 832, 1040],
        help="Embargo durations (steps) to plot heatmap grids for.",
    )
    p.add_argument(
        "--kpi", default="cumulative_unfulfilled",
        help="KPI column from summary.csv to plot.",
    )
    args = p.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # Load summary + scenarios; the scenarios JSON carries the per-row
    # cell dict that summary.csv does not. We join on the row index.
    df = pd.read_csv(args.summary)
    with open(args.scenarios) as f:
        sc = json.load(f)
    cells = pd.DataFrame([
        {"index": i, **(s.get("cell") or {})}
        for i, s in enumerate(sc)
    ])
    df = df.merge(cells, on="index", validate="one_to_one")

    cell_cols = ["substitution_trigger_steps", "max_substitution", "price_spread"]
    for c in cell_cols:
        if c not in df.columns:
            raise SystemExit(
                f"Missing column '{c}' after join -- did the scenarios "
                f"JSON have a 'cell' dict on each row?"
            )

    # Group on (cell, embargo_duration) to get the 20-seed mean.
    cell = (
        df.groupby(["substitution_trigger_steps", "max_substitution",
                    "price_spread", "embargo_duration"])
          [args.kpi].mean().reset_index()
    )
    print(f"Loaded {len(df)} rows; collapsed to {len(cell)} cell-mean rows.")

    # ---- Plot 1: heatmap grid per embargo slice ---------------------------
    sub_trig_vals = sorted(cell.substitution_trigger_steps.unique())
    max_sub_vals  = sorted(cell.max_substitution.unique())
    spread_vals   = sorted(cell.price_spread.unique())

    for ed in args.embargo_slices:
        slc = cell[cell.embargo_duration == ed]
        if slc.empty:
            print(f"WARN: no rows for embargo_duration={ed}, skipping")
            continue
        vmin, vmax = slc[args.kpi].quantile([0.02, 0.98])
        nrows, ncols = len(sub_trig_vals), len(max_sub_vals)
        fig, axes = plt.subplots(nrows, ncols,
                                 figsize=(2.8 * ncols, 2.4 * nrows),
                                 sharex=True, sharey=True, squeeze=False)
        fig.suptitle(
            f"Stage 3 paradox-inversion surface — {args.kpi}\n"
            f"embargo_duration = {ed} wk   (mean across 20 seeds)\n"
            f"rows: substitution_trigger_steps   cols: max_substitution",
            fontsize=11, y=1.02,
        )
        for i, st in enumerate(sub_trig_vals):
            for j, ms in enumerate(max_sub_vals):
                ax = axes[i][j]
                sub = (slc[(slc.substitution_trigger_steps == st) &
                           (slc.max_substitution == ms)]
                          .sort_values("price_spread"))
                ax.plot(sub.price_spread, sub[args.kpi],
                        "o-", color="C0", lw=1.5)
                ax.axhline(0, color="grey", lw=0.5)
                ax.set_ylim(vmin, vmax)
                ax.set_xticks(spread_vals)
                ax.tick_params(labelsize=7)
                if i == 0:
                    ax.set_title(f"max_sub={ms:g}", fontsize=8)
                if j == 0:
                    ax.set_ylabel(f"trig={st}", fontsize=8)
                if i == nrows - 1:
                    ax.set_xlabel("price_spread", fontsize=8)
                ax.grid(alpha=0.3)
        fig.tight_layout()
        out = outdir / f"stage3_grid_ed{ed:04d}.png"
        fig.savefig(out, dpi=140, bbox_inches="tight")
        plt.close(fig)
        print(f"wrote {out}")

    # ---- Plot 2: paradox curves at the long-embargo extreme ---------------
    ed = max(args.embargo_slices)
    base_st = 6 if 6 in sub_trig_vals else sub_trig_vals[0]
    slc = cell[(cell.embargo_duration == ed) &
               (cell.substitution_trigger_steps == base_st)]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    cmap = plt.cm.coolwarm(np.linspace(0, 1, len(spread_vals)))
    for s, c in zip(spread_vals, cmap):
        cur = slc[slc.price_spread == s].sort_values("max_substitution")
        ax.plot(cur.max_substitution, cur[args.kpi],
                "o-", lw=1.5, color=c, label=f"price_spread={s:g}")
    ax.set_xlabel("max_substitution")
    ax.set_ylabel(args.kpi + "  (mean across 20 seeds)")
    ax.set_title(f"Paradox curve at embargo_duration={ed} wk, "
                 f"substitution_trigger_steps={base_st}\n"
                 f"(if curves rotate from + slope at spread=0 to "
                 f"~0 / − slope at spread=1, the paradox is inverting)")
    ax.legend(fontsize=8, loc="best")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    out = outdir / f"stage3_paradox_curves_ed{ed:04d}.png"
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")

    # ---- Plot 3: scalar metric per spread -- the "inversion size" ---------
    # gap = mean_KPI(max_sub=hi) - mean_KPI(max_sub=lo) at baseline trigger
    lo, hi = min(max_sub_vals), max(max_sub_vals)
    rows = []
    for ed_v in sorted(cell.embargo_duration.unique()):
        slc = cell[(cell.embargo_duration == ed_v) &
                   (cell.substitution_trigger_steps == base_st)]
        for s in spread_vals:
            sub = slc[slc.price_spread == s]
            try:
                gap = (float(sub[sub.max_substitution == hi][args.kpi].iloc[0])
                       - float(sub[sub.max_substitution == lo][args.kpi].iloc[0]))
            except IndexError:
                continue
            rows.append({"embargo_duration": ed_v, "price_spread": s, "gap": gap})
    gap_df = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for s, c in zip(spread_vals, cmap):
        cur = gap_df[gap_df.price_spread == s].sort_values("embargo_duration")
        ax.plot(cur.embargo_duration, cur.gap, "o-", color=c,
                label=f"spread={s:g}")
    ax.axhline(0, color="grey", lw=0.7)
    ax.set_xlabel("embargo_duration (wk)")
    ax.set_ylabel(f"{args.kpi} gap: max_sub={hi} − max_sub={lo}")
    ax.set_title("Substitution-paradox magnitude vs price_spread\n"
                 "(positive gap = paradox active; gap → 0 or < 0 means inversion)")
    ax.legend(fontsize=8, loc="best")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    out = outdir / "stage3_paradox_gap_vs_spread.png"
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")

    # Print a small table of the gap values for quick verification.
    table = gap_df.pivot(index="embargo_duration", columns="price_spread",
                         values="gap")
    print("\nGap (cumulative_unfulfilled, max_sub=hi − max_sub=lo) by "
          "(embargo_duration, price_spread):\n")
    print(table.applymap(lambda v: f"{v:+,.0f}").to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
