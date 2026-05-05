"""Build (X, Y) training matrices from per-scenario CSVs + JSON.

Walks ``runs/<mineral>/<NNNNNN>.csv`` produced by
``scripts/run_one_scenario.py``, matches each CSV to its scenario in
``scenarios/<mineral>.json``, encodes the scenario via
``surrogate.features.encode``, and extracts target values via
``surrogate.targets.extract_targets``.

Result is one tabular dataset per mineral, suitable for LightGBM /
sklearn / etc.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import features as ft
from . import targets as tg


#: Columns we actually need from each model_data CSV. Restricting the
#: read makes loading 6000 CSVs ~10x faster.
_NEEDED_COLUMNS = [
    "Step",
    "Global_Price",
    "Fulfilled_Demand_Units",
    "Unfulfilled_Demand_Units",
]


def _list_run_csvs(runs_dir: Path) -> list[tuple[int, Path]]:
    """Return [(index, path)] sorted by index for run CSVs in a mineral dir.

    Filenames follow the ``run_one_scenario.py`` convention
    ``<index:06d>.csv`` (e.g. ``000123.csv``). Files that don't match
    are skipped silently.
    """
    out: list[tuple[int, Path]] = []
    for p in sorted(runs_dir.glob("*.csv")):
        try:
            idx = int(p.stem)
        except ValueError:
            continue
        out.append((idx, p))
    return sorted(out, key=lambda x: x[0])


def build_mineral_dataset(
    mineral: str,
    runs_dir: Path,
    scenarios_path: Path,
    n_steps: int = ft.DEFAULT_N_STEPS,
) -> pd.DataFrame:
    """Build one mineral's dataset as a flat DataFrame.

    Args:
        mineral: e.g. ``'lithium'``.
        runs_dir: directory containing ``<index>.csv`` files for this
            mineral (typically ``<root>/runs/<mineral>/``).
        scenarios_path: JSON file of scenario dicts as produced by
            ``scripts/sample_scenarios.py``. Must align by index with
            the CSV filenames.
        n_steps: simulation horizon (passed to ``encode`` so feature
            normalisation matches what the surrogate expects at
            inference time).

    Returns:
        DataFrame of shape (n_complete, 1 + F + T) where
            * column ``scenario_index`` is the integer index,
            * columns ``f00 ... f{F-1}`` are the encoded features,
            * columns named in ``TARGET_NAMES`` are the targets.
        Rows where target extraction yielded NaN (or the CSV failed
        to load) are dropped silently with a printed count.
    """
    with scenarios_path.open() as f:
        scenarios = json.load(f)
    if not isinstance(scenarios, list):
        raise ValueError(f"{scenarios_path}: top-level must be a list")

    runs = _list_run_csvs(runs_dir)
    feat_names = ft.feature_names(mineral)

    rows: list[dict] = []
    n_dropped = 0
    for idx, csv_path in runs:
        if idx >= len(scenarios):
            n_dropped += 1
            continue
        scen = scenarios[idx]
        if scen.get("mineral") != mineral:
            n_dropped += 1
            continue
        try:
            df = pd.read_csv(csv_path, usecols=_NEEDED_COLUMNS)
        except (ValueError, FileNotFoundError):
            n_dropped += 1
            continue

        targets = tg.extract_targets(df, scen)
        if any(not np.isfinite(v) for v in targets.values()):
            n_dropped += 1
            continue

        x = ft.encode(scen, n_steps=n_steps)
        row: dict = {"scenario_index": idx}
        for j, name in enumerate(feat_names):
            row[name] = float(x[j])
        for name in tg.TARGET_NAMES:
            row[name] = float(targets[name])
        rows.append(row)

    print(f"[{mineral}] {len(rows)} usable rows from {len(runs)} csvs ({n_dropped} dropped)")
    return pd.DataFrame(rows)


def split_xy(
    df: pd.DataFrame, mineral: str,
) -> tuple[np.ndarray, np.ndarray, list[str], list[str]]:
    """Split a mineral dataset into feature matrix X and target matrix Y.

    Returns ``(X, Y, feature_names, target_names)`` where X is
    ``(N, F)`` float32 and Y is ``(N, len(TARGET_NAMES))`` float32.
    The order of ``feature_names`` matches ``surrogate.features.feature_names``,
    so a saved model can verify input alignment at inference time.
    """
    feat_names = ft.feature_names(mineral)
    X = df[feat_names].to_numpy(dtype=np.float32)
    Y = df[tg.TARGET_NAMES].to_numpy(dtype=np.float32)
    return X, Y, feat_names, list(tg.TARGET_NAMES)


def train_test_split_indices(
    n: int, test_frac: float = 0.1, val_frac: float = 0.1, seed: int = 0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Random 80/10/10 split of an integer range.

    Returns three sorted arrays of indices: (train, val, test).
    """
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    n_test = int(round(test_frac * n))
    n_val = int(round(val_frac * n))
    test = np.sort(idx[:n_test])
    val = np.sort(idx[n_test:n_test + n_val])
    train = np.sort(idx[n_test + n_val:])
    return train, val, test
