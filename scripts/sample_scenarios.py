#!/usr/bin/env python3
"""Sample diverse pyExaMINE scenarios for surrogate-model training.

Emits one JSON file per mineral plus an index file. Each scenario is a
self-contained dict that ``run_simulation.py`` (or scripts that call
``MineralSupplyChainModel`` directly) can consume verbatim.

Usage:
    uv run python scripts/sample_scenarios.py --n 2000 --out scenarios/

Produces:
    scenarios/lithium.json     -- list of 2000 lithium scenarios
    scenarios/nickel.json
    scenarios/platinum.json
    scenarios/index.json       -- {mineral: count, ...} + master seed

The output JSON layout matches ``MineralSupplyChainModel(config)``'s
input contract -- ``embargoes`` and ``chokepoint_crises`` lists are
passed straight through to the model config; ``config_overrides`` is
merged into the per-mineral config dict before model construction.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from the repo root without installing the package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.surrogate.sampling import sample_scenarios, expand_with_seeds   # noqa: E402
from src.surrogate import features as ft                                 # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--n", type=int, default=2000,
        help="Number of scenarios per mineral (default 2000).",
    )
    p.add_argument(
        "--out", type=Path, default=Path("scenarios"),
        help="Output directory; one JSON per mineral lands here.",
    )
    p.add_argument(
        "--mineral", choices=list(ft.COUNTRIES_BY_MINERAL),
        action="append",
        help="Restrict to a specific mineral. May be repeated; defaults to all 3.",
    )
    p.add_argument(
        "--seed", type=int, default=0,
        help="Master RNG seed (default 0). Re-running with the same seed and "
             "--n value reproduces the same scenario list bit-identically.",
    )
    p.add_argument(
        "--n-steps", type=int, default=ft.DEFAULT_N_STEPS,
        help=f"Simulation horizon written into each scenario (default "
             f"{ft.DEFAULT_N_STEPS}; matches the 2050 NetZero canonical run).",
    )
    p.add_argument(
        "--seeds-per-scenario", type=int, default=1, dest="seeds_per_scenario",
        help=(
            "Replicate each scenario this many times with different "
            "random_seed values. Default 1 (legacy single-seed behavior). "
            "Use 20 to produce per-scenario ensemble training data: the "
            "expanded JSON has ``--n * --seeds-per-scenario`` entries, with "
            "underlying scenario k occupying flat indices "
            "[k*S, k*S + S - 1]. The dataset builder uses this layout to "
            "group seeds when computing per-scenario mean / std targets."
        ),
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    minerals = args.mineral or list(ft.COUNTRIES_BY_MINERAL)
    counts: dict[str, int] = {}
    n_unique: dict[str, int] = {}
    for i, mineral in enumerate(minerals):
        # Per-mineral seed offset so scenarios for different minerals
        # don't share the same continuous-knob LHS draws.
        scenarios = sample_scenarios(
            n=args.n, mineral=mineral,
            seed=args.seed + 1000 * i, n_steps=args.n_steps,
        )
        n_unique[mineral] = len(scenarios)

        if args.seeds_per_scenario > 1:
            scenarios = expand_with_seeds(scenarios, args.seeds_per_scenario)

        out_path = args.out / f"{mineral}.json"
        with out_path.open("w") as f:
            json.dump(scenarios, f, indent=1)
        counts[mineral] = len(scenarios)
        if args.seeds_per_scenario > 1:
            print(f"  wrote {out_path} ({len(scenarios)} entries = "
                  f"{n_unique[mineral]} scenarios x {args.seeds_per_scenario} seeds)")
        else:
            print(f"  wrote {out_path} ({len(scenarios)} scenarios)")

    index = {
        "n_per_mineral": counts,
        "n_unique_scenarios": n_unique,
        "seeds_per_scenario": args.seeds_per_scenario,
        "master_seed": args.seed,
        "n_steps": args.n_steps,
        "feature_dim": {m: ft.feature_dim(m) for m in minerals},
    }
    with (args.out / "index.json").open("w") as f:
        json.dump(index, f, indent=2)
    print(f"  wrote {args.out / 'index.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
