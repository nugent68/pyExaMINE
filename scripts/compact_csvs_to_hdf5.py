#!/usr/bin/env python3
"""Compact per-scenario simulator outputs into per-mineral HDF5 files.

Inputs come from ``run_one_scenario.py`` and can be either format:
  * ``runs/<mineral>/NNNNNN.csv``   (legacy, --format csv)
  * ``runs/<mineral>/NNNNNN.h5``    (new, --format h5; ~5x smaller
                                     and ~10x faster to read here)

This script auto-detects the per-scenario format by extension, reads
all of them for a mineral, and consolidates into a single per-mineral
HDF5 with one ``(N, T)`` ``float32`` dataset per column we care
about.

    runs/lithium/NNNNNN.{csv,h5}  x N -> runs_h5/lithium.h5  (one file)

Layout of the output (h5py-readable, also openable via HDFView):

    /meta/flat_idx     (N,)  int32     -- which scenarios are present
    /meta/columns      attribute      -- list of column names stored
    /meta/n_steps      attribute      -- horizon T
    /Step              (T,)  int32    -- the shared time axis
    /Global_Price      (N, T) float32 -- per-scenario trajectories
    /Total_Mine_Output ...
    ...

Compression: gzip level 6 (HDF5 default).  Trajectory data compresses
~3-5x for these float arrays since neighbouring timesteps are
correlated.  Total per-mineral file size at 10x scale is ~3-5 GB
(vs ~17 GB raw CSV).

Usage:

    uv run python scripts/compact_csvs_to_hdf5.py \\
        --runs       /pscratch/.../surrogate_data/runs \\
        --scenarios  /pscratch/.../surrogate_data/scenarios \\
        --out        /pscratch/.../surrogate_data/runs_h5 \\
        --mineral    lithium    # repeatable; default = all 3
        --n-steps    1352
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import h5py
import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.surrogate import features as ft         # noqa: E402


#: Columns we materialise into the HDF5 by default.  Anything else can
#: be added later by re-running with --columns.  Default set covers
#: the headline price target plus the inputs to the existing scalar
#: targets (Fulfilled / Unfulfilled demand, embargo / chokepoint
#: counts, mine-output proxies).
DEFAULT_COLUMNS = [
    "Step",
    "Global_Price",
    "Marginal_Cost",
    "Cheapest_Active_Cost",
    "Total_Processor_Inventory",
    "Total_Mine_Output",
    "Total_Recycled_Supply",
    "Total_Processor_Throughput",
    "Disrupted_Mines_Count",
    "Embargoed_Mines_Count",
    "Total_Embargoed_Production",
    "Closed_Chokepoints_Count",
    "Fulfilled_Demand_Units",
    "Unfulfilled_Demand_Units",
    "Total_Reserves",
    "Total_In_Transit_Tons",
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runs", type=Path, required=True,
                   help="Directory holding per-mineral CSV trees "
                        "(<mineral>/NNNNNN.csv).")
    p.add_argument("--scenarios", type=Path, required=True,
                   help="Directory holding <mineral>.json scenario "
                        "lists (used to record total scenario count).")
    p.add_argument("--out", type=Path, required=True,
                   help="Output directory; one <mineral>.h5 per "
                        "mineral lands here.")
    p.add_argument("--mineral", action="append",
                   choices=list(ft.COUNTRIES_BY_MINERAL),
                   help="Restrict to specific minerals (repeatable; "
                        "default = all 3).")
    p.add_argument("--columns", default=",".join(DEFAULT_COLUMNS),
                   help="Comma-separated list of columns to extract. "
                        "Default covers Global_Price + everything the "
                        "scalar surrogate's targets need.")
    p.add_argument("--n-steps", type=int, default=ft.DEFAULT_N_STEPS,
                   help="Simulation horizon T (rows per CSV).")
    p.add_argument("--compression", default="gzip",
                   help="HDF5 compression filter ('gzip', 'lzf', "
                        "or 'none').  Default 'gzip'.")
    p.add_argument("--compression-opts", type=int, default=4,
                   help="Compression level (gzip 1-9, default 4).")
    p.add_argument("--chunk-rows", type=int, default=64,
                   help="HDF5 chunk size in scenarios (rows).  "
                        "Default 64 -- a single chunk holds 64 "
                        "trajectories worth of one column.")
    p.add_argument("--progress-every", type=int, default=2000,
                   help="Log progress every N CSVs.")
    return p.parse_args()


def _list_inputs(runs_dir: Path) -> list[tuple[int, Path]]:
    """Sorted ``[(flat_idx, path), ...]`` of per-scenario inputs.

    Accepts both ``NNNNNN.csv`` and ``NNNNNN.h5`` (the new
    ``run_one_scenario.py --format h5`` output).  If both formats
    coexist for the same index, prefers H5 (newer, smaller).
    """
    by_idx: dict[int, Path] = {}
    # CSV first so H5 (if present) overrides.
    for ext in (".csv", ".h5"):
        for p in sorted(runs_dir.glob(f"*{ext}")):
            try:
                idx = int(p.stem)
            except ValueError:
                continue
            by_idx[idx] = p
    return sorted(by_idx.items())


def _read_one_scenario(path: Path, columns: list[str], n_steps: int):
    """Return ``{col: float32 ndarray of length n_steps}`` for one scenario.

    Dispatches by extension: CSV -> pandas.read_csv; H5 -> h5py read
    of the per-column datasets.  Missing columns get zeroed arrays;
    short trajectories get tail-padded (matches dataset.py logic).
    """
    import h5py
    import numpy as np
    out: dict[str, np.ndarray] = {}
    if path.suffix == ".h5":
        with h5py.File(path, "r") as f:
            for col in columns:
                if col in f:
                    arr = f[col][:].astype(np.float32, copy=False)
                else:
                    arr = np.zeros(n_steps, dtype=np.float32)
                out[col] = _fit_to_length(arr, n_steps)
    else:                                            # .csv
        df = pd.read_csv(path, usecols=lambda c: c in columns)
        for col in columns:
            if col in df.columns:
                arr = df[col].to_numpy(dtype=np.float32)
            else:
                arr = np.zeros(n_steps, dtype=np.float32)
            out[col] = _fit_to_length(arr, n_steps)
    return out


def _fit_to_length(arr, n_steps: int):
    """Truncate or tail-pad a 1-D array to exactly ``n_steps`` entries."""
    import numpy as np
    n = min(len(arr), n_steps)
    if len(arr) == n_steps:
        return arr.astype(np.float32, copy=False)
    out = np.zeros(n_steps, dtype=np.float32)
    if n > 0:
        out[:n] = arr[:n]
        if n < n_steps:
            out[n:] = arr[-1]
    return out


def _compact_one(
    mineral: str,
    runs_dir: Path,
    scenarios_path: Path,
    out_path: Path,
    columns: list[str],
    n_steps: int,
    compression: str,
    compression_opts: int,
    chunk_rows: int,
    progress_every: int,
) -> None:
    inputs = _list_inputs(runs_dir)
    if not inputs:
        print(f"[{mineral}] no per-scenario inputs at {runs_dir}; skipping")
        return
    flat_idxs = [i for i, _ in inputs]
    N, T = len(inputs), n_steps
    n_h5 = sum(1 for _, p in inputs if p.suffix == ".h5")
    print(f"[{mineral}] compacting {N} files "
          f"(CSV={N - n_h5}, H5={n_h5}; {len(columns)} cols, "
          f"T={T}) -> {out_path}")

    # Total scenario count (informational; lets readers know if the
    # flat-idx coverage is partial).
    try:
        with scenarios_path.open() as f:
            total_scenarios = len(json.load(f))
    except Exception:
        total_scenarios = -1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Ensure no stale partial file.
    if out_path.exists():
        out_path.unlink()

    # Compression keyword is None (passed as compression=None) for
    # 'none', else the literal name.
    comp = None if compression == "none" else compression
    comp_opts = compression_opts if comp == "gzip" else None

    t0 = time.time()
    with h5py.File(out_path, "w") as h:
        # Metadata first.
        meta = h.create_group("meta")
        meta.create_dataset("flat_idx",
                            data=np.asarray(flat_idxs, dtype=np.int32))
        meta.attrs["mineral"] = mineral
        meta.attrs["n_steps"] = T
        meta.attrs["n_scenarios_in_json"] = total_scenarios
        meta.attrs["columns"] = ",".join(columns)

        # Pre-allocate one (N, T) dataset per column.  Step gets its
        # own (T,) dataset because it's identical across all rows.
        col_datasets: dict[str, h5py.Dataset] = {}
        for col in columns:
            if col == "Step":
                # Stored once; lazy-fill from the first CSV below.
                col_datasets[col] = h.create_dataset(
                    "Step", shape=(T,), dtype=np.int32,
                )
            else:
                col_datasets[col] = h.create_dataset(
                    col, shape=(N, T), dtype=np.float32,
                    chunks=(min(chunk_rows, N), T),
                    compression=comp,
                    compression_opts=comp_opts,
                )

        step_filled = False
        # Buffer in chunks of `chunk_rows` to write whole HDF5 chunks
        # in one go, reducing compression overhead.
        buf: dict[str, np.ndarray] = {
            col: np.zeros((chunk_rows, T), dtype=np.float32)
            for col in columns if col != "Step"
        }
        buf_n = 0

        def _flush(start: int, n: int) -> None:
            for col, arr in buf.items():
                col_datasets[col][start:start + n] = arr[:n]

        i = 0
        for idx, src_path in inputs:
            cols_arr = _read_one_scenario(src_path, columns, T)
            if not step_filled and "Step" in cols_arr:
                col_datasets["Step"][:] = cols_arr["Step"].astype(np.int32)
                step_filled = True
            for col in columns:
                if col == "Step":
                    continue
                buf[col][buf_n] = cols_arr[col]

            buf_n += 1
            i += 1
            if buf_n == chunk_rows:
                _flush(i - buf_n, buf_n)
                buf_n = 0

            if progress_every and i % progress_every == 0:
                rate = i / (time.time() - t0)
                eta = (N - i) / max(1.0, rate)
                print(f"[{mineral}]  {i:>6d}/{N}  "
                      f"({rate:>5.0f} files/s  eta {eta:>5.0f}s)")

        # Flush trailing partial buffer.
        if buf_n > 0:
            _flush(i - buf_n, buf_n)

    elapsed = time.time() - t0
    size_mb = out_path.stat().st_size / 2 ** 20
    print(f"[{mineral}] done in {elapsed:.1f}s ({N / elapsed:.0f} CSVs/s).  "
          f"output {size_mb:.1f} MB.")


def main() -> int:
    args = _parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    columns = [c.strip() for c in args.columns.split(",") if c.strip()]
    minerals = args.mineral or list(ft.COUNTRIES_BY_MINERAL)
    for mineral in minerals:
        runs_dir = args.runs / mineral
        scenarios_path = args.scenarios / f"{mineral}.json"
        out_path = args.out / f"{mineral}.h5"
        _compact_one(
            mineral=mineral,
            runs_dir=runs_dir,
            scenarios_path=scenarios_path,
            out_path=out_path,
            columns=columns,
            n_steps=args.n_steps,
            compression=args.compression,
            compression_opts=args.compression_opts,
            chunk_rows=args.chunk_rows,
            progress_every=args.progress_every,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
