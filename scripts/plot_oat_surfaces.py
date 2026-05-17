#!/usr/bin/env python3
"""Plot OAT decision-surface heatmaps for the Stage 2 v2 sweep.

For each of the 12 OAT param_names, plot KPI = f(embargo_duration,
param_value) as a heatmap, averaged across 20 seeds. The shared
baseline cell (param_name='baseline') is included as a horizontal
reference line in each subplot.

Usage:
    python scripts/plot_oat_surfaces.py \
        --summary runs/sweep_mpi_53073849/summary.csv \
        --outdir  runs/sweep_mpi_53073849/figures
"""
import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

KPIS = [
    ("cumulative_unfulfilled",   "Cumulative unfulfilled US demand (tons)"),
    ("peak_unfulfilled",         "Peak unfulfilled US demand (tons)"),
    ("peak_price",               "Peak global Li price ($/ton)"),
    ("mean_price",               "Mean global Li price ($/ton)"),
    ("reserve_released_total",   "Total reserve released (tons)"),
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--summary", required=True)
    p.add_argument("--outdir",  required=True)
    args = p.parse_args()

    df = pd.read_csv(args.summary)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # Knobs to scan (excluding 'baseline' which is the single shared cell)
    knobs = sorted(k for k in df["param_name"].unique() if k != "baseline")
    print(f"Knobs ({len(knobs)}):", knobs)

    # Per-cell mean across seeds
    cell = (
        df.groupby(["param_name", "param_value", "embargo_duration"], dropna=False)
          .agg({k: "mean" for k, _ in KPIS})
          .reset_index()
    )

    for kpi, kpi_label in KPIS:
        ncols = 4
        nrows = int(np.ceil(len(knobs) / ncols))
        fig, axes = plt.subplots(nrows, ncols,
                                 figsize=(4 * ncols, 3.2 * nrows),
                                 squeeze=False)
        fig.suptitle(f"OAT decision surface — {kpi_label}\n"
                     f"(mean across 20 seeds)",
                     fontsize=13, y=1.00)

        # Use shared vmin/vmax across knobs so visual comparison is meaningful
        sub = cell[cell.param_name != "baseline"]
        vmin, vmax = sub[kpi].quantile([0.02, 0.98])

        for i, knob in enumerate(knobs):
            ax = axes[i // ncols][i % ncols]
            knob_df = cell[cell.param_name == knob]
            pivot = (
                knob_df.pivot(index="param_value",
                              columns="embargo_duration",
                              values=kpi)
                       .sort_index()
            )
            im = ax.imshow(pivot.values, aspect="auto", origin="lower",
                           cmap="viridis", vmin=vmin, vmax=vmax)
            ax.set_xticks(range(len(pivot.columns)))
            ax.set_xticklabels(pivot.columns, rotation=70, fontsize=7)
            ax.set_yticks(range(len(pivot.index)))
            ax.set_yticklabels([f"{v:g}" for v in pivot.index], fontsize=8)
            ax.set_xlabel("embargo duration (wk)", fontsize=8)
            ax.set_ylabel("param value",            fontsize=8)
            ax.set_title(knob, fontsize=9)
            fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02)

        # hide any unused axes
        for j in range(len(knobs), nrows * ncols):
            axes[j // ncols][j % ncols].set_visible(False)

        fig.tight_layout()
        out = outdir / f"oat_{kpi}.png"
        fig.savefig(out, dpi=140, bbox_inches="tight")
        plt.close(fig)
        print(f"wrote {out}")

    # Companion plot: line-charts of KPI vs embargo_duration, one line per
    # param_value for each knob (easier to read than heatmap for
    # monotonicity checks).
    for kpi, kpi_label in KPIS:
        ncols = 4
        nrows = int(np.ceil(len(knobs) / ncols))
        fig, axes = plt.subplots(nrows, ncols,
                                 figsize=(4 * ncols, 3.2 * nrows),
                                 squeeze=False)
        fig.suptitle(f"OAT fan plot — {kpi_label}\n"
                     f"(mean across 20 seeds)",
                     fontsize=13, y=1.00)

        # Baseline reference line
        baseline_curve = (
            cell[cell.param_name == "baseline"]
                .set_index("embargo_duration")[kpi]
                .sort_index()
        )

        for i, knob in enumerate(knobs):
            ax = axes[i // ncols][i % ncols]
            knob_df = cell[cell.param_name == knob]
            param_values = sorted(knob_df.param_value.dropna().unique())
            cmap = plt.cm.coolwarm(np.linspace(0, 1, len(param_values)))
            for pv, c in zip(param_values, cmap):
                curve = knob_df[knob_df.param_value == pv]
                curve = curve.sort_values("embargo_duration")
                ax.plot(curve.embargo_duration, curve[kpi],
                        marker="o", ms=3, lw=1.0, color=c,
                        label=f"{pv:g}")
            ax.plot(baseline_curve.index, baseline_curve.values,
                    "k--", lw=1.2, label="baseline")
            ax.set_xlabel("embargo duration (wk)", fontsize=8)
            ax.set_title(knob, fontsize=9)
            ax.tick_params(labelsize=7)
            ax.legend(fontsize=6, loc="best", ncol=2)
            ax.grid(alpha=0.3)

        for j in range(len(knobs), nrows * ncols):
            axes[j // ncols][j % ncols].set_visible(False)

        fig.tight_layout()
        out = outdir / f"oat_fan_{kpi}.png"
        fig.savefig(out, dpi=140, bbox_inches="tight")
        plt.close(fig)
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
