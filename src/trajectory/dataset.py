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

**Performance notes.**  Per-CSV pandas loads are slow at scale (40k
CSVs ~~ 30-60 s at construction).  The dataset eagerly stacks every
trajectory into a single dense ``(N_records, T)`` torch tensor at
init, with an optional ``.pt`` cache file so subsequent runs skip the
CSV read entirely.  ``__getitem__`` is then pure tensor indexing
(microseconds), avoiding the per-call ``torch.tensor(...)`` allocation
that bottlenecked the v1 lazy-cache implementation.  Workers spawned
via fork on Linux share the cache tensor copy-on-write -- no per-worker
reload.

Targets are by default the ``Global_Price`` column; pass another column
name to predict something else.
"""

from __future__ import annotations

import hashlib
import json
import time
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
    sample.  In a typical epoch we don't iterate every (csv, step) --
    that's 40k * 1352 = 54M samples; we pre-pick ``subsample`` random
    timesteps per CSV (default 100), so an epoch is ~4M samples.

    **Eager dense cache.**  At construction every CSV is loaded into a
    single ``(N_records, T)`` ``float32`` tensor, plus a corresponding
    ``(N_records, F)`` features tensor.  The CSV-read cost is paid once
    (~30 s for 40k records on Lustre) and from then on every
    ``__getitem__`` is pure tensor indexing.

    **Optional disk cache.**  If ``cache_dir`` is supplied, the values
    tensor is checkpointed to ``<cache_dir>/trajectory_cache_<col>_<digest>.pt``
    after the first build; subsequent constructions reload it in <2 s.
    The digest is over the (sorted) list of csv_path strings so any
    change to the underlying corpus invalidates the cache.

    **Worker-friendly memory.**  The dense tensors live in the parent
    process; PyTorch workers spawned via fork (Linux default) share
    them copy-on-write, so a 4-worker DataLoader does not 4x the
    memory footprint.
    """

    #: Filename version tag.  Bump when the on-disk cache layout changes.
    _CACHE_VERSION = 1

    def __init__(
        self,
        records: Sequence[TrajectoryRecord],
        target_column: str = DEFAULT_TARGET,
        n_steps: int = ft.DEFAULT_N_STEPS,
        subsample: int = 100,
        seed: int = 0,
        cache_dir: Path | None = None,
        verbose: bool = True,
        with_phase: bool = False,
    ) -> None:
        if not records:
            raise ValueError("TrajectoryDataset got zero records")
        self.records = list(records)
        self.target_column = target_column
        self.n_steps = int(n_steps)
        self.subsample = int(subsample)
        self._rng = np.random.default_rng(seed)
        self._verbose = verbose
        self.with_phase = bool(with_phase)

        # Eagerly encode all scenario features (fast: ~ms per record).
        feats = np.stack([
            ft.encode(r.scenario, n_steps=self.n_steps).astype(np.float32)
            for r in self.records
        ])
        self._features: torch.Tensor = torch.from_numpy(feats)

        # Eagerly load all trajectories into a dense tensor.  This is
        # the slow step on a cold cache (~ms per CSV) but is paid only
        # once per Dataset lifetime / cache file.
        self._values: torch.Tensor = self._load_or_build_values(cache_dir)

        # Pre-pick the per-record subsample of timesteps.
        self._sampled_steps: torch.Tensor = self._pick_subsamples()

        # Per-record event tensors for the phase variant.  Shape
        # (N_records, MAX_EVENT_SLOTS) each; only built when needed.
        if self.with_phase:
            from .deeponet import (                # local import to avoid
                MAX_EVENT_SLOTS,                    # circular at module load
                scenario_event_tensors,
            )
            ev_s = torch.zeros((len(self.records), MAX_EVENT_SLOTS),
                               dtype=torch.float32)
            ev_e = torch.zeros_like(ev_s)
            ev_a = torch.zeros_like(ev_s, dtype=torch.bool)
            for i, r in enumerate(self.records):
                s, e, a = scenario_event_tensors(r.scenario, self.n_steps)
                ev_s[i] = s; ev_e[i] = e; ev_a[i] = a
            self._event_starts: torch.Tensor = ev_s
            self._event_ends: torch.Tensor = ev_e
            self._event_active: torch.Tensor = ev_a

    # ----- public API -----

    def feature_dim(self) -> int:
        return int(self._features.shape[1])

    def resample(self) -> None:
        """Re-pick the per-CSV timestep subsamples for the next epoch."""
        self._sampled_steps = self._pick_subsamples()

    def feature_dim_per_mineral(self) -> dict[str, int]:
        """Return ``{mineral: feature_dim}`` over records present in the set."""
        out: dict[str, int] = {}
        for i, r in enumerate(self.records):
            out.setdefault(r.mineral, int(self._features.shape[1]))
        return out

    # ----- dataset interface -----

    def __len__(self) -> int:
        steps_per = self.subsample if self.subsample > 0 else self.n_steps
        return len(self.records) * steps_per

    def __getitem__(self, idx: int):
        # Pure tensor indexing -- no fresh allocations on the hot path.
        # The per-batch collation (default torch.stack) is what actually
        # builds the (B, F) and (B,) tensors the model sees.
        steps_per = self.subsample if self.subsample > 0 else self.n_steps
        rec_idx, in_rec = divmod(idx, steps_per)
        step = int(self._sampled_steps[rec_idx, in_rec])
        t_norm = torch.tensor(step / max(1, self.n_steps - 1), dtype=torch.float32)
        if not self.with_phase:
            return (
                self._features[rec_idx],
                t_norm,
                self._values[rec_idx, step],
            )
        # Phase variant: also return the (3,) event-phase features.
        from .deeponet import compute_phase_features
        phase = compute_phase_features(
            t_norm.unsqueeze(0),
            self._event_starts[rec_idx].unsqueeze(0),
            self._event_ends[rec_idx].unsqueeze(0),
            self._event_active[rec_idx].unsqueeze(0),
        ).squeeze(0)
        return (
            self._features[rec_idx],
            t_norm,
            phase,
            self._values[rec_idx, step],
        )

    # ----- internal -----

    def _cache_path(self, cache_dir: Path | None) -> Path | None:
        if cache_dir is None:
            return None
        # Digest over the canonical-form record fingerprints.  Any
        # change to which CSVs we're loading -> invalidated cache.
        h = hashlib.sha1()
        for r in self.records:
            h.update(str(r.csv_path).encode())
            h.update(b"|")
        digest = h.hexdigest()[:16]
        name = (
            f"trajectory_cache_v{self._CACHE_VERSION}_"
            f"{self.target_column}_T{self.n_steps}_N{len(self.records)}_"
            f"{digest}.pt"
        )
        return Path(cache_dir) / name

    def _load_or_build_values(self, cache_dir: Path | None) -> torch.Tensor:
        cache_path = self._cache_path(cache_dir)
        if cache_path is not None and cache_path.is_file():
            t0 = time.time()
            values = torch.load(cache_path, weights_only=True)
            if values.shape != (len(self.records), self.n_steps):
                # Corrupt or mismatched -- fall through to rebuild.
                if self._verbose:
                    print(f"[trajectory] cache shape mismatch at "
                          f"{cache_path}; rebuilding")
            else:
                if self._verbose:
                    print(f"[trajectory] loaded cache "
                          f"({values.shape[0]} records, "
                          f"{values.shape[1]} steps) "
                          f"in {time.time() - t0:.1f}s")
                return values

        N, T = len(self.records), self.n_steps
        if self._verbose:
            print(f"[trajectory] building values cache: "
                  f"{N} CSVs x {T} steps -> "
                  f"{N * T * 4 / 2**20:.1f} MB")
        values = torch.zeros((N, T), dtype=torch.float32)
        t0 = time.time()
        for i, r in enumerate(self.records):
            df = pd.read_csv(r.csv_path, usecols=[self.target_column])
            arr = df[self.target_column].to_numpy(dtype=np.float32)
            n = min(len(arr), T)
            values[i, :n] = torch.from_numpy(arr[:n])
            if n < T:
                # Pad short trajectories with the last observed value.
                # Should be rare -- run_one_scenario.py writes T rows.
                values[i, n:] = arr[-1]
            if self._verbose and (i + 1) % 5000 == 0:
                rate = (i + 1) / (time.time() - t0)
                eta = (N - i - 1) / max(1.0, rate)
                print(f"[trajectory]  {i + 1:>6d}/{N}  "
                      f"({rate:.0f} CSVs/s, eta {eta:.0f}s)")
        elapsed = time.time() - t0
        if self._verbose:
            print(f"[trajectory] values cache built in {elapsed:.1f}s "
                  f"({N / elapsed:.0f} CSVs/s)")

        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            torch.save(values, cache_path)
            if self._verbose:
                print(f"[trajectory] saved cache to {cache_path}")
        return values

    def _pick_subsamples(self) -> torch.Tensor:
        N = len(self.records)
        if self.subsample <= 0:
            return (torch.arange(self.n_steps, dtype=torch.long)
                    .unsqueeze(0).expand(N, -1).contiguous())
        K = self.subsample
        out = torch.empty((N, K), dtype=torch.long)
        for i in range(N):
            out[i] = torch.from_numpy(
                self._rng.choice(self.n_steps, size=K, replace=False)
            )
        return out

    # Backwards-compat shim so external callers that still import
    # ``_load_values(rec_idx)`` (e.g. older training scripts that peeked
    # at sample values for log-target normalization) keep working.
    def _load_values(self, rec_idx: int) -> np.ndarray:
        return self._values[rec_idx].numpy()


# ---------------------------------------------------------------------------
# Convenience factories
# ---------------------------------------------------------------------------

def from_canonical(
    root: Path = Path("ensemble_runs/2050"),
    mineral: str | None = None,
    cache_dir: Path | None = None,
    **dataset_kwargs,
) -> TrajectoryDataset:
    """Build a :class:`TrajectoryDataset` from ``ensemble_runs/2050/``."""
    records = _scan_canonical_2050(root)
    if mineral is not None:
        records = [r for r in records if r.mineral == mineral]
    return TrajectoryDataset(records, cache_dir=cache_dir, **dataset_kwargs)


def from_sweep(
    runs_root: Path,
    scenarios_root: Path,
    mineral: str | None = None,
    cache_dir: Path | None = None,
    **dataset_kwargs,
) -> TrajectoryDataset:
    """Build a :class:`TrajectoryDataset` from a sweep ``runs/`` tree."""
    minerals = [mineral] if mineral else None
    records = _scan_sweep(runs_root, scenarios_root, minerals=minerals)
    return TrajectoryDataset(records, cache_dir=cache_dir, **dataset_kwargs)


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
