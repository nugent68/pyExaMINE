"""Scenario <-> fixed-dimension feature vector for the pyExaMINE surrogate.

This module is the single source of truth for the surrogate's input
schema. Anything that wants to predict from a scenario or train on
existing runs goes through ``encode``.

Key design choices:

* **Per-mineral models.** Each mineral's surrogate has its own feature
  vector with its own set of producing-country one-hots. This keeps
  the feature dimension small (~80-110) and avoids the surrogate
  having to learn a "this country only matters for nickel" rule.

* **Padded slots.** The scenario can carry up to ``K_MAX_EMBARGOES`` and
  ``K_MAX_CHOKEPOINTS`` simultaneous events. Empty slots are filled
  with a NONE sentinel one-hot + start=0 + duration=0. Real scenarios
  never need this many slots, so the surrogate is "happy padded".

* **Canonical slot ordering.** Events are sorted by (start_step, name)
  before encoding so the same scenario always produces the same feature
  vector regardless of input order. Trees and most NNs benefit from
  this canonical form.

* **Tree-friendly numerics.** Continuous knobs pass through raw (no
  z-scoring) since LightGBM splits on values, not magnitudes. start_step
  is normalized to [0, 1] by the simulation horizon. duration uses
  log1p so 1-week and 5-year embargoes get reasonable spread.

* **Support check.** ``support_check`` flags inputs that fall outside
  the training distribution we plan to sample. Used at inference time
  to warn users when they ask "extrapolate" questions.
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np

# ---------------------------------------------------------------------------
# Static metadata. These constants define the surrogate's input space.
# ---------------------------------------------------------------------------

#: Producing-country lists per mineral. Embargoing a non-producer has
#: no model effect, so we restrict the surrogate's input space to these.
COUNTRIES_BY_MINERAL: dict[str, list[str]] = {
    "lithium": [
        "Argentina", "Australia", "Brazil", "Canada", "Chile", "China",
        "Other countries", "Portugal", "USA", "Zimbabwe",
    ],
    "nickel": [
        "Australia", "Brazil", "Canada", "China", "Cuba", "Indonesia",
        "Madagascar", "New Caledonia", "Other countries", "Philippines",
        "Russia", "USA",
    ],
    "platinum": [
        "Canada", "Other countries", "Russia", "South Africa",
        "USA", "Zimbabwe",
    ],
}

#: Routing chokepoints recognised by the simulation.
CHOKEPOINTS: list[str] = [
    "Strait of Hormuz",
    "Suez Canal",
    "Malacca Strait",
    "Panama Canal",
    "Cape of Good Hope",
]

#: Maximum simultaneous events the surrogate represents. Scenarios
#: exceeding these counts are unsupported (will be silently truncated
#: with a support_check warning).
K_MAX_EMBARGOES: int = 5
K_MAX_CHOKEPOINTS: int = 3

#: Continuous config knobs the surrogate varies. Triples are
#: (key, min, max) for sampling. Order is stable and feeds directly
#: into the feature vector.
CONFIG_KNOBS: list[tuple[str, float, float]] = [
    ("geopolitical_event_probability",   0.0,   0.05),
    ("mine_disruption_probability",      0.0,   0.05),
    ("mine_capacity_growth_per_year",    0.0,   0.20),
    ("reserve_replacement_rate",         0.0,   1.0),
    ("substitution_rate",                0.0,   0.10),
    ("max_substitution",                 0.0,   0.50),
]

#: Reasonable defaults to fill in when a scenario doesn't override a knob.
#: Values match the per-mineral configs in src/config/.
DEFAULT_CONFIG_KNOBS: dict[str, float] = {
    "geopolitical_event_probability": 0.01,
    "mine_disruption_probability":    0.02,
    "mine_capacity_growth_per_year":  0.075,
    "reserve_replacement_rate":       0.70,
    "substitution_rate":              0.05,
    "max_substitution":               0.30,
}

#: Canonical simulation horizon used to normalize start_step. Surrogate
#: assumes runs are 1352 steps (1 step = 1 week, 2024 -> 2050 horizon).
#: Pass a different ``n_steps`` to ``encode`` if you train on shorter
#: runs, but be consistent across train + inference.
DEFAULT_N_STEPS: int = 1352

#: Sampling support range for embargo / chokepoint timing. Used by
#: support_check; sampling.py respects the same ranges.
SUPPORTED_START_STEP: tuple[int, int] = (104, 1248)   # year 2 .. year 24
SUPPORTED_DURATION: tuple[int, int] = (4, 260)        # 1 month .. 5 years


# ---------------------------------------------------------------------------
# Feature dimension / names
# ---------------------------------------------------------------------------

def _embargo_slot_dim(mineral: str) -> int:
    return (len(COUNTRIES_BY_MINERAL[mineral]) + 1) + 2   # +1 NONE sentinel

def _chokepoint_slot_dim() -> int:
    return (len(CHOKEPOINTS) + 1) + 2                     # +1 NONE sentinel


def feature_dim(mineral: str) -> int:
    """Number of columns in the feature vector for ``mineral``."""
    return (
        len(CONFIG_KNOBS)
        + K_MAX_EMBARGOES * _embargo_slot_dim(mineral)
        + K_MAX_CHOKEPOINTS * _chokepoint_slot_dim()
    )


def feature_names(mineral: str) -> list[str]:
    """Return the names of every feature in the order ``encode`` emits.

    Useful for GBT ``feature_importance`` analysis and for sanity-checking
    that the order of slots in the encoder matches what the trained
    model expects.
    """
    countries = COUNTRIES_BY_MINERAL[mineral]
    names: list[str] = [k for k, _, _ in CONFIG_KNOBS]
    for i in range(K_MAX_EMBARGOES):
        for c in countries:
            names.append(f"embargo{i}_country_{c}")
        names.append(f"embargo{i}_country_NONE")
        names.append(f"embargo{i}_start_step_norm")
        names.append(f"embargo{i}_log_duration")
    for i in range(K_MAX_CHOKEPOINTS):
        for cp in CHOKEPOINTS:
            names.append(f"chokepoint{i}_name_{cp}")
        names.append(f"chokepoint{i}_name_NONE")
        names.append(f"chokepoint{i}_start_step_norm")
        names.append(f"chokepoint{i}_log_duration")
    return names


# ---------------------------------------------------------------------------
# encode
# ---------------------------------------------------------------------------

def encode(scenario: dict, n_steps: int = DEFAULT_N_STEPS) -> np.ndarray:
    """Convert a scenario dict to a fixed-dim float32 feature vector.

    Scenario schema (matches what ``run_simulation.py`` accepts via CLI
    or config overrides):

        {
            "mineral":            "lithium",                 # required
            "embargoes":          [{"country": ..., "start_step": ..., "duration": ...}, ...],
            "chokepoint_crises":  [{"chokepoint": ..., "start_step": ..., "duration": ...}, ...],
            "config_overrides":   {"geopolitical_event_probability": 0.02, ...},
        }

    Slots beyond the scenario's actual event count get a NONE sentinel.
    Slots are sorted by (start_step, country/chokepoint) so order in the
    input list doesn't change the encoding.
    """
    mineral = scenario["mineral"]
    if mineral not in COUNTRIES_BY_MINERAL:
        raise ValueError(
            f"Unsupported mineral '{mineral}' (known: {list(COUNTRIES_BY_MINERAL)})"
        )
    countries = COUNTRIES_BY_MINERAL[mineral]
    n_countries = len(countries)

    config = scenario.get("config_overrides", {})
    embargoes = sorted(
        scenario.get("embargoes", []) or [],
        key=lambda e: (int(e["start_step"]), str(e["country"])),
    )
    chokes = sorted(
        scenario.get("chokepoint_crises", []) or [],
        key=lambda c: (int(c["start_step"]), str(c["chokepoint"])),
    )

    horizon = max(1, int(n_steps))
    parts: list[float] = []

    # Continuous knobs.
    for key, _, _ in CONFIG_KNOBS:
        parts.append(float(config.get(key, DEFAULT_CONFIG_KNOBS.get(key, 0.0))))

    # Embargo slots.
    for i in range(K_MAX_EMBARGOES):
        country_onehot = np.zeros(n_countries + 1, dtype=np.float32)
        if i < len(embargoes):
            e = embargoes[i]
            try:
                country_onehot[countries.index(e["country"])] = 1.0
            except ValueError:
                # Unknown country -> treat as NONE so encoding stays stable.
                country_onehot[-1] = 1.0
            start_norm = float(e["start_step"]) / horizon
            log_dur = math.log1p(max(0.0, float(e["duration"])))
        else:
            country_onehot[-1] = 1.0
            start_norm = 0.0
            log_dur = 0.0
        parts.extend(country_onehot.tolist())
        parts.append(start_norm)
        parts.append(log_dur)

    # Chokepoint slots.
    for i in range(K_MAX_CHOKEPOINTS):
        cp_onehot = np.zeros(len(CHOKEPOINTS) + 1, dtype=np.float32)
        if i < len(chokes):
            c = chokes[i]
            try:
                cp_onehot[CHOKEPOINTS.index(c["chokepoint"])] = 1.0
            except ValueError:
                cp_onehot[-1] = 1.0
            start_norm = float(c["start_step"]) / horizon
            log_dur = math.log1p(max(0.0, float(c["duration"])))
        else:
            cp_onehot[-1] = 1.0
            start_norm = 0.0
            log_dur = 0.0
        parts.extend(cp_onehot.tolist())
        parts.append(start_norm)
        parts.append(log_dur)

    return np.asarray(parts, dtype=np.float32)


# ---------------------------------------------------------------------------
# Support check
# ---------------------------------------------------------------------------

def support_check(scenario: dict) -> list[str]:
    """Return a list of human-readable warnings about out-of-distribution input.

    Empty list = scenario is fully inside the training support. Any
    non-empty return means the surrogate is being asked to extrapolate;
    callers should propagate the warning to users (per the project's
    "extrapolate with warning" policy).
    """
    warnings: list[str] = []
    mineral = scenario.get("mineral")
    if mineral not in COUNTRIES_BY_MINERAL:
        warnings.append(f"unsupported mineral '{mineral}'")
        return warnings

    countries = COUNTRIES_BY_MINERAL[mineral]

    embargoes = scenario.get("embargoes") or []
    if len(embargoes) > K_MAX_EMBARGOES:
        warnings.append(
            f"{len(embargoes)} embargoes exceeds the surrogate's slot count "
            f"({K_MAX_EMBARGOES}); extras will be dropped after sorting"
        )
    for e in embargoes:
        if e.get("country") not in countries:
            warnings.append(
                f"embargo on '{e.get('country')}' which is not a {mineral} producer; "
                f"the simulation will accept it but the surrogate has not been trained on this"
            )
        d = int(e.get("duration", 0))
        if d < SUPPORTED_DURATION[0] or d > SUPPORTED_DURATION[1]:
            warnings.append(
                f"embargo duration {d} weeks is outside the surrogate's "
                f"training range {SUPPORTED_DURATION}"
            )
        s = int(e.get("start_step", 0))
        if s < SUPPORTED_START_STEP[0] or s > SUPPORTED_START_STEP[1]:
            warnings.append(
                f"embargo start_step {s} is outside the surrogate's "
                f"training range {SUPPORTED_START_STEP}"
            )

    chokes = scenario.get("chokepoint_crises") or []
    if len(chokes) > K_MAX_CHOKEPOINTS:
        warnings.append(
            f"{len(chokes)} chokepoint crises exceeds the surrogate's slot count "
            f"({K_MAX_CHOKEPOINTS}); extras will be dropped after sorting"
        )
    for c in chokes:
        if c.get("chokepoint") not in CHOKEPOINTS:
            warnings.append(
                f"unknown chokepoint '{c.get('chokepoint')}' "
                f"(known: {CHOKEPOINTS})"
            )

    config = scenario.get("config_overrides", {}) or {}
    for key, lo, hi in CONFIG_KNOBS:
        if key in config:
            v = float(config[key])
            if v < lo or v > hi:
                warnings.append(
                    f"config knob {key}={v} outside training range [{lo}, {hi}]"
                )

    return warnings


def encode_batch(scenarios: Iterable[dict], n_steps: int = DEFAULT_N_STEPS) -> np.ndarray:
    """Stack ``encode(s)`` for each scenario into a (N, F) array."""
    rows = [encode(s, n_steps=n_steps) for s in scenarios]
    if not rows:
        return np.empty((0, 0), dtype=np.float32)
    return np.stack(rows, axis=0)


# ---------------------------------------------------------------------------
# v2 encoder -- adds richer encodings of the same scenario inputs.
#
# Why v2: the v1 encoder uses K_MAX padded "slots" so the model has to learn
# that "Chile embargo in slot 0" is equivalent to "Chile embargo in slot 2".
# At 10x data scale that consolidation was apparently learned for headline
# price targets (R^2 >= 0.95) but the trajectory model and the harder
# scalars (recovery_time R^2 ~ 0.57-0.73) plateaued.  v2 makes the
# consolidation explicit:
#
#   * per-country embargo summary (one column block per country)
#   * per-chokepoint crisis summary (one column block per chokepoint)
#   * event-interaction features (peak simultaneous coverage, total
#     embargo+chokepoint overlap days, etc)
#   * temporal-structure features (event-count, mean start, spread)
#
# v2 is strictly additive on top of v1 -- ``encode_v2`` returns
# ``[encode_v1(s); extras(s)]``.  This keeps existing bundles loadable
# (they ignore the new columns) and means we can A/B test v1 vs v2 by
# rebuilding the parquet without changing the simulator at all.
# ---------------------------------------------------------------------------

#: Number of summary features emitted per country (embargo block) /
#: per chokepoint (crisis block).
_PER_COUNTRY_DIM: int = 5
_PER_CHOKEPOINT_DIM: int = 5

#: Number of cross-event interaction scalars.
_INTERACTION_DIM: int = 6

#: Number of temporal-structure scalars.
_TEMPORAL_DIM: int = 4


def _per_country_block(
    embargoes: list[dict],
    countries: list[str],
    n_steps: int,
) -> list[float]:
    """5 features per country, totals across however many embargoes hit it."""
    horizon = max(1, int(n_steps))
    out: list[float] = []
    for c in countries:
        hits = [e for e in embargoes if e.get("country") == c]
        if hits:
            num = float(len(hits))
            total_days_norm = sum(float(e["duration"]) for e in hits) / horizon
            earliest_start_norm = (
                min(float(e["start_step"]) for e in hits) / horizon
            )
            max_log_duration = max(
                math.log1p(max(0.0, float(e["duration"]))) for e in hits
            )
            has = 1.0
        else:
            num = 0.0
            total_days_norm = 0.0
            earliest_start_norm = 0.0
            max_log_duration = 0.0
            has = 0.0
        out.extend([has, num, total_days_norm,
                    earliest_start_norm, max_log_duration])
    return out


def _per_chokepoint_block(
    chokes: list[dict],
    n_steps: int,
) -> list[float]:
    """5 features per chokepoint, totals across however many crises hit it."""
    horizon = max(1, int(n_steps))
    out: list[float] = []
    for cp in CHOKEPOINTS:
        hits = [c for c in chokes if c.get("chokepoint") == cp]
        if hits:
            num = float(len(hits))
            total_days_norm = sum(float(c["duration"]) for c in hits) / horizon
            earliest_start_norm = (
                min(float(c["start_step"]) for c in hits) / horizon
            )
            max_log_duration = max(
                math.log1p(max(0.0, float(c["duration"]))) for c in hits
            )
            has = 1.0
        else:
            has = 0.0
            num = 0.0
            total_days_norm = 0.0
            earliest_start_norm = 0.0
            max_log_duration = 0.0
        out.extend([has, num, total_days_norm,
                    earliest_start_norm, max_log_duration])
    return out


def _event_active_steps(events: list[dict]) -> np.ndarray:
    """0/1 indicator of how many events are active at each simulation step.

    Returns an ``(n_steps,)`` int array; sum across events of
    ``[start, start+duration)`` overlap.
    """
    n = DEFAULT_N_STEPS
    counts = np.zeros(n, dtype=np.int32)
    for e in events:
        s = int(e.get("start_step", 0))
        d = int(e.get("duration", 0))
        if d <= 0:
            continue
        a = max(0, s)
        b = min(n, s + d)
        if b > a:
            counts[a:b] += 1
    return counts


def _interaction_block(
    embargoes: list[dict],
    chokes: list[dict],
    n_steps: int,
) -> list[float]:
    """Cross-event statistics:

    * total embargo coverage / horizon  (sum of durations, normalized)
    * peak simultaneous embargoes
    * total chokepoint coverage / horizon
    * peak simultaneous chokepoints
    * days with BOTH an active embargo AND an active chokepoint, normalized
    * days with ANY event active, normalized
    """
    horizon = max(1, int(n_steps))
    emb_active = _event_active_steps(embargoes)
    cho_active = _event_active_steps(chokes)

    total_emb_cov = float(sum(int(e.get("duration", 0)) for e in embargoes)) / horizon
    total_cho_cov = float(sum(int(c.get("duration", 0)) for c in chokes)) / horizon
    peak_emb = float(emb_active.max()) if emb_active.size else 0.0
    peak_cho = float(cho_active.max()) if cho_active.size else 0.0
    overlap_days = float(int(((emb_active > 0) & (cho_active > 0)).sum())) / horizon
    any_active_days = float(int(((emb_active > 0) | (cho_active > 0)).sum())) / horizon

    return [total_emb_cov, peak_emb, total_cho_cov, peak_cho,
            overlap_days, any_active_days]


def _temporal_block(
    embargoes: list[dict],
    chokes: list[dict],
    n_steps: int,
) -> list[float]:
    """Temporal-structure scalars:

    * number of embargoes (raw count)
    * number of chokepoint crises (raw count)
    * mean embargo start_step (normalized) -- 0 if none
    * embargo start_step spread (max - min, normalized) -- 0 if 0/1 events
    """
    horizon = max(1, int(n_steps))
    n_emb = float(len(embargoes))
    n_cho = float(len(chokes))
    if embargoes:
        starts = [float(e["start_step"]) for e in embargoes]
        mean_start = sum(starts) / len(starts) / horizon
        spread = (max(starts) - min(starts)) / horizon if len(starts) > 1 else 0.0
    else:
        mean_start = 0.0
        spread = 0.0
    return [n_emb, n_cho, mean_start, spread]


def _v2_extras_dim(mineral: str) -> int:
    return (
        len(COUNTRIES_BY_MINERAL[mineral]) * _PER_COUNTRY_DIM
        + len(CHOKEPOINTS) * _PER_CHOKEPOINT_DIM
        + _INTERACTION_DIM
        + _TEMPORAL_DIM
    )


def feature_dim_v2(mineral: str) -> int:
    return feature_dim(mineral) + _v2_extras_dim(mineral)


def feature_names_v2(mineral: str) -> list[str]:
    countries = COUNTRIES_BY_MINERAL[mineral]
    names = list(feature_names(mineral))
    for c in countries:
        for tag in ("has_embargo", "n_embargoes", "total_days_norm",
                    "earliest_start_norm", "max_log_duration"):
            names.append(f"country_{c}__{tag}")
    for cp in CHOKEPOINTS:
        cp_slug = cp.replace(" ", "_")
        for tag in ("has_crisis", "n_crises", "total_days_norm",
                    "earliest_start_norm", "max_log_duration"):
            names.append(f"chokepoint_{cp_slug}__{tag}")
    for tag in ("total_emb_cov", "peak_emb", "total_cho_cov", "peak_cho",
                "emb_cho_overlap_days_norm", "any_active_days_norm"):
        names.append(f"interaction__{tag}")
    for tag in ("n_emb", "n_cho", "mean_start_norm", "start_spread_norm"):
        names.append(f"temporal__{tag}")
    return names


def encode_v2(scenario: dict, n_steps: int = DEFAULT_N_STEPS) -> np.ndarray:
    """v1 encoder output concatenated with v2 extras (per-country,
    per-chokepoint, interaction, temporal).  See module docstring.
    """
    base = encode(scenario, n_steps=n_steps)
    mineral = scenario["mineral"]
    countries = COUNTRIES_BY_MINERAL[mineral]

    embargoes = sorted(
        scenario.get("embargoes", []) or [],
        key=lambda e: (int(e["start_step"]), str(e["country"])),
    )
    chokes = sorted(
        scenario.get("chokepoint_crises", []) or [],
        key=lambda c: (int(c["start_step"]), str(c["chokepoint"])),
    )

    extras: list[float] = []
    extras.extend(_per_country_block(embargoes, countries, n_steps))
    extras.extend(_per_chokepoint_block(chokes, n_steps))
    extras.extend(_interaction_block(embargoes, chokes, n_steps))
    extras.extend(_temporal_block(embargoes, chokes, n_steps))

    return np.concatenate(
        [base, np.asarray(extras, dtype=np.float32)],
        axis=0,
    )


def encode_batch_v2(
    scenarios: Iterable[dict],
    n_steps: int = DEFAULT_N_STEPS,
) -> np.ndarray:
    rows = [encode_v2(s, n_steps=n_steps) for s in scenarios]
    if not rows:
        return np.empty((0, 0), dtype=np.float32)
    return np.stack(rows, axis=0)


# ---------------------------------------------------------------------------
# Version dispatch -- single entry point that selects v1 or v2 by name.
# ---------------------------------------------------------------------------

FEATURE_VERSIONS: tuple[str, ...] = ("v1", "v2")
DEFAULT_FEATURE_VERSION: str = "v1"


def encode_versioned(
    scenario: dict,
    n_steps: int = DEFAULT_N_STEPS,
    version: str = DEFAULT_FEATURE_VERSION,
) -> np.ndarray:
    if version == "v1":
        return encode(scenario, n_steps=n_steps)
    if version == "v2":
        return encode_v2(scenario, n_steps=n_steps)
    raise ValueError(f"unknown feature version '{version}' "
                     f"(known: {FEATURE_VERSIONS})")


def feature_dim_versioned(mineral: str, version: str = DEFAULT_FEATURE_VERSION) -> int:
    if version == "v1":
        return feature_dim(mineral)
    if version == "v2":
        return feature_dim_v2(mineral)
    raise ValueError(f"unknown feature version '{version}'")


def feature_names_versioned(
    mineral: str, version: str = DEFAULT_FEATURE_VERSION,
) -> list[str]:
    if version == "v1":
        return feature_names(mineral)
    if version == "v2":
        return feature_names_v2(mineral)
    raise ValueError(f"unknown feature version '{version}'")
