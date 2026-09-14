"""Generate diverse pyExaMINE scenarios for surrogate-model training.

Sampling strategy (per mineral):

* **Stratified by event multiplicity.** Each scenario is assigned a
  shape (n_embargoes, n_chokepoints) drawn from a fixed mix that
  resembles the questions users actually ask:

      shape           weight
      0 + 0           0.10   pure baseline (other knobs varied)
      1 + 0           0.30
      2 + 0           0.20
      0 + 1           0.10
      1 + 1           0.15
      2 + 1           0.10
      3 + 1           0.05

  This keeps simple baselines well-represented while ensuring the
  multi-event tail (where most of the interesting interactions live)
  gets sampled too.

* **Latin Hypercube on continuous knobs.** All ``CONFIG_KNOBS`` are
  jointly LHS-sampled across the full training set, so the marginal
  coverage of each knob is uniform regardless of which scenarios end
  up active.

* **Per-event sampling.** Each "active" embargo or chokepoint slot
  draws:
    - country / chokepoint uniformly from the valid set
    - start_step uniformly in SUPPORTED_START_STEP
    - duration log-uniformly in SUPPORTED_DURATION (so 1-month and
      5-year embargoes get equal probability mass on a log scale).

* **Random seed per scenario.** A fresh seed is assigned so the ABM
  trace differs across scenarios. We do not vary the seed *within*
  a scenario at this phase (Phase 3 paired-ensemble training will).

Scenarios are emitted as a list of dicts in the same JSON shape that
``run_simulation.py`` accepts as config overrides.
"""

from __future__ import annotations

import math
import random
from typing import Sequence

import numpy as np
from scipy.stats import qmc

from . import features as ft


#: Multiplicities and their sampling weights.
SCENARIO_SHAPES: list[tuple[int, int, float]] = [
    # (n_embargoes, n_chokepoints, weight)
    (0, 0, 0.10),
    (1, 0, 0.30),
    (2, 0, 0.20),
    (0, 1, 0.10),
    (1, 1, 0.15),
    (2, 1, 0.10),
    (3, 1, 0.05),
]


def _draw_shape(rng: random.Random) -> tuple[int, int]:
    weights = [w for _, _, w in SCENARIO_SHAPES]
    pick = rng.choices(SCENARIO_SHAPES, weights=weights, k=1)[0]
    return pick[0], pick[1]


def _log_uniform(lo: int, hi: int, rng: random.Random) -> int:
    """Draw an integer log-uniformly in [lo, hi]."""
    if lo <= 0 or hi < lo:
        return lo
    log_lo = math.log(lo)
    log_hi = math.log(hi)
    return max(lo, min(hi, int(round(math.exp(rng.uniform(log_lo, log_hi))))))


def _uniform_int(lo: int, hi: int, rng: random.Random) -> int:
    return rng.randint(lo, hi)


def _draw_event_set(n: int, choices: Sequence[str], rng: random.Random) -> list[str]:
    """Draw ``n`` distinct event hosts (country or chokepoint) without replacement."""
    if n <= 0 or not choices:
        return []
    if n >= len(choices):
        return list(choices)
    return rng.sample(list(choices), n)


def sample_scenarios(
    n: int,
    mineral: str,
    seed: int = 0,
    n_steps: int = ft.DEFAULT_N_STEPS,
) -> list[dict]:
    """Generate ``n`` diverse scenarios for the named mineral.

    Args:
        n: Number of scenarios to produce.
        mineral: One of ``COUNTRIES_BY_MINERAL`` keys.
        seed: Master RNG seed; controls scenario shapes, event hosts,
            timings, knob values, and the per-scenario random_seed
            field. Re-running with the same seed produces the same
            scenario list -- important for reproducible training data.
        n_steps: Simulation horizon (used to bound start_step).

    Returns:
        List of ``n`` scenario dicts. Each dict has keys
        ``mineral``, ``n_steps``, ``random_seed``, ``embargoes``,
        ``chokepoint_crises``, ``config_overrides``.
    """
    if mineral not in ft.COUNTRIES_BY_MINERAL:
        raise ValueError(f"unknown mineral '{mineral}'")
    if n <= 0:
        return []

    countries = ft.COUNTRIES_BY_MINERAL[mineral]
    chokepoints = ft.CHOKEPOINTS

    # Master RNG (Python's, for choices/randint) and a NumPy QMC engine
    # for the LHS knob sampling. Seeded so the run is reproducible.
    rng = random.Random(seed)
    qmc_engine = qmc.LatinHypercube(d=len(ft.CONFIG_KNOBS), seed=seed)
    knob_samples = qmc_engine.random(n=n)   # shape (n, n_knobs) in [0, 1)
    # Map each knob's [0, 1) to its [lo, hi] range.
    lo = np.array([k[1] for k in ft.CONFIG_KNOBS])
    hi = np.array([k[2] for k in ft.CONFIG_KNOBS])
    knob_samples = lo + knob_samples * (hi - lo)

    # Cap start_step to leave room for max-duration events to play out
    # within the simulation horizon (so we never sample an event whose
    # window is truncated by the run end).
    max_dur = ft.SUPPORTED_DURATION[1]
    start_lo = ft.SUPPORTED_START_STEP[0]
    start_hi = min(ft.SUPPORTED_START_STEP[1], n_steps - max_dur - 4)
    if start_hi < start_lo:
        start_hi = start_lo

    scenarios: list[dict] = []
    for i in range(n):
        n_emb, n_choke = _draw_shape(rng)

        embargoes = []
        for c in _draw_event_set(n_emb, countries, rng):
            embargoes.append({
                "country": c,
                "start_step": _uniform_int(start_lo, start_hi, rng),
                "duration": _log_uniform(*ft.SUPPORTED_DURATION, rng),
            })

        chokes = []
        for cp in _draw_event_set(n_choke, chokepoints, rng):
            chokes.append({
                "chokepoint": cp,
                "start_step": _uniform_int(start_lo, start_hi, rng),
                "duration": _log_uniform(*ft.SUPPORTED_DURATION, rng),
            })

        config_overrides = {
            knob[0]: float(knob_samples[i, j])
            for j, knob in enumerate(ft.CONFIG_KNOBS)
        }

        scenarios.append({
            "mineral": mineral,
            "n_steps": n_steps,
            "random_seed": rng.randint(0, 2**31 - 1),
            "embargoes": embargoes,
            "chokepoint_crises": chokes,
            "config_overrides": config_overrides,
        })

    return scenarios


def expand_with_seeds(scenarios: list[dict], n_seeds: int) -> list[dict]:
    """Replicate each scenario into ``n_seeds`` runs with different seeds.

    Used by the surrogate Phase-2 pipeline to train on per-scenario
    ensemble means + stds rather than single-seed point estimates.
    Run k of scenario i (0-indexed) lands at expanded index
    ``i * n_seeds + k``, so given a flat task index ``j`` the underlying
    scenario is ``j // n_seeds`` -- a property the dataset builder
    relies on to group seeds back together.

    Each replica gets a deterministic seed derived from the scenario's
    original ``random_seed`` plus a large prime times ``k``. Two
    independent calls with the same input produce identical output
    (no in-call RNG), making the expansion reproducible.
    """
    if n_seeds <= 1:
        return list(scenarios)
    if n_seeds <= 0:
        return []

    expanded: list[dict] = []
    for scen in scenarios:
        base_seed = int(scen.get("random_seed", 0))
        for k in range(n_seeds):
            replica = dict(scen)
            replica["random_seed"] = (base_seed + k * 314_159) % (2**31)
            expanded.append(replica)
    return expanded
