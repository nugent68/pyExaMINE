#!/usr/bin/env python3
"""
Regenerate every committed simulation output in `outputs/`.

Runs the canonical 24-yr (1248-step) baselines for Li / Ni / Pt, the
embargo scenarios, the chokepoint scenarios, and the 26-yr (1352-step)
2050 scenarios under `outputs/2050/`. Also regenerates the scenario
summary plots (`outputs/embargo_comparison.png`,
`outputs/2050/scenarios_2050.png`, `outputs/2050/scenario_summary.png`).

All scenarios run with seed 42. Driven entirely from this file so it's
easy to reproduce after model changes; intended to be re-run whenever
the model dynamics change.

Run with: ``uv run python scripts/regenerate_outputs.py``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make `src` importable when run directly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.config.lithium_config import LITHIUM_CONFIG
from src.config.nickel_config import NICKEL_CONFIG
from src.config.platinum_config import PLATINUM_CONFIG
from src.model.supply_chain_model import MineralSupplyChainModel
from src.visualization.visualizer import (
    create_summary_statistics,
    plot_supply_chain_analysis,
    save_summary_statistics,
)

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


CANONICAL_STEPS = 1248       # 24 weekly years
SCENARIO_2050_STEPS = 1352   # 26 weekly years (2024 -> 2050)
EMBARGO_START = 624          # year 12 in the 24-yr canonical run
SEED = 42

CONFIGS = {
    'lithium': LITHIUM_CONFIG,
    'nickel': NICKEL_CONFIG,
    'platinum': PLATINUM_CONFIG,
}


def _run(mineral: str, steps: int, output_dir: Path, *, embargoes=None,
         chokepoint_crises=None, generate_viz: bool = True):
    """Run one mineral simulation and write CSV / stats / PNG to ``output_dir``."""
    cfg = CONFIGS[mineral].copy()
    cfg['n_steps'] = steps
    cfg['random_seed'] = SEED
    if embargoes:
        cfg['political_embargoes'] = list(embargoes)
    if chokepoint_crises:
        cfg['chokepoint_crises'] = list(chokepoint_crises)

    output_dir.mkdir(parents=True, exist_ok=True)
    model = MineralSupplyChainModel(cfg)
    model.run_model(steps)

    df = model.get_model_data()
    csv_path = output_dir / f"{mineral}_model_data.csv"
    df.to_csv(csv_path)

    stats = create_summary_statistics(df, cfg)
    save_summary_statistics(stats, str(output_dir / f"{mineral}_summary_stats.txt"))

    if generate_viz:
        plot_supply_chain_analysis(
            df, cfg, str(output_dir / f"{mineral}_supply_chain_analysis.png"),
        )
    return df


def _embargo(country: str, start: int, duration: int) -> dict:
    return {'country': country, 'start_step': start, 'duration': duration}


def _chokepoint(name: str, start: int, duration: int) -> dict:
    return {'chokepoint': name, 'start_step': start, 'duration': duration}


def _in_window_avg(df: pd.DataFrame, start: int, length: int) -> float:
    end = start + length
    window = df[(df['Step'] >= start) & (df['Step'] < end)]
    return float(window['Global_Price'].mean())


# ---------------------------------------------------------------------------
# Canonical 24-yr baselines (top-level outputs/{mineral}_*)
# ---------------------------------------------------------------------------

def regenerate_canonical_baselines(out_root: Path):
    print("\n=== Canonical 24-yr baselines ===")
    dfs = {}
    for mineral in ('lithium', 'nickel', 'platinum'):
        print(f"\n[baseline] {mineral}")
        dfs[mineral] = _run(mineral, CANONICAL_STEPS, out_root)
    return dfs


# ---------------------------------------------------------------------------
# Lithium embargo scenarios
# ---------------------------------------------------------------------------

LI_EMBARGO_SCENARIOS = {
    # Folder name -> list of embargo dicts
    'china_li':       [_embargo('China',     EMBARGO_START, 52)],
    'chile_li':       [_embargo('Chile',     EMBARGO_START, 52)],
    'australia_li':   [_embargo('Australia', EMBARGO_START, 52)],
    'chile_china_li': [_embargo('Chile',     EMBARGO_START, 52),
                       _embargo('China',     EMBARGO_START, 52)],
    'big3_li_5yr':    [_embargo('Chile',     EMBARGO_START, 260),
                       _embargo('China',     EMBARGO_START, 260),
                       _embargo('Australia', EMBARGO_START, 260)],
}

# Chokepoint scenarios (8 wk closure at step 624 by default)
CHOKEPOINT_SCENARIOS = {
    'suez_li':    ('lithium', [_chokepoint('Suez Canal',     EMBARGO_START, 8)]),
    'malacca_li': ('lithium', [_chokepoint('Malacca Strait', EMBARGO_START, 8)]),
    'hormuz_li':  ('lithium', [_chokepoint('Strait of Hormuz', EMBARGO_START, 8)]),
    'malacca_ni': ('nickel',  [_chokepoint('Malacca Strait', EMBARGO_START, 8)]),
    'suez_pt':    ('platinum',[_chokepoint('Suez Canal',     EMBARGO_START, 8)]),
}


def regenerate_li_embargo_scenarios(out_root: Path, baseline_li: pd.DataFrame):
    print("\n=== Li political-embargo scenarios ===")
    rows = []
    base_avg = _in_window_avg(baseline_li, EMBARGO_START, 52)
    rows.append(('Baseline (no embargo)', base_avg, 0.0))
    for folder, embargoes in LI_EMBARGO_SCENARIOS.items():
        print(f"\n[embargo] {folder}: {embargoes}")
        df = _run('lithium', CANONICAL_STEPS, out_root / folder, embargoes=embargoes)
        # 5-yr scenario uses a longer in-window average; everyone else 52 weeks.
        window_len = max(int(e['duration']) for e in embargoes)
        avg = _in_window_avg(df, EMBARGO_START, window_len)
        delta = (avg - base_avg) / base_avg * 100.0
        rows.append((folder, avg, delta))
    return rows


def regenerate_pt_embargo_scenario(out_root: Path, baseline_pt: pd.DataFrame):
    print("\n=== Pt political-embargo scenario ===")
    rows = []
    base_avg = _in_window_avg(baseline_pt, EMBARGO_START, 52)
    rows.append(('Pt baseline', base_avg, 0.0))
    folder = 'sa_pt'
    embargoes = [_embargo('South Africa', EMBARGO_START, 52)]
    print(f"\n[embargo] {folder}: {embargoes}")
    df = _run('platinum', CANONICAL_STEPS, out_root / folder, embargoes=embargoes)
    avg = _in_window_avg(df, EMBARGO_START, 52)
    delta = (avg - base_avg) / base_avg * 100.0
    rows.append(('Pt SA embargo, 1 yr', avg, delta))
    return rows


def regenerate_chokepoint_scenarios(
    out_root: Path, baselines: dict[str, pd.DataFrame],
):
    print("\n=== Chokepoint-crisis scenarios ===")
    rows = []
    for folder, (mineral, crises) in CHOKEPOINT_SCENARIOS.items():
        print(f"\n[chokepoint] {folder}: {crises}")
        df = _run(mineral, CANONICAL_STEPS, out_root / folder, chokepoint_crises=crises)
        # The crisis-vs-baseline in-window window is the closure plus the
        # lead-time tail, so use 24 weeks (matches the README convention).
        window = 24
        base_avg = _in_window_avg(baselines[mineral], EMBARGO_START, window)
        avg = _in_window_avg(df, EMBARGO_START, window)
        delta = (avg - base_avg) / base_avg * 100.0
        rows.append((mineral, folder, avg, base_avg, delta))
    return rows


# ---------------------------------------------------------------------------
# 2050 combined scenarios (1352 steps; embargoes + chokepoints together)
# ---------------------------------------------------------------------------

# A 2050 scenario can apply different events to different minerals -- the
# data structure below is keyed by (mineral, scenario_folder) and holds
# (embargoes, chokepoint_crises).
SCENARIOS_2050 = {
    # asia_crisis_2030: China embargo + Malacca + Suez at step 312 (~2030)
    ('lithium',  'asia_crisis_2030'): (
        [_embargo('China', 312, 52)],
        [_chokepoint('Malacca Strait', 312, 16),
         _chokepoint('Suez Canal',     312, 12)],
    ),
    ('nickel',   'asia_crisis_2030'): (
        [_embargo('China', 312, 52)],
        [_chokepoint('Malacca Strait', 312, 16),
         _chokepoint('Suez Canal',     312, 12)],
    ),
    ('platinum', 'asia_crisis_2030'): (
        [_embargo('China', 312, 52)],
        [_chokepoint('Malacca Strait', 312, 16),
         _chokepoint('Suez Canal',     312, 12)],
    ),
    # li_nationalism_2035: Chile + Australia 2-yr embargo + 1-yr Suez at step 572
    ('lithium', 'li_nationalism_2035'): (
        [_embargo('Chile',     572, 104),
         _embargo('Australia', 572, 104)],
        [_chokepoint('Suez Canal', 572, 52)],
    ),
    # indonesia_squeeze_2032: Indonesia 2-yr embargo + Malacca 6-mo at step 416
    ('nickel', 'indonesia_squeeze_2032'): (
        [_embargo('Indonesia', 416, 104)],
        [_chokepoint('Malacca Strait', 416, 26)],
    ),
    # sa_pt_crisis_2030: South Africa 1-yr embargo + Cape closure 16 wks
    ('platinum', 'sa_pt_crisis_2030'): (
        [_embargo('South Africa', 312, 52)],
        [_chokepoint('Cape of Good Hope', 312, 16)],
    ),
    # multi_crisis_2040: Russia + Indonesia 78-wk embargo + Suez + Hormuz at step 832
    ('lithium', 'multi_crisis_2040'): (
        [_embargo('Russia', 832, 78),
         _embargo('Indonesia', 832, 78)],
        [_chokepoint('Suez Canal', 832, 26),
         _chokepoint('Strait of Hormuz', 832, 8)],
    ),
    ('nickel', 'multi_crisis_2040'): (
        [_embargo('Russia', 832, 78),
         _embargo('Indonesia', 832, 78)],
        [_chokepoint('Suez Canal', 832, 26),
         _chokepoint('Strait of Hormuz', 832, 8)],
    ),
    ('platinum', 'multi_crisis_2040'): (
        [_embargo('Russia', 832, 78),
         _embargo('Indonesia', 832, 78)],
        [_chokepoint('Suez Canal', 832, 26),
         _chokepoint('Strait of Hormuz', 832, 8)],
    ),
}

SCENARIO_2050_WINDOW_START = {
    'asia_crisis_2030':       312,
    'li_nationalism_2035':    572,
    'indonesia_squeeze_2032': 416,
    'sa_pt_crisis_2030':      312,
    'multi_crisis_2040':      832,
}
# Length of the in-window averaging slice (matches the longest event in each scenario).
SCENARIO_2050_WINDOW_LEN = {
    'asia_crisis_2030':       52,
    'li_nationalism_2035':    104,
    'indonesia_squeeze_2032': 104,
    'sa_pt_crisis_2030':      52,
    'multi_crisis_2040':      78,
}


def regenerate_2050_scenarios(out_root: Path):
    out_2050 = out_root / '2050'
    print("\n=== 2050 baseline + combined scenarios ===")

    # Baseline first (used to compute deltas).
    baselines: dict[str, pd.DataFrame] = {}
    for mineral in ('lithium', 'nickel', 'platinum'):
        print(f"\n[2050 baseline] {mineral}")
        baselines[mineral] = _run(
            mineral, SCENARIO_2050_STEPS, out_2050 / 'baseline',
        )

    rows = []
    for (mineral, folder), (embargoes, choke) in SCENARIOS_2050.items():
        print(f"\n[2050] {folder} / {mineral}: emb={embargoes}, choke={choke}")
        df = _run(
            mineral, SCENARIO_2050_STEPS, out_2050 / folder,
            embargoes=embargoes, chokepoint_crises=choke,
        )
        ws = SCENARIO_2050_WINDOW_START[folder]
        wl = SCENARIO_2050_WINDOW_LEN[folder]
        base_avg = _in_window_avg(baselines[mineral], ws, wl)
        avg = _in_window_avg(df, ws, wl)
        delta = (avg - base_avg) / base_avg * 100.0 if base_avg > 0 else 0.0
        rows.append((mineral, folder, base_avg, avg, delta))

    return baselines, rows


# ---------------------------------------------------------------------------
# Cross-scenario summary plots
# ---------------------------------------------------------------------------

def plot_embargo_comparison(out_root: Path, baseline_li: pd.DataFrame):
    """Stacked-line chart of price under each Li embargo scenario."""
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(baseline_li['Step'], baseline_li['Global_Price'] / 1000.0,
            label='Baseline', color='black', lw=1.4, alpha=0.7)
    palette = {
        'china_li':       'tab:orange',
        'chile_li':       'tab:blue',
        'australia_li':   'tab:green',
        'chile_china_li': 'tab:red',
        'big3_li_5yr':    'tab:purple',
    }
    for folder in LI_EMBARGO_SCENARIOS:
        path = out_root / folder / 'lithium_model_data.csv'
        if not path.exists():
            continue
        df = pd.read_csv(path)
        ax.plot(df['Step'], df['Global_Price'] / 1000.0,
                label=folder, color=palette.get(folder), lw=1.0, alpha=0.85)
    ax.axvline(EMBARGO_START, color='grey', linestyle='--', lw=0.8,
               label=f'Embargo start (step {EMBARGO_START})')
    ax.set_xlabel('Step (weekly)')
    ax.set_ylabel('Price ($k / t Li)')
    ax.set_title('Lithium price under political-embargo scenarios (seed 42)')
    ax.grid(True, alpha=0.3)
    ax.legend(loc='upper left', fontsize=9)
    fig.tight_layout()
    fig.savefig(out_root / 'embargo_comparison.png', dpi=120)
    plt.close(fig)


def plot_scenarios_2050(out_root: Path):
    """Price-time-series plot grouped by scenario for the 2050 runs."""
    out_2050 = out_root / '2050'
    scenarios = sorted({s for (_, s) in SCENARIOS_2050})
    minerals = ('lithium', 'nickel', 'platinum')

    fig, axes = plt.subplots(len(minerals), 1, figsize=(12, 9), sharex=True)
    palette = plt.get_cmap('tab10')
    for ax, mineral in zip(axes, minerals):
        bpath = out_2050 / 'baseline' / f"{mineral}_model_data.csv"
        if bpath.exists():
            base = pd.read_csv(bpath)
            ax.plot(base['Step'], base['Global_Price'],
                    label='Baseline', color='black', lw=1.2, alpha=0.7)
        for i, scen in enumerate(scenarios):
            path = out_2050 / scen / f"{mineral}_model_data.csv"
            if not path.exists():
                continue
            df = pd.read_csv(path)
            ax.plot(df['Step'], df['Global_Price'],
                    label=scen, color=palette(i % 10), lw=0.9, alpha=0.85)
        ax.set_title(f'{mineral.title()} price under 2050 scenarios')
        ax.set_ylabel('$/t')
        ax.grid(True, alpha=0.3)
        ax.legend(loc='upper left', fontsize=8, ncol=2)
    axes[-1].set_xlabel('Step (weekly)')
    fig.tight_layout()
    fig.savefig(out_2050 / 'scenarios_2050.png', dpi=120)
    plt.close(fig)


def plot_scenario_summary(out_root: Path, summary_rows: list[tuple]):
    """Bar chart of in-window % delta for the 2050 scenarios."""
    if not summary_rows:
        return
    out_2050 = out_root / '2050'
    labels = [f"{m[:2].upper()} / {s}" for (m, s, *_) in summary_rows]
    deltas = [r[-1] for r in summary_rows]

    fig, ax = plt.subplots(figsize=(11, 6))
    bars = ax.barh(labels, deltas,
                   color=['tab:red' if d > 0 else 'tab:green' for d in deltas])
    ax.axvline(0, color='black', lw=0.6)
    ax.set_xlabel('In-window price delta vs baseline (%)')
    ax.set_title('2050 combined embargo + chokepoint scenarios')
    for bar, d in zip(bars, deltas):
        ax.text(bar.get_width() + (1 if d >= 0 else -1),
                bar.get_y() + bar.get_height() / 2,
                f"{d:+.1f}%", va='center',
                ha='left' if d >= 0 else 'right', fontsize=9)
    ax.grid(True, axis='x', alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_2050 / 'scenario_summary.png', dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    out_root = _REPO_ROOT / 'outputs'
    out_root.mkdir(parents=True, exist_ok=True)

    baselines = regenerate_canonical_baselines(out_root)
    li_embargo_rows = regenerate_li_embargo_scenarios(out_root, baselines['lithium'])
    pt_embargo_rows = regenerate_pt_embargo_scenario(out_root, baselines['platinum'])
    chokepoint_rows = regenerate_chokepoint_scenarios(out_root, baselines)

    plot_embargo_comparison(out_root, baselines['lithium'])

    _, scenario_2050_rows = regenerate_2050_scenarios(out_root)
    plot_scenarios_2050(out_root)
    plot_scenario_summary(out_root, scenario_2050_rows)

    print("\n=== SUMMARY ===")
    print("\nLi embargo scenarios:")
    for name, avg, delta in li_embargo_rows:
        print(f"  {name:32s} ${avg:>11,.0f}  {delta:+6.2f}%")

    print("\nPt embargo scenarios:")
    for name, avg, delta in pt_embargo_rows:
        print(f"  {name:32s} ${avg:>15,.0f}  {delta:+6.2f}%")

    print("\nChokepoint scenarios (24-week in-window):")
    for mineral, folder, avg, base, delta in chokepoint_rows:
        print(f"  {mineral:9s}/{folder:14s} avg=${avg:>13,.0f} base=${base:>13,.0f} {delta:+6.2f}%")

    print("\n2050 combined scenarios (in-window):")
    for mineral, folder, base, avg, delta in scenario_2050_rows:
        print(f"  {mineral:9s}/{folder:24s} base=${base:>13,.0f} avg=${avg:>13,.0f} {delta:+6.2f}%")


if __name__ == '__main__':
    main()
