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

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

# Make `src` importable when run directly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Per-seed run CSVs land here (gitignored). Committed ensemble outputs
# (summary CSV + band PNG) stay in outputs/. Keeps the visible
# repository tidy even when N=20 produces hundreds of large CSVs.
_ENSEMBLE_RUN_DIR = _REPO_ROOT / 'ensemble_runs'

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


# ---------------------------------------------------------------------------
# Ensemble helpers (used when --n-seeds > 1)
# ---------------------------------------------------------------------------


def _run_one_seed(mineral: str, steps: int, seed: int, *,
                  embargoes=None, chokepoint_crises=None):
    """Run one mineral simulation at a given seed and return the DataFrame.

    No file output -- caller decides where (if anywhere) to persist.
    Used by the ensemble runner so per-seed CSVs land in the gitignored
    ensemble_runs/ tree rather than in committed outputs/.
    """
    cfg = CONFIGS[mineral].copy()
    cfg['n_steps'] = steps
    cfg['random_seed'] = seed
    if embargoes:
        cfg['political_embargoes'] = list(embargoes)
    if chokepoint_crises:
        cfg['chokepoint_crises'] = list(chokepoint_crises)
    model = MineralSupplyChainModel(cfg)
    model.run_model(steps)
    return model.get_model_data()


def _seed_job(args):
    """Worker entry point for ProcessPoolExecutor.

    Must be a top-level function (not a closure / lambda) so it pickles
    cleanly to subprocesses. The driver passes a tuple of plain Python
    objects (mineral name, ints, dicts, str path) which all pickle
    without ceremony. Each worker writes its own per-seed CSV (no
    contention -- paths differ by seed) and returns the DataFrame for
    the driver to summarise. With ~320 KB per DataFrame, pickle
    transfer is cheap relative to the 30-50 s of compute per seed.
    """
    mineral, steps, seed, embargoes, choke, csv_path = args
    df = _run_one_seed(
        mineral, steps, seed,
        embargoes=embargoes, chokepoint_crises=choke,
    )
    df.to_csv(csv_path)
    return df


def _run_ensemble(mineral: str, steps: int, scenario_path: Path, *,
                  n_seeds: int, seed_base: int,
                  embargoes=None, chokepoint_crises=None,
                  pool: ProcessPoolExecutor | None = None):
    """Run the same scenario N times with seeds [seed_base..seed_base+N-1].

    Per-seed CSVs go to the gitignored ensemble_runs/ tree mirroring
    the scenario_path layout (e.g. outputs/2050/asia_crisis_2030 ->
    ensemble_runs/2050/asia_crisis_2030/lithium/seed_42.csv). Returns
    the list of DataFrames (one per seed, in seed order) so callers
    can pass them straight to _summarize_ensemble / plot_ensemble_band.

    If a ProcessPoolExecutor is supplied, seeds run in parallel and
    results are reassembled in seed order for the paired-comparison
    statistics. ``pool=None`` runs sequentially (the default for
    --n-workers 1).
    """
    rel = scenario_path.relative_to(_REPO_ROOT / 'outputs')
    seed_dir = _ENSEMBLE_RUN_DIR / rel / mineral
    seed_dir.mkdir(parents=True, exist_ok=True)

    jobs = [
        (
            mineral, steps, seed_base + i, embargoes, chokepoint_crises,
            str(seed_dir / f'seed_{seed_base + i}.csv'),
        )
        for i in range(n_seeds)
    ]

    if pool is None:
        return [_seed_job(j) for j in jobs]
    # pool.map preserves input order, so the returned list is in
    # seed order even though workers may finish out-of-order.
    return list(pool.map(_seed_job, jobs))


def _summarize_ensemble(scen_dfs, window_start, window_len, base_dfs=None):
    """Compute mean/std/p10/p50/p90 of in-window price across an ensemble.

    If baseline DataFrames are supplied (one per seed, paired by index
    with scen_dfs), also computes the per-seed delta percentage. Pairing
    by index means delta is taken on matched RNG sequences -- shared
    geopolitical events that fired in both runs cancel out, leaving
    just the scenario-attributable signal. This is the whole point of
    matched-pair ensemble inference.
    """
    scen_avgs = np.array([
        _in_window_avg(df, window_start, window_len) for df in scen_dfs
    ], dtype=float)
    summary = {
        'n_seeds': int(len(scen_avgs)),
        'scen_mean': float(np.mean(scen_avgs)),
        'scen_std': float(np.std(scen_avgs, ddof=1)) if len(scen_avgs) > 1 else 0.0,
        'scen_p10': float(np.percentile(scen_avgs, 10)),
        'scen_p50': float(np.percentile(scen_avgs, 50)),
        'scen_p90': float(np.percentile(scen_avgs, 90)),
    }
    if base_dfs:
        base_avgs = np.array([
            _in_window_avg(df, window_start, window_len) for df in base_dfs
        ], dtype=float)
        summary['base_mean'] = float(np.mean(base_avgs))
        summary['base_std'] = float(np.std(base_avgs, ddof=1)) if len(base_avgs) > 1 else 0.0
        # Paired per-seed deltas. Skip seeds where the baseline avg
        # is non-positive (would divide by zero).
        valid = base_avgs > 0
        deltas = np.where(valid, (scen_avgs - base_avgs) / base_avgs * 100.0, 0.0)
        if valid.any():
            d_valid = deltas[valid]
            summary.update({
                'delta_mean_pct': float(np.mean(d_valid)),
                'delta_std_pct': float(np.std(d_valid, ddof=1)) if len(d_valid) > 1 else 0.0,
                'delta_p10_pct': float(np.percentile(d_valid, 10)),
                'delta_p50_pct': float(np.percentile(d_valid, 50)),
                'delta_p90_pct': float(np.percentile(d_valid, 90)),
            })
    return summary, scen_avgs


def _write_ensemble_summary(scenario_path: Path, rows: list[dict]):
    """Write a single ensemble_summary.csv at the scenario level.

    Each row is one mineral. Columns are the union of keys from
    _summarize_ensemble (paired-delta keys may be absent if no
    baseline was supplied).
    """
    if not rows:
        return
    df = pd.DataFrame(rows)
    df.to_csv(scenario_path / 'ensemble_summary.csv', index=False)


def plot_ensemble_band(scenario_path: Path, mineral: str,
                       scen_dfs, baseline_dfs=None,
                       window_start: int | None = None,
                       window_len: int | None = None):
    """Time-series plot with median + p10/p90 band (per mineral).

    If a baseline ensemble is passed, draws its median + band in grey
    underneath the scenario for visual comparison. Saves to
    {scenario_path}/{mineral}_ensemble_band.png.
    """
    if not scen_dfs:
        return

    steps = scen_dfs[0]['Step'].values
    scen_prices = pd.DataFrame(
        {i: df['Global_Price'].values for i, df in enumerate(scen_dfs)},
    )

    fig, ax = plt.subplots(figsize=(11, 5))
    if baseline_dfs:
        base_prices = pd.DataFrame(
            {i: df['Global_Price'].values for i, df in enumerate(baseline_dfs)},
        )
        ax.fill_between(
            steps,
            base_prices.quantile(0.10, axis=1),
            base_prices.quantile(0.90, axis=1),
            alpha=0.15, color='grey', label=f'baseline p10-p90 (N={len(baseline_dfs)})',
        )
        ax.plot(
            steps, base_prices.quantile(0.50, axis=1),
            color='grey', lw=1.0, ls='--', label='baseline median',
        )
    ax.fill_between(
        steps,
        scen_prices.quantile(0.10, axis=1),
        scen_prices.quantile(0.90, axis=1),
        alpha=0.30, label=f'scenario p10-p90 (N={len(scen_dfs)})',
    )
    ax.plot(
        steps, scen_prices.quantile(0.50, axis=1),
        lw=1.4, label='scenario median',
    )
    if window_start is not None and window_len is not None:
        ax.axvspan(window_start, window_start + window_len,
                   color='orange', alpha=0.10, label='comparison window')
    ax.set_xlabel('Step (weekly)')
    ax.set_ylabel(f'{mineral.title()} price ($/t)')
    ax.set_title(f'{mineral.title()} ensemble — {scenario_path.name}')
    ax.grid(True, alpha=0.3)
    ax.legend(loc='upper left', fontsize=9)
    fig.tight_layout()
    fig.savefig(scenario_path / f'{mineral}_ensemble_band.png', dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Ensemble entry points (called when --n-seeds > 1)
# ---------------------------------------------------------------------------


def regenerate_ensembles(out_root: Path, n_seeds: int, seed_base: int,
                         n_workers: int = 1):
    """Run paired ensembles for every comparison-style scenario.

    Layout per scenario folder:
      ensemble_summary.csv            (one row per mineral)
      <mineral>_ensemble_band.png     (one per mineral)
    Per-seed CSVs go to ensemble_runs/<scenario>/<mineral>/seed_<N>.csv
    (gitignored).

    A single ProcessPoolExecutor is created up front and reused across
    every scenario so the per-pool spawn cost (re-importing matplotlib /
    pandas / numpy in each worker) is paid once for the whole run, not
    once per scenario.
    """
    print(f"\n=== Running ensembles with N={n_seeds} seeds, "
          f"{n_workers} worker{'s' if n_workers != 1 else ''} (base={seed_base}) ===")

    pool = ProcessPoolExecutor(max_workers=n_workers) if n_workers > 1 else None
    try:
        _regenerate_ensembles_impl(out_root, n_seeds, seed_base, pool)
    finally:
        if pool is not None:
            pool.shutdown(wait=True)


def _regenerate_ensembles_impl(out_root: Path, n_seeds: int, seed_base: int,
                                pool: ProcessPoolExecutor | None):
    # ---- Canonical baselines (Li/Ni/Pt at CANONICAL_STEPS) ----
    print("\n[ensemble] canonical baselines")
    canonical_baselines: dict[str, list[pd.DataFrame]] = {}
    for mineral in ('lithium', 'nickel', 'platinum'):
        print(f"  {mineral}")
        canonical_baselines[mineral] = _run_ensemble(
            mineral, CANONICAL_STEPS, out_root,
            n_seeds=n_seeds, seed_base=seed_base, pool=pool,
        )

    # ---- 24-yr canonical embargo / chokepoint scenarios ----
    embargo_scenarios = []
    for folder, embargoes in LI_EMBARGO_SCENARIOS.items():
        embargo_scenarios.append(
            (folder, 'lithium', embargoes, None, EMBARGO_START,
             max(int(e['duration']) for e in embargoes)),
        )
    embargo_scenarios.append(
        ('sa_pt', 'platinum',
         [_embargo('South Africa', EMBARGO_START, 52)], None,
         EMBARGO_START, 52),
    )

    chokepoint_window = 24
    chokepoint_scenarios = []
    for folder, (mineral, crises) in CHOKEPOINT_SCENARIOS.items():
        chokepoint_scenarios.append(
            (folder, mineral, None, crises, EMBARGO_START, chokepoint_window),
        )

    canonical_comparison = embargo_scenarios + chokepoint_scenarios

    print("\n[ensemble] canonical embargo + chokepoint scenarios")
    for folder, mineral, embargoes, choke, ws, wl in canonical_comparison:
        scenario_path = out_root / folder
        scenario_path.mkdir(parents=True, exist_ok=True)
        print(f"  {folder} / {mineral}")
        scen_dfs = _run_ensemble(
            mineral, CANONICAL_STEPS, scenario_path,
            n_seeds=n_seeds, seed_base=seed_base,
            embargoes=embargoes, chokepoint_crises=choke,
            pool=pool,
        )
        summary, _ = _summarize_ensemble(
            scen_dfs, ws, wl, base_dfs=canonical_baselines[mineral],
        )
        summary['mineral'] = mineral
        summary['window_start'] = ws
        summary['window_len'] = wl
        _write_ensemble_summary(scenario_path, [summary])
        plot_ensemble_band(
            scenario_path, mineral,
            scen_dfs, baseline_dfs=canonical_baselines[mineral],
            window_start=ws, window_len=wl,
        )

    # ---- 26-yr 2050 baselines + combined scenarios ----
    out_2050 = out_root / '2050'
    print("\n[ensemble] 2050 baselines")
    baselines_2050: dict[str, list[pd.DataFrame]] = {}
    base_dir = out_2050 / 'baseline'
    base_dir.mkdir(parents=True, exist_ok=True)
    base_summary_rows = []
    for mineral in ('lithium', 'nickel', 'platinum'):
        print(f"  {mineral}")
        baselines_2050[mineral] = _run_ensemble(
            mineral, SCENARIO_2050_STEPS, base_dir,
            n_seeds=n_seeds, seed_base=seed_base, pool=pool,
        )
        # Write a baseline-only summary (no delta) for reference -- the
        # comparison window for the baseline matches each crisis scenario's
        # window, so we report the union of windows below.
    # Compute baseline summary across each scenario's window once we
    # know which windows we need (done in scenario loop below).
    # For the baseline summary CSV, just dump the full-run mean.
    full_window = (0, SCENARIO_2050_STEPS)
    for mineral in ('lithium', 'nickel', 'platinum'):
        s, _ = _summarize_ensemble(
            baselines_2050[mineral], full_window[0], full_window[1],
        )
        s['mineral'] = mineral
        s['window_start'] = full_window[0]
        s['window_len'] = full_window[1]
        base_summary_rows.append(s)
    _write_ensemble_summary(base_dir, base_summary_rows)
    for mineral in ('lithium', 'nickel', 'platinum'):
        plot_ensemble_band(
            base_dir, mineral,
            baselines_2050[mineral],
            window_start=full_window[0], window_len=full_window[1],
        )

    print("\n[ensemble] 2050 combined scenarios")
    # Group by folder so we write one ensemble_summary.csv per folder
    # (with multiple rows when a scenario covers more than one mineral).
    by_folder: dict[str, list] = {}
    for (mineral, folder), (embargoes, choke) in SCENARIOS_2050.items():
        by_folder.setdefault(folder, []).append((mineral, embargoes, choke))

    for folder, items in by_folder.items():
        scenario_path = out_2050 / folder
        scenario_path.mkdir(parents=True, exist_ok=True)
        ws = SCENARIO_2050_WINDOW_START[folder]
        wl = SCENARIO_2050_WINDOW_LEN[folder]
        rows = []
        for mineral, embargoes, choke in items:
            print(f"  {folder} / {mineral}")
            scen_dfs = _run_ensemble(
                mineral, SCENARIO_2050_STEPS, scenario_path,
                n_seeds=n_seeds, seed_base=seed_base,
                embargoes=embargoes, chokepoint_crises=choke,
                pool=pool,
            )
            summary, _ = _summarize_ensemble(
                scen_dfs, ws, wl, base_dfs=baselines_2050[mineral],
            )
            summary['mineral'] = mineral
            summary['window_start'] = ws
            summary['window_len'] = wl
            rows.append(summary)
            plot_ensemble_band(
                scenario_path, mineral,
                scen_dfs, baseline_dfs=baselines_2050[mineral],
                window_start=ws, window_len=wl,
            )
        _write_ensemble_summary(scenario_path, rows)


def print_ensemble_summary(out_root: Path):
    """Print a one-screen overview of every ensemble_summary.csv."""
    print("\n=== ENSEMBLE SUMMARY ===")
    for summary_path in sorted(out_root.rglob('ensemble_summary.csv')):
        rel = summary_path.parent.relative_to(out_root)
        df = pd.read_csv(summary_path)
        print(f"\n{rel}")
        for _, r in df.iterrows():
            mineral = r['mineral']
            scen_mean = r.get('scen_mean', float('nan'))
            scen_std = r.get('scen_std', float('nan'))
            if 'delta_mean_pct' in df.columns and not pd.isna(r.get('delta_mean_pct')):
                d_mean = r['delta_mean_pct']
                d_std = r['delta_std_pct']
                d_p10 = r['delta_p10_pct']
                d_p90 = r['delta_p90_pct']
                print(
                    f"  {mineral:8s}  scen=${scen_mean:>13,.0f} ± {scen_std:>11,.0f}  "
                    f"Δ={d_mean:+6.2f}% ± {d_std:5.2f}%  "
                    f"[p10 {d_p10:+6.2f}%, p90 {d_p90:+6.2f}%]"
                )
            else:
                print(f"  {mineral:8s}  mean=${scen_mean:>13,.0f} ± {scen_std:>11,.0f}")


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

def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate every committed simulation output. "
            "With --n-seeds=1 (default) reproduces the legacy single-seed "
            "behavior. With --n-seeds>1 runs paired ensembles for every "
            "comparison-style scenario; per-seed CSVs land in the "
            "gitignored ensemble_runs/ tree, while ensemble_summary.csv "
            "and *_ensemble_band.png are committed alongside the regular "
            "scenario outputs."
        ),
    )
    parser.add_argument(
        '--n-seeds', type=int, default=1,
        help='Number of seeds per scenario (default 1 = single-seed).'
    )
    parser.add_argument(
        '--seed-base', type=int, default=42,
        help='Starting seed; ensemble uses [seed_base, seed_base+N-1].'
    )
    parser.add_argument(
        '--n-workers', type=int, default=0,
        help=(
            'Parallel workers for ensemble runs. 0 = auto '
            '(min(cpu_count, n_seeds)); 1 = sequential. Each seed-run '
            'is fully independent so speedup is near-linear up to '
            'n_seeds.'
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    out_root = _REPO_ROOT / 'outputs'
    out_root.mkdir(parents=True, exist_ok=True)

    if args.n_seeds <= 1:
        # Single-seed legacy path: produces the canonical
        # outputs/{mineral}_*, scenario subdirs, and PNGs as before.
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
    else:
        # Ensemble path. Skips the single-seed PNGs (each scenario gets
        # one ensemble band plot instead). Produces ensemble_summary.csv
        # at every scenario folder.
        if args.n_workers <= 0:
            n_workers = min(os.cpu_count() or 1, args.n_seeds)
        else:
            n_workers = max(1, args.n_workers)
        regenerate_ensembles(
            out_root,
            n_seeds=args.n_seeds, seed_base=args.seed_base,
            n_workers=n_workers,
        )
        print_ensemble_summary(out_root)


if __name__ == '__main__':
    main()
