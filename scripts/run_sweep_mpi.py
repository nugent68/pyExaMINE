#!/usr/bin/env python3
"""Run a pyExaMINE sweep with MPI-parallel direct-to-HDF5 writes.

Replaces the ``xargs -P run_one_scenario.py + compact_csvs_to_hdf5.py``
chain for large ensemble runs. Each MPI rank gets a contiguous block
of scenario indices, runs each scenario serially, and writes the
trajectory row directly into a shared HDF5 file via the h5py MPI-IO
driver. Resumable: a ``meta/done`` bitmap in the output file lets a
re-run skip already-completed rows.

Typical Perlmutter invocation (see scripts/sweep_mpi.slurm):

    srun --cpu-bind=cores --mpi=cray_shasta \
        shifter --image=docker:nugent68/pyexamine:us-policy-mpi \
                --module=mpich \
                --volume="$RUNDIR:/data" --volume="$REPO:/work:ro" \
        /app/.venv/bin/python /work/scripts/run_sweep_mpi.py \
            --scenarios /work/scenarios/sweep.json \
            --output    /data/sweep.h5

Local single-process (for laptop testing):

    /app/.venv/bin/python scripts/run_sweep_mpi.py \
        --scenarios scenarios/foo.json --output /tmp/sweep.h5

Output file layout (single-mineral; all scenarios must share the
same mineral and n_steps):

    /Step                       (T,)   int32
    /Global_Price               (N, T) float32
    /Total_Mine_Output          (N, T) float32
    ... + the rest of DEFAULT_COLUMNS
    /meta/done                  (N,)   bool       per-scenario completion
    /meta/columns               attr   csv string
    /meta/n_scenarios           attr   int
    /meta/n_steps               attr   int
    /meta/mineral               attr   string
    /meta/scenarios_json        attr   string (basename of input)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Try MPI; fall back to a single-process degenerate mode for laptop
# testing where mpi4py may not be available.
try:
    from mpi4py import MPI
    _HAVE_MPI = True
except ImportError:
    _HAVE_MPI = False

import h5py
import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.config.lithium_config import LITHIUM_CONFIG       # noqa: E402
from src.config.nickel_config import NICKEL_CONFIG         # noqa: E402
from src.config.platinum_config import PLATINUM_CONFIG     # noqa: E402
from src.model.supply_chain_model import MineralSupplyChainModel  # noqa: E402

_BASE_CONFIGS = {
    "lithium":  LITHIUM_CONFIG,
    "nickel":   NICKEL_CONFIG,
    "platinum": PLATINUM_CONFIG,
}

# Mirrors compact_csvs_to_hdf5.py's DEFAULT_COLUMNS plus
# Avg_Manufacturer_Intensity (added for substitution analysis).
# Any extra columns the model emits but aren't listed here get
# silently dropped. Add new columns here when the data collector
# grows.
DEFAULT_COLUMNS = (
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
    "Avg_Manufacturer_Intensity",
    "Mass_Balance_Discrepancy",
    "Strategic_Reserve_Stock",
    "Strategic_Reserve_Bought",
    "Strategic_Reserve_Released",
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scenarios", type=Path, required=True,
                   help="Path to a scenarios JSON (list of scenario dicts; "
                        "the schema produced by build_policy_scenarios.py).")
    p.add_argument("--output", type=Path, required=True,
                   help="Path to the shared HDF5 sweep file. Created if "
                        "absent; reopened with skip-existing semantics if "
                        "it already exists (used for resume after a wall).")
    p.add_argument("--columns", default=",".join(DEFAULT_COLUMNS),
                   help="Comma-separated list of DataCollector columns to "
                        "extract from each scenario's model_data. Defaults "
                        "to the standard surrogate + diagnostics set.")
    p.add_argument("--flush-every", type=int, default=50,
                   help="Flush the HDF5 file every N completed scenarios "
                        "per rank (resumability vs throughput trade-off).")
    p.add_argument("--compression", default="gzip",
                   choices=["gzip", "lzf", "none"],
                   help="Per-row chunk compression filter. Default gzip-4.")
    p.add_argument("--compression-opts", type=int, default=4,
                   help="Compression level (gzip 1-9; default 4).")
    return p.parse_args()


def _build_config(scenario: dict) -> dict:
    """Replicate run_one_scenario.py's per-scenario config build."""
    mineral = scenario["mineral"]
    if mineral not in _BASE_CONFIGS:
        raise ValueError(f"unknown mineral '{mineral}'")
    cfg = dict(_BASE_CONFIGS[mineral])
    cfg.update(scenario.get("config_overrides", {}) or {})
    cfg["random_seed"] = int(scenario.get("random_seed", 42))
    cfg["n_steps"] = int(scenario.get("n_steps", cfg.get("n_steps", 1352)))
    if scenario.get("embargoes"):
        cfg["political_embargoes"] = list(scenario["embargoes"])
    if scenario.get("chokepoint_crises"):
        cfg["chokepoint_crises"] = list(scenario["chokepoint_crises"])
    co = scenario.get("country_overrides")
    us_policy = scenario.get("us_policy")
    if co or us_policy:
        merged = dict(cfg.get("country_overrides", {}) or {})
        if co:
            merged.update(co)
        if us_policy is not None:
            merged["USA"] = us_policy
        cfg["country_overrides"] = merged
    return cfg


def _block_partition(N: int, rank: int, size: int):
    """Return [start, end) for this rank's contiguous block of indices."""
    start = rank * N // size
    end = (rank + 1) * N // size
    return start, end


def _run_one(scenario: dict, columns: list[str], T: int):
    """Run one scenario and return ``{col: float32 ndarray[T]}``.

    Missing columns get tail-padded zeros so the writer never has to
    branch on schema. Step is returned as int32 in float32 representation
    (the column has integer values 0..T-1; converting back happens at
    file-init time for the shared /Step dataset).
    """
    cfg = _build_config(scenario)
    model = MineralSupplyChainModel(cfg)
    model.run_model(T)
    df = model.get_model_data()
    out = {}
    for col in columns:
        if col in df.columns:
            arr = df[col].to_numpy(dtype=np.float32)
        else:
            arr = np.zeros(T, dtype=np.float32)
        if len(arr) < T:
            padded = np.zeros(T, dtype=np.float32)
            padded[:len(arr)] = arr
            if len(arr) > 0:
                padded[len(arr):] = arr[-1]
            arr = padded
        elif len(arr) > T:
            arr = arr[:T].astype(np.float32, copy=False)
        out[col] = arr
    return out


def main() -> int:
    args = _parse_args()
    columns = [c.strip() for c in args.columns.split(",") if c.strip()]
    if "Step" not in columns:
        columns = ["Step"] + columns

    if _HAVE_MPI:
        comm = MPI.COMM_WORLD
        rank = comm.Get_rank()
        size = comm.Get_size()
    else:
        comm = None
        rank, size = 0, 1

    # Rank 0 reads scenarios + broadcasts.
    if rank == 0:
        with args.scenarios.open() as f:
            scenarios = json.load(f)
        if not isinstance(scenarios, list) or not scenarios:
            raise SystemExit(f"{args.scenarios}: expected non-empty JSON list")
        N = len(scenarios)
        T = int(scenarios[0].get("n_steps", _BASE_CONFIGS[scenarios[0]["mineral"]].get("n_steps", 1352)))
        mineral = scenarios[0]["mineral"]
        # Single-mineral / single-n_steps assertions: the file is
        # pre-allocated with one (N, T) dataset per column, so all
        # scenarios must agree.
        for i, s in enumerate(scenarios):
            if s["mineral"] != mineral:
                raise SystemExit(
                    f"scenario {i} has mineral={s['mineral']!r}, expected "
                    f"{mineral!r} (mixed-mineral sweeps not supported)"
                )
            si_t = int(s.get("n_steps", T))
            if si_t != T:
                raise SystemExit(
                    f"scenario {i} has n_steps={si_t}, expected {T} "
                    f"(mixed-horizon sweeps not supported)"
                )
        print(f"[rank 0] {N} scenarios, mineral={mineral}, T={T}, "
              f"size={size}", flush=True)
    else:
        scenarios = N = T = mineral = None

    if _HAVE_MPI:
        scenarios = comm.bcast(scenarios, root=0)
        N = comm.bcast(N, root=0)
        T = comm.bcast(T, root=0)
        mineral = comm.bcast(mineral, root=0)

    # Collective file open. All ranks call h5py.File together with
    # the MPI-IO driver; HDF5 routes the metadata writes through MPI
    # so there's no rank-0-serial / Lustre-cache-coherence race. For
    # resume runs, the file already exists -- open with mode='r+'.
    # For fresh runs we want mode='w'. Rank 0 decides which based on
    # file existence and broadcasts.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    comp = None if args.compression == "none" else args.compression
    comp_opts = args.compression_opts if comp == "gzip" else None

    if rank == 0:
        # Detect whether to create-fresh or resume.
        is_fresh = not args.output.exists()
    else:
        is_fresh = None
    if _HAVE_MPI:
        is_fresh = comm.bcast(is_fresh, root=0)

    kw = {"libver": "latest"}
    if _HAVE_MPI:
        kw.update(driver="mpio", comm=comm)
    kw["mode"] = "w" if is_fresh else "r+"
    f = h5py.File(args.output, **kw)

    # Collective dataset allocation. All ranks call create_dataset
    # with identical args; HDF5 MPI-IO mode handles this correctly.
    # On resume (mode='r+'), datasets already exist and we skip
    # creation; meta/done is loaded below.
    if is_fresh:
        # Explicitly create the /meta group BEFORE any "meta/..."
        # children, so attribute writes targeting it have a guaranteed
        # parent. Under MPI-IO, auto-creation of intermediate groups
        # via create_dataset("meta/done") proved unreliable across
        # ranks ("component not found" KeyErrors at the attrs write).
        meta = f.create_group("meta")
        # /Step (1-D shared time axis)
        f.create_dataset("Step", shape=(T,), dtype="i4")
        # Per-column (N, T) datasets with one-row chunks
        for col in columns:
            if col == "Step":
                continue
            dset_kw = {"shape": (N, T), "dtype": "f4", "chunks": (1, T)}
            if comp is not None:
                dset_kw["compression"] = comp
                if comp_opts is not None:
                    dset_kw["compression_opts"] = comp_opts
            f.create_dataset(col, **dset_kw)
        # /meta/done bitmap (now created as a child of the existing
        # /meta group, not via path auto-creation).
        meta.create_dataset("done", shape=(N,), dtype="?")

        # /meta attrs: under MPI-IO, attribute writes are metadata ops
        # that must be CALLED by all ranks (with identical values).
        meta.attrs["columns"] = ",".join(columns)
        meta.attrs["n_scenarios"] = int(N)
        meta.attrs["n_steps"] = int(T)
        meta.attrs["mineral"] = mineral
        meta.attrs["scenarios_json"] = str(args.scenarios.name)
        # Step is a small 1-D dataset; only rank 0 needs to write its
        # values, and the default (independent) write mode lets the
        # other ranks no-op without coordination.
        if rank == 0:
            f["Step"][:] = np.arange(T, dtype=np.int32)

    if _HAVE_MPI:
        comm.Barrier()

    # Block partition + skip-already-done.
    start, end = _block_partition(N, rank, size)
    done = f["meta/done"][:]
    todo = [i for i in range(start, end) if not done[i]]

    if rank == 0:
        n_done_total = int(done.sum())
        print(f"[rank 0] {n_done_total}/{N} scenarios already done "
              f"(resume); each rank has {(end - start)} assigned, "
              f"{len(todo)} to do on rank 0", flush=True)

    # Compute + write each assigned row.
    t0 = time.time()
    n_done_local = 0
    n_failed = 0
    for i in todo:
        try:
            arrays = _run_one(scenarios[i], columns, T)
        except Exception as e:  # noqa: BLE001
            n_failed += 1
            print(f"[rank {rank}] scenario {i} FAILED: {type(e).__name__}: {e}",
                  flush=True)
            continue
        # Write each column's row. Independent I/O -- each rank's row
        # is its own chunk so there's no collision.
        for col in columns:
            if col == "Step":
                continue
            f[col][i, :] = arrays[col]
        f["meta/done"][i] = True
        n_done_local += 1
        if n_done_local % args.flush_every == 0:
            f.flush()
            if rank == 0:
                elapsed = time.time() - t0
                print(f"[rank 0] {n_done_local}/{len(todo)} done; "
                      f"elapsed {elapsed:.0f}s", flush=True)

    f.flush()
    f.close()

    # Summary print from rank 0.
    if _HAVE_MPI:
        local_counts = comm.gather((n_done_local, n_failed), root=0)
    else:
        local_counts = [(n_done_local, n_failed)]
    if rank == 0:
        total_done = sum(d for d, _ in local_counts)
        total_failed = sum(fl for _, fl in local_counts)
        elapsed = time.time() - t0
        print(f"\n[rank 0] sweep done: {total_done} scenarios computed, "
              f"{total_failed} failed; elapsed {elapsed:.0f} s "
              f"({total_done / max(1, elapsed):.1f} scn/s aggregate)",
              flush=True)
        return 1 if total_failed > 0 else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
