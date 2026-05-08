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

import contextlib
import fcntl
import hashlib
import json
import os
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


@contextlib.contextmanager
def _file_lock(lock_path: Path):
    """Advisory ``flock`` over ``lock_path`` for cooperative cache builds.

    Multiple processes running training on the same record set will
    race to build the trajectory tensor cache; this lock serialises
    the build so only one process pays the CSV-read cost and the
    others reload the finished ``.pt``.  Lock is released when the
    context exits.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


#: Default per-step CSV column predicted by the v1 trajectory surrogate.
DEFAULT_TARGET = "Global_Price"


@dataclass
class TrajectoryRecord:
    """One trajectory's metadata.

    Source can be either an individual CSV (``csv_path`` is set) or a
    row inside a per-mineral compacted HDF5 (``h5_path`` + ``h5_row``
    are set).  See :func:`from_sweep` and :func:`from_hdf5_sweep` for
    the two factory paths.
    """
    csv_path: Path
    mineral: str
    scenario: dict
    seed: int | None = None
    folder: str | None = None    # for canonical runs
    h5_path: Path | None = None
    h5_row: int | None = None    # row index within h5_path's data array


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
        with_hybrid: bool = False,
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
        self.with_hybrid = bool(with_hybrid)
        if self.with_phase and self.with_hybrid:
            raise ValueError("with_phase and with_hybrid are mutually exclusive")

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

        # Per-record tensors for the hybrid variant: baseline knobs +
        # per-event (id, start_norm, log_duration, active).
        if self.with_hybrid:
            from .hybrid import (
                MAX_EVENTS, encode_baseline_knobs, encode_events,
            )
            mineral = self.records[0].mineral
            kn_dim = encode_baseline_knobs(self.records[0].scenario).shape[0]
            kn_t = torch.zeros((len(self.records), kn_dim), dtype=torch.float32)
            ids_t = torch.zeros((len(self.records), MAX_EVENTS), dtype=torch.long)
            st_t = torch.zeros_like(ids_t, dtype=torch.float32)
            du_t = torch.zeros_like(st_t)
            ac_t = torch.zeros_like(ids_t, dtype=torch.bool)
            for i, r in enumerate(self.records):
                kn_t[i] = encode_baseline_knobs(r.scenario)
                ids, st, du, ac = encode_events(
                    r.scenario, mineral, n_steps=self.n_steps,
                )
                ids_t[i] = ids; st_t[i] = st; du_t[i] = du; ac_t[i] = ac
            self._knob_features: torch.Tensor = kn_t
            self._event_ids: torch.Tensor = ids_t
            self._event_starts_norm: torch.Tensor = st_t
            self._event_log_durations: torch.Tensor = du_t
            self._event_active_h: torch.Tensor = ac_t

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
        if self.with_hybrid:
            # 6-tuple for HybridTrajectoryModel.forward.
            return (
                self._knob_features[rec_idx],
                t_norm,
                self._event_ids[rec_idx],
                self._event_starts_norm[rec_idx],
                self._event_log_durations[rec_idx],
                self._event_active_h[rec_idx],
                self._values[rec_idx, step],
            )
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

        def _try_load() -> torch.Tensor | None:
            if cache_path is None or not cache_path.is_file():
                return None
            t0 = time.time()
            try:
                values = torch.load(cache_path, weights_only=True)
            except Exception:                       # pickle, EOF, etc.
                return None
            if values.shape != (len(self.records), self.n_steps):
                if self._verbose:
                    print(f"[trajectory] cache shape mismatch at "
                          f"{cache_path}; will rebuild")
                return None
            if self._verbose:
                print(f"[trajectory] loaded cache "
                      f"({values.shape[0]} records, "
                      f"{values.shape[1]} steps) "
                      f"in {time.time() - t0:.1f}s")
            return values

        # Fast path: cache is ready, no lock needed.
        loaded = _try_load()
        if loaded is not None:
            return loaded

        # Slow path: take a per-cache-file flock so only one of N
        # concurrent training runs actually builds the .pt; the
        # others wait, see the file appear, and load it on second try.
        if cache_path is not None:
            lock_path = cache_path.with_suffix(cache_path.suffix + ".lock")
            with _file_lock(lock_path):
                # Another process may have built the cache while we
                # were blocked -- retry the load before doing work.
                loaded = _try_load()
                if loaded is not None:
                    return loaded
                values = self._build_values_from_csvs()
                tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
                torch.save(values, tmp_path)
                tmp_path.replace(cache_path)        # atomic on POSIX
                if self._verbose:
                    print(f"[trajectory] saved cache to {cache_path}")
                return values

        # No cache_dir: build in-memory and don't persist.
        return self._build_values_from_csvs()

    def _build_values_from_csvs(self) -> torch.Tensor:
        # Dispatch: if every record points at an HDF5 source, use the
        # bulk HDF5 path (orders of magnitude faster on 40k+ records).
        if self.records and all(r.h5_path is not None for r in self.records):
            return self._build_values_from_hdf5()
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
        return values

    def _build_values_from_hdf5(self) -> torch.Tensor:
        """Bulk-load values from per-mineral HDF5 files.

        Groups records by their ``h5_path`` (one per mineral), opens
        each file once, and pulls the target-column slice for the
        rows we need in a single read.  Orders of magnitude faster
        than the per-CSV path at scale.
        """
        import h5py                                  # local import
        from collections import defaultdict
        N, T = len(self.records), self.n_steps
        if self._verbose:
            print(f"[trajectory] building values cache from HDF5: "
                  f"{N} records x {T} steps -> "
                  f"{N * T * 4 / 2**20:.1f} MB")
        values = torch.zeros((N, T), dtype=torch.float32)
        by_h5: dict[Path, list[tuple[int, int]]] = defaultdict(list)
        for i, r in enumerate(self.records):
            by_h5[r.h5_path].append((i, int(r.h5_row)))
        t0 = time.time()
        for h5_path, rows in by_h5.items():
            with h5py.File(h5_path, "r") as h:
                if self.target_column not in h:
                    raise KeyError(
                        f"{h5_path} has no dataset {self.target_column!r}"
                    )
                ds = h[self.target_column]
                ds_T = ds.shape[1]
                if ds_T < T:
                    raise ValueError(
                        f"{h5_path} has T={ds_T}; expected T>={T}"
                    )
                # HDF5 fancy indexing requires sorted indices; sort
                # rows then unscramble afterwards.
                rows.sort(key=lambda x: x[1])
                wanted = np.asarray([r for _, r in rows], dtype=np.int64)
                arr = ds[wanted, :T]                 # (M, T) float32
            for (i, _), src_row in zip(rows, range(len(rows))):
                values[i] = torch.from_numpy(arr[src_row])
            if self._verbose:
                rate = len(rows) / max(0.001, time.time() - t0)
                print(f"[trajectory]   {h5_path.name}: "
                      f"{len(rows)} records ({rate:.0f}/s)")
        elapsed = time.time() - t0
        if self._verbose:
            print(f"[trajectory] HDF5 values cache built in "
                  f"{elapsed:.1f}s ({N / elapsed:.0f} records/s)")
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


def _scan_hdf5_sweep(
    h5_root: Path,
    scenarios_root: Path,
    minerals: Sequence[str] | None = None,
) -> list[TrajectoryRecord]:
    """Walk ``<h5_root>/<mineral>.h5`` paired with scenario JSONs.

    Each row in the per-mineral HDF5's ``meta/flat_idx`` dataset
    corresponds to one (scenario, seed) combination -- exactly the
    same flat-index convention as the CSV tree, just denser on disk.
    """
    import h5py
    h5_root = Path(h5_root)
    scenarios_root = Path(scenarios_root)
    minerals = minerals or sorted(ft.COUNTRIES_BY_MINERAL)
    records: list[TrajectoryRecord] = []
    for mineral in minerals:
        h5_path = h5_root / f"{mineral}.h5"
        scen_path = scenarios_root / f"{mineral}.json"
        if not h5_path.is_file() or not scen_path.is_file():
            continue
        with scen_path.open() as f:
            scenarios = json.load(f)
        with h5py.File(h5_path, "r") as h:
            flat_idxs = h["meta/flat_idx"][:]
        for h5_row, flat_idx in enumerate(flat_idxs):
            i = int(flat_idx)
            if i < 0 or i >= len(scenarios):
                continue
            scen = scenarios[i]
            seed = int(scen.get("random_seed", 0))
            records.append(TrajectoryRecord(
                csv_path=h5_path,         # legacy field; not read here
                mineral=mineral,
                scenario=scen, seed=seed,
                h5_path=h5_path,
                h5_row=int(h5_row),
            ))
    return records


def from_hdf5_sweep(
    h5_root: Path,
    scenarios_root: Path,
    mineral: str | None = None,
    cache_dir: Path | None = None,
    **dataset_kwargs,
) -> TrajectoryDataset:
    """Build a :class:`TrajectoryDataset` from per-mineral HDF5 files.

    Companion to :func:`from_sweep`; takes the output of
    ``scripts/compact_csvs_to_hdf5.py``.  HDF5 is much faster to read
    in bulk (no per-file Lustre stat tax, single decompression pass)
    so the values cache builds in seconds instead of minutes.
    """
    minerals = [mineral] if mineral else None
    records = _scan_hdf5_sweep(h5_root, scenarios_root, minerals=minerals)
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
