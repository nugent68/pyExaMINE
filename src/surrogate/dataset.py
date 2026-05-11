"""Build (X, Y) training matrices from per-scenario CSVs + JSON.

Walks ``runs/<mineral>/<NNNNNN>.csv`` produced by
``scripts/run_one_scenario.py``, matches each CSV to its scenario in
``scenarios/<mineral>.json``, encodes the scenario via
``surrogate.features.encode``, and extracts target values via
``surrogate.targets.extract_targets``.

Two dataset modes:

* **Single-seed** (``seeds_per_scenario == 1``): one row per CSV.
  Each target column carries the raw extracted value. Used by
  the Phase-1 surrogate.

* **Ensemble** (``seeds_per_scenario > 1``): every group of K
  consecutive CSV indices is one underlying scenario; aggregate
  the K runs into per-scenario mean and std for each target. Used
  by the Phase-2 surrogate to predict not just expected response
  but per-scenario uncertainty. ``recovered`` aggregates as a
  rate (mean of 0/1). ``recovery_time_if_recovered`` aggregates
  only over the recovered subset of seeds, with a per-row
  ``..._n_recovered`` count to flag low-confidence aggregates.
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
    feature_version: str = ft.DEFAULT_FEATURE_VERSION,
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
    feat_names = ft.feature_names_versioned(mineral, version=feature_version)

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

        x = ft.encode_versioned(scen, n_steps=n_steps, version=feature_version)
        row: dict = {"scenario_index": idx}
        for j, name in enumerate(feat_names):
            row[name] = float(x[j])
        for name in tg.TARGET_NAMES:
            row[name] = float(targets[name])
        rows.append(row)

    print(f"[{mineral}] {len(rows)} usable rows from {len(runs)} csvs ({n_dropped} dropped)")
    return pd.DataFrame(rows)


def build_ensemble_dataset(
    mineral: str,
    runs_dir: Path,
    scenarios_path: Path,
    seeds_per_scenario: int,
    n_steps: int = ft.DEFAULT_N_STEPS,
    feature_version: str = ft.DEFAULT_FEATURE_VERSION,
) -> pd.DataFrame:
    """Build per-scenario aggregated dataset from K-seed expanded runs.

    Args:
        mineral: e.g. ``'lithium'``.
        runs_dir: directory containing ``<flat_idx:06d>.csv`` files.
        scenarios_path: per-mineral JSON list, length ``n_unique * K``,
            with seed replicas laid out contiguously per scenario.
        seeds_per_scenario: K. Underlying scenario u occupies flat
            indices ``[u*K, u*K + K - 1]``.
        n_steps: simulation horizon.

    Returns:
        DataFrame with one row per UNIQUE scenario. Columns:
            scenario_index : underlying-scenario id (0..n_unique-1)
            n_seeds_used   : seeds whose CSVs were available + parsed
            <feature_names> : ``F`` encoded features
            <target>_mean, <target>_std for each target in TARGET_NAMES
            recovery_time_if_recovered_n_recovered : seeds that recovered

    Behavior on missing data:
      * If <K/2 seeds parsed for a given scenario, the row is dropped.
      * recovery_time_if_recovered_mean is NaN if no seed in the
        ensemble recovered. Train-time code filters this column.
    """
    with scenarios_path.open() as f:
        all_entries = json.load(f)
    if not isinstance(all_entries, list):
        raise ValueError(f"{scenarios_path}: top-level must be a list")

    K = int(seeds_per_scenario)
    if K <= 0:
        raise ValueError(f"seeds_per_scenario must be positive, got {K}")

    n_unique, rem = divmod(len(all_entries), K)
    if rem:
        raise ValueError(
            f"{scenarios_path}: {len(all_entries)} entries is not a "
            f"multiple of seeds_per_scenario={K}"
        )

    feat_names = ft.feature_names_versioned(mineral, version=feature_version)
    rows: list[dict] = []
    n_dropped = 0

    for u in range(n_unique):
        per_seed: list[dict] = []
        for k in range(K):
            flat_idx = u * K + k
            csv_path = runs_dir / f"{flat_idx:06d}.csv"
            if not csv_path.is_file():
                continue
            try:
                df = pd.read_csv(csv_path, usecols=_NEEDED_COLUMNS)
            except (ValueError, FileNotFoundError):
                continue
            t = tg.extract_targets(df, all_entries[flat_idx])
            # Skip seeds where every target came back NaN -- pathological.
            if any(np.isfinite(v) for v in t.values()):
                per_seed.append(t)

        if len(per_seed) < max(2, K // 2):
            n_dropped += 1
            continue

        # Encode features once from the first seed's scenario (all K
        # share the same feature vector by construction).
        scen = all_entries[u * K]
        x = ft.encode_versioned(scen, n_steps=n_steps, version=feature_version)
        row: dict = {"scenario_index": u, "n_seeds_used": len(per_seed)}
        for j, name in enumerate(feat_names):
            row[name] = float(x[j])

        for target in tg.TARGET_NAMES:
            vals = np.array([t[target] for t in per_seed], dtype=np.float64)
            if target == "recovery_time_if_recovered":
                # Aggregate only over recovered seeds. Other seeds
                # have NaN here and don't contribute -- the
                # ``recovered`` rate captures that dimension.
                finite = vals[np.isfinite(vals)]
                if finite.size > 0:
                    row[f"{target}_mean"] = float(np.mean(finite))
                    row[f"{target}_std"]  = float(np.std(finite, ddof=0))
                else:
                    row[f"{target}_mean"] = float("nan")
                    row[f"{target}_std"]  = float("nan")
                row[f"{target}_n_recovered"] = int(finite.size)
            else:
                # Replace any per-seed NaN with the run's nan-mean to
                # avoid one bad seed killing the whole row. Practical
                # NaN rate here should be ~0.
                if not np.all(np.isfinite(vals)):
                    finite = vals[np.isfinite(vals)]
                    if finite.size == 0:
                        row[f"{target}_mean"] = float("nan")
                        row[f"{target}_std"]  = float("nan")
                        continue
                    vals = finite
                row[f"{target}_mean"] = float(np.mean(vals))
                row[f"{target}_std"]  = float(np.std(vals, ddof=0))

        rows.append(row)

    print(f"[{mineral}] {len(rows)} usable scenarios, {n_dropped} dropped "
          f"(K={K} seeds/scenario)")
    return pd.DataFrame(rows)


def build_ensemble_dataset_from_h5(
    mineral: str,
    h5_path: Path,
    scenarios_path: Path,
    seeds_per_scenario: int,
    n_steps: int = ft.DEFAULT_N_STEPS,
    feature_version: str = ft.DEFAULT_FEATURE_VERSION,
) -> pd.DataFrame:
    """Like :func:`build_ensemble_dataset` but reads from a compacted H5.

    Equivalent to the CSV-iteration path, but bulk-reads the three
    target-relevant columns (``Global_Price``,
    ``Fulfilled_Demand_Units``, ``Unfulfilled_Demand_Units``) from the
    per-mineral aggregate HDF5 once instead of opening N=400000
    individual CSV / per-scenario HDF5 files.  At 10x scale (1.2M
    sims) the CSV path would take ~17 hours; this path takes ~5
    minutes per mineral.

    The output DataFrame is bit-identical to what the CSV path
    produces for the same scenarios + same seed-count, so existing
    train_scalar / train_quantile pipelines work unchanged.
    """
    import h5py

    with scenarios_path.open() as f:
        all_entries = json.load(f)
    if not isinstance(all_entries, list):
        raise ValueError(f"{scenarios_path}: top-level must be a list")
    K = int(seeds_per_scenario)
    if K <= 0:
        raise ValueError(f"seeds_per_scenario must be positive, got {K}")
    n_unique, rem = divmod(len(all_entries), K)
    if rem:
        raise ValueError(
            f"{scenarios_path}: {len(all_entries)} entries is not a "
            f"multiple of seeds_per_scenario={K}"
        )

    # Bulk-load the columns extract_targets reads.  Total ~6 GB for
    # the 10x corpus, well within typical node RAM.
    print(f"[{mineral}] reading H5 columns from {h5_path}")
    with h5py.File(h5_path, "r") as h:
        flat_idxs = h["meta/flat_idx"][:]
        gp = h["Global_Price"][:]
        fd = h["Fulfilled_Demand_Units"][:]
        ud = h["Unfulfilled_Demand_Units"][:]
        if "Step" in h:
            step_arr = h["Step"][:]
        else:
            step_arr = np.arange(gp.shape[1], dtype=np.int32)
    flat_to_row: dict[int, int] = {
        int(fi): r for r, fi in enumerate(flat_idxs)
    }
    print(f"[{mineral}] loaded {gp.shape[0]} trajectories x "
          f"{gp.shape[1]} steps")

    feat_names = ft.feature_names_versioned(mineral, version=feature_version)
    rows: list[dict] = []
    n_dropped = 0

    for u in range(n_unique):
        per_seed: list[dict] = []
        for k in range(K):
            flat_idx = u * K + k
            row_in_h5 = flat_to_row.get(flat_idx)
            if row_in_h5 is None:
                continue
            # extract_targets takes a DataFrame; build the smallest
            # one that has the three columns it reads.
            df = pd.DataFrame({
                "Step": step_arr,
                "Global_Price": gp[row_in_h5],
                "Fulfilled_Demand_Units": fd[row_in_h5],
                "Unfulfilled_Demand_Units": ud[row_in_h5],
            })
            t = tg.extract_targets(df, all_entries[flat_idx])
            if any(np.isfinite(v) for v in t.values()):
                per_seed.append(t)

        if len(per_seed) < max(2, K // 2):
            n_dropped += 1
            continue

        scen = all_entries[u * K]
        x = ft.encode_versioned(scen, n_steps=n_steps, version=feature_version)
        row: dict = {"scenario_index": u, "n_seeds_used": len(per_seed)}
        for j, name in enumerate(feat_names):
            row[name] = float(x[j])

        for target in tg.TARGET_NAMES:
            vals = np.array([t[target] for t in per_seed], dtype=np.float64)
            if target == "recovery_time_if_recovered":
                finite = vals[np.isfinite(vals)]
                if finite.size > 0:
                    row[f"{target}_mean"] = float(np.mean(finite))
                    row[f"{target}_std"]  = float(np.std(finite, ddof=0))
                else:
                    row[f"{target}_mean"] = float("nan")
                    row[f"{target}_std"]  = float("nan")
                row[f"{target}_n_recovered"] = int(finite.size)
            else:
                if not np.all(np.isfinite(vals)):
                    finite = vals[np.isfinite(vals)]
                    if finite.size == 0:
                        row[f"{target}_mean"] = float("nan")
                        row[f"{target}_std"]  = float("nan")
                        continue
                    vals = finite
                row[f"{target}_mean"] = float(np.mean(vals))
                row[f"{target}_std"]  = float(np.std(vals, ddof=0))

        rows.append(row)

    print(f"[{mineral}] {len(rows)} usable scenarios, {n_dropped} dropped "
          f"(K={K} seeds/scenario, source=h5)")
    return pd.DataFrame(rows)


def split_xy(
    df: pd.DataFrame, mineral: str,
) -> tuple[np.ndarray, np.ndarray, list[str], list[str]]:
    """Split a single-seed dataset into feature matrix X and target matrix Y.

    Returns ``(X, Y, feature_names, target_names)`` where X is
    ``(N, F)`` float32 and Y is ``(N, len(TARGET_NAMES))`` float32.
    The order of ``feature_names`` matches ``surrogate.features.feature_names``,
    so a saved model can verify input alignment at inference time.

    For ensemble datasets (with ``<target>_mean`` / ``<target>_std``
    columns) use ``split_xy_ensemble`` instead.
    """
    feat_names = ft.feature_names_versioned(mineral, version=detect_feature_version(df, mineral))
    X = df[feat_names].to_numpy(dtype=np.float32)
    Y = df[tg.TARGET_NAMES].to_numpy(dtype=np.float32)
    return X, Y, feat_names, list(tg.TARGET_NAMES)


def detect_feature_version(df: pd.DataFrame, mineral: str) -> str:
    """Look at columns and decide whether this parquet was built v1 or v2.

    v2 adds ``country_<X>__has_embargo`` columns that v1 does not have.
    """
    probe_country = ft.COUNTRIES_BY_MINERAL[mineral][0]
    sample_v2_col = f"country_{probe_country}__has_embargo"
    return "v2" if sample_v2_col in df.columns else "v1"


def split_xy_ensemble(
    df: pd.DataFrame, mineral: str,
) -> tuple[np.ndarray, dict[str, np.ndarray], dict[str, np.ndarray], list[str]]:
    """Split an ensemble dataset (mean+std cols) into X plus per-target arrays.

    Returns ``(X, Y_mean, Y_std, feature_names)`` where:
        * ``X`` has shape ``(N, F)``, float32.
        * ``Y_mean`` is a dict ``target -> (N,) float32`` with NaNs left in.
        * ``Y_std``  is the same shape, also leaving NaNs.

    Targets with all-NaN columns (e.g. recovery_time_if_recovered_mean
    where no seed recovered) survive here; downstream training code
    is responsible for masking those rows out.
    """
    feat_names = ft.feature_names_versioned(mineral, version=detect_feature_version(df, mineral))
    X = df[feat_names].to_numpy(dtype=np.float32)
    Y_mean: dict[str, np.ndarray] = {}
    Y_std: dict[str, np.ndarray] = {}
    for t in tg.TARGET_NAMES:
        Y_mean[t] = df[f"{t}_mean"].to_numpy(dtype=np.float32)
        Y_std[t]  = df[f"{t}_std"].to_numpy(dtype=np.float32)
    return X, Y_mean, Y_std, feat_names


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
