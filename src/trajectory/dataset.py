"""PyTorch dataset wrappers for trajectory training.

Two corpus types this module knows how to load:

* **Canonical ensemble runs.**  ``ensemble_runs/2050/<folder>/<mineral>/seed_*.csv``,
  the small (~240 trajectories) committed test set.  Built from the
  scenario dicts in :mod:`trajectory.scenarios`.  Useful for unit-testing
  the pipeline before the big sweep lands.

* **Surrogate sweep.**  ``<root>/<mineral>/<idx>.csv`` produced by
  ``scripts/run_one_scenario.py`` against ``scenarios/<mineral>.json``.
  120k CSVs at full sweep size; flat indices map to scenarios via the
  expanded JSON list.

Both routes produce :class:`TrajectoryDataset` instances yielding
``(scenario_features, time_norm, value)`` triples ready for DeepONet
training.  Time is normalised to ``[0, 1]`` by the simulator horizon
``ft.DEFAULT_N_STEPS``.

Targets are by default the ``Global_Price`` column; pass another column
name to predict something else, or ``["Global_Price",
"Total_Mine_Output"]`` to multi-channel.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset

from src.surrogate import features as ft
from .scenarios import CANONICAL_2050


#: Default per-step CSV column predicted by the v1 trajectory surrogate.
DEFAULT_TARGET = "Global_Price"


@dataclass
class TrajectoryRecord:
    """One CSV's worth of trajectory data plus its scenario metadata."""
    csv_path: Path
    mineral: str
    scenario: dict
    seed: int | None = None
    folder: str | None = None    # for canonical runs


def _scan_canonical_2050(root: Path) -> list[TrajectoryRecord]:
    """Walk ``ensemble_runs/2050/<folder>/<mineral>/seed_*.csv``."""
    records: list[TrajectoryRecord] = []
    root = Path(root)
    for (mineral, folder), scen in CANONICAL_2050.items():
        mineral_dir = root / folder / mineral
        if not mineral_dir.is_dir():
            continue
        for csv in sorted(mineral_dir.glob("seed_*.csv")):
            try:
                seed = int(csv.stem.split("_")[1])
            except (IndexError, ValueError):
                seed = None
            records.append(TrajectoryRecord(
                csv_path=csv, mineral=mineral, scenario=scen,
                seed=seed, folder=folder,
            ))
    return records


def _scan_sweep(
    runs_root: Path, scenarios_root: Path,
    minerals: Sequence[str] | None = None,
) -> list[TrajectoryRecord]:
    """Walk ``<runs_root>/<mineral>/<NNNNNN>.csv`` paired with scenario JSONs.

    The flat index ``i*K + k`` (scenario ``i``, seed ``k``) indexes into
    the expanded scenarios list -- exactly what
    ``scripts/run_one_scenario.py`` writes.  We just read the scenarios
    JSON once per mineral and match.
    """
    runs_root = Path(runs_root)
    scenarios_root = Path(scenarios_root)
    minerals = minerals or sorted(ft.COUNTRIES_BY_MINERAL)
    records: list[TrajectoryRecord] = []
    for mineral in minerals:
        runs_dir = runs_root / mineral
        scen_path = scenarios_root / f"{mineral}.json"
        if not runs_dir.is_dir() or not scen_path.is_file():
            continue
        with scen_path.open() as f:
            scenarios = json.load(f)
        for csv in sorted(runs_dir.glob("*.csv")):
            try:
                idx = int(csv.stem)
            except ValueError:
                continue
            if idx >= len(scenarios):
                continue
            scen = scenarios[idx]
            # Some samplers stash the seed inside the scenario dict
            # under "random_seed"; pull it out for bookkeeping.
            seed = int(scen.get("random_seed", 0))
            records.append(TrajectoryRecord(
                csv_path=csv, mineral=mineral, scenario=scen, seed=seed,
            ))
    return records


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

class TrajectoryDataset(Dataset):
    """Yields ``(scenario_features, time_norm, value)`` triples.

    Each ``__getitem__`` returns one *flattened* (scenario, timestep)
    sample.  In a typical epoch we wouldn't iterate every (csv, step) --
    that's 240 trajectories * 1352 steps = 324k samples per epoch on the
    small canonical corpus.  Instead, ``set_subsample(K)`` tells the
    dataset to pre-pick K random timesteps per CSV; ``len`` then becomes
    ``len(records) * K``.  Default K = 100, matching DeepONet practice.

    Caching: scenario feature vectors are cached per record (constant
    over time), and the price array is mmap'd lazily on first access
    via pandas + a small per-record cache.  Loaded arrays survive in
    memory for the dataset's lifetime, so an epoch over the canonical
    corpus is one disk pass.
    """

    def __init__(
        self,
        records: Sequence[TrajectoryRecord],
        target_column: str = DEFAULT_TARGET,
        n_steps: int = ft.DEFAULT_N_STEPS,
        subsample: int = 100,
        seed: int = 0,
    ) -> None:
        if not records:
            raise ValueError("TrajectoryDataset got zero records")
        self.records = list(records)
        self.target_column = target_column
        self.n_steps = int(n_steps)
        self.subsample = int(subsample)
        self._rng = np.random.default_rng(seed)

        # Per-record cached encoded features (constant over time).
        self._features: list[np.ndarray] = [
            ft.encode(r.scenario, n_steps=self.n_steps).astype(np.float32)
            for r in self.records
        ]
        # Per-record cached target arrays, lazily filled on first access.
        self._values: dict[int, np.ndarray] = {}
        # Per-record cached pre-sampled timestep indices, refreshed by
        # ``resample()``.
        self._sampled_steps: list[np.ndarray] = []
        self.resample()

    # ----- public API -----

    def feature_dim(self) -> int:
        return self._features[0].shape[0]

    def resample(self) -> None:
        """Re-pick the per-CSV timestep subsamples for the next epoch."""
        if self.subsample <= 0:
            self._sampled_steps = [np.arange(self.n_steps) for _ in self.records]
        else:
            self._sampled_steps = [
                self._rng.choice(self.n_steps, size=self.subsample, replace=False)
                for _ in self.records
            ]

    def feature_dim_per_mineral(self) -> dict[str, int]:
        """Return ``{mineral: feature_dim}`` over records present in the set."""
        out: dict[str, int] = {}
        for r, feat in zip(self.records, self._features):
            out.setdefault(r.mineral, feat.shape[0])
        return out

    # ----- dataset interface -----

    def __len__(self) -> int:
        steps_per = self.subsample if self.subsample > 0 else self.n_steps
        return len(self.records) * steps_per

    def __getitem__(self, idx: int):
        steps_per = self.subsample if self.subsample > 0 else self.n_steps
        rec_idx, in_rec = divmod(idx, steps_per)
        step = int(self._sampled_steps[rec_idx][in_rec])
        feat = self._features[rec_idx]
        values = self._load_values(rec_idx)
        if step >= len(values):
            step = len(values) - 1
        return (
            torch.from_numpy(feat),
            torch.tensor(step / max(1, self.n_steps - 1), dtype=torch.float32),
            torch.tensor(values[step], dtype=torch.float32),
        )

    # ----- internal -----

    def _load_values(self, rec_idx: int) -> np.ndarray:
        if rec_idx in self._values:
            return self._values[rec_idx]
        rec = self.records[rec_idx]
        col = self.target_column
        df = pd.read_csv(rec.csv_path, usecols=[col])
        arr = df[col].to_numpy(dtype=np.float32)
        self._values[rec_idx] = arr
        return arr


# ---------------------------------------------------------------------------
# Convenience factories
# ---------------------------------------------------------------------------

def from_canonical(
    root: Path = Path("ensemble_runs/2050"),
    mineral: str | None = None,
    **dataset_kwargs,
) -> TrajectoryDataset:
    """Build a :class:`TrajectoryDataset` from ``ensemble_runs/2050/``."""
    records = _scan_canonical_2050(root)
    if mineral is not None:
        records = [r for r in records if r.mineral == mineral]
    return TrajectoryDataset(records, **dataset_kwargs)


def from_sweep(
    runs_root: Path,
    scenarios_root: Path,
    mineral: str | None = None,
    **dataset_kwargs,
) -> TrajectoryDataset:
    """Build a :class:`TrajectoryDataset` from a sweep ``runs/`` tree."""
    minerals = [mineral] if mineral else None
    records = _scan_sweep(runs_root, scenarios_root, minerals=minerals)
    return TrajectoryDataset(records, **dataset_kwargs)


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------

def split_by_unique_scenario(
    records: Sequence[TrajectoryRecord],
    test_frac: float = 0.10,
    val_frac: float = 0.10,
    seed: int = 0,
) -> tuple[list[TrajectoryRecord], list[TrajectoryRecord], list[TrajectoryRecord]]:
    """Split records so seeds of the same scenario stay in the same fold.

    Train-on-some-scenarios / test-on-others is a stricter test than
    train-on-some-seeds-of-every-scenario: the model has to generalise
    across parameter configurations, not just across noise.
    """
    # Group records by scenario "fingerprint" -- a stable key derived
    # from the scenario dict.  Two records sharing a fingerprint share
    # all parameters except the seed.
    def _fp(rec: TrajectoryRecord) -> str:
        sc = rec.scenario
        embargoes = sorted(
            (e["country"], int(e["start_step"]), int(e["duration"]))
            for e in (sc.get("embargoes") or [])
        )
        chokes = sorted(
            (c["chokepoint"], int(c["start_step"]), int(c["duration"]))
            for c in (sc.get("chokepoint_crises") or [])
        )
        # config_overrides is a dict; sort items.
        overrides = tuple(sorted((sc.get("config_overrides") or {}).items()))
        return repr((rec.mineral, embargoes, chokes, overrides))

    groups: dict[str, list[TrajectoryRecord]] = {}
    for rec in records:
        groups.setdefault(_fp(rec), []).append(rec)
    keys = sorted(groups)
    rng = np.random.default_rng(seed)
    rng.shuffle(keys)
    n = len(keys)
    n_test = int(round(test_frac * n))
    n_val = int(round(val_frac * n))
    test_keys = set(keys[:n_test])
    val_keys = set(keys[n_test:n_test + n_val])
    train, val, test = [], [], []
    for k, recs in groups.items():
        if k in test_keys:
            test.extend(recs)
        elif k in val_keys:
            val.extend(recs)
        else:
            train.extend(recs)
    return train, val, test
