#!/usr/bin/env python3
"""Generate scenario JSON for a US-policy sweep on Perlmutter.

Takes one or more US-policy JSON files (each a ``country_overrides["USA"]``
block) and emits a flat list of scenario dicts that
``scripts/run_one_scenario.py --scenarios <file> --index <i>`` can drive
directly. Axes that can be swept:

  - ``--policies``           (one or more policy-archetype files)
  - ``--seeds``              (random_seed values)
  - ``--minerals``           (lithium / nickel / platinum)
  - ``--embargo-durations``  (one or more embargo lengths, in steps)
  - ``--param-grid``         (JSON grid of US-policy knobs to OAT-scan
                              on top of the baseline policy)

Every emitted scenario is stamped with ``policy_name``, ``random_seed``,
``embargo_duration``, ``param_name`` and ``param_value`` so the
summarizer can group / pivot without re-deriving anything.

Stage 1 usage (archetype comparison, no param scan):
    python scripts/build_policy_scenarios.py \
        --policies policies/us_default.json \
                   policies/us_strategic_reserve.json \
                   policies/us_aggressive.json \
        --seeds 0 1 ... 19 \
        --minerals lithium \
        --n-steps 1352 \
        --embargo-start 312 \
        --embargo-durations 52 104 156 ... 1040 \
        --output scenarios/sweep_stage1.json

Stage 2 usage (one-at-a-time scan around a single baseline):
    python scripts/build_policy_scenarios.py \
        --policies policies/us_strategic_reserve.json \
        --param-grid scenarios/sweep_stage2_param_grid.json \
        --seeds 0 1 ... 19 \
        --minerals lithium \
        --n-steps 1352 \
        --embargo-start 312 \
        --embargo-durations 52 104 156 ... 1040 \
        --output scenarios/sweep_stage2.json

Stage 3 usage (full-factorial Cartesian-product cross of multiple knobs):
    python scripts/build_policy_scenarios.py \
        --policies policies/us_aggressive.json \
        --param-grid-factorial scenarios/sweep_stage3_param_grid.json \
        --seeds 0 1 ... 19 \
        --minerals lithium \
        --n-steps 1352 \
        --embargo-start 312 \
        --embargo-durations 52 104 156 ... 1040 \
        --output scenarios/sweep_stage3.json

Factorial vs OAT semantics. With ``--param-grid`` (OAT) every scenario
varies exactly one knob from the baseline; with
``--param-grid-factorial`` every scenario is one cell of the full
Cartesian product of all knob value lists. The JSON file format is
identical for both modes. Factorial scenarios are tagged
``param_name="factorial"``, ``param_value=<cell_id>`` (a short stable
string like ``"ms0.3_ps0.5_sts6"``) and additionally carry a ``cell``
dict ``{knob: value, ...}`` for direct filtering downstream.

Param-grid file format. A JSON object mapping dotted-key paths to grids
of values. A "plain list" form is shorthand for "5 values, baseline is
the middle one" (only allowed for odd-length lists). A dict form lets
you specify the baseline explicitly:

    {
      "strategic_reserve.release_rate_tons_per_step": [50,100,150,250,400],
      "substitution_trigger_steps": [4, 8, 10, 26, 52],
      "max_substitution":           {"values": [0.1, 0.2, 0.3, 0.4, 0.5],
                                     "baseline": 0.3}
    }

OAT dedup: a scenario whose only "change" is the baseline value of any
knob is the same simulation -- the builder emits exactly one baseline
scenario per (embargo, seed, mineral, policy), tagged
``param_name="baseline"``, ``param_value=null``.

Dotted keys: ``"strategic_reserve.release_rate_tons_per_step"`` writes
into ``policy["strategic_reserve"]["release_rate_tons_per_step"]`` and
creates the intermediate dict if missing (deep-merge, no clobber of
other keys at the same level).
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--policies", type=Path, nargs="+", required=True,
        help="One or more US policy JSON files. The filename stem "
             "(without .json) is used as policy_name on each "
             "generated scenario.",
    )
    p.add_argument(
        "--seeds", type=int, nargs="+", required=True,
        help="Random seeds (the Cartesian product with the other axes "
             "determines the total scenario count).",
    )
    p.add_argument(
        "--minerals", nargs="+",
        choices=["lithium", "nickel", "platinum", "cobalt", "manganese"],
        default=["lithium"],
        help="Minerals to include (default: lithium only).",
    )
    p.add_argument(
        "--n-steps", type=int, default=500,
        help="Steps per scenario (default 500; use 1352 for the "
             "2024->2050 horizon).",
    )
    p.add_argument(
        "--embargo-start", type=int, default=312,
        help="Step at which the China embargo begins (default 312, "
             "matching china_us_embargo_2030 ~ year 2030).",
    )
    dur = p.add_mutually_exclusive_group()
    dur.add_argument(
        "--embargo-duration", type=int, default=None,
        help="Single embargo duration in steps. Mutually exclusive with "
             "--embargo-durations. If neither is given, defaults to 156.",
    )
    dur.add_argument(
        "--embargo-durations", type=int, nargs="+", default=None,
        help="Multiple embargo durations -- one scenario will be emitted "
             "per duration value per (policy, seed, mineral, param) cell.",
    )
    p.add_argument(
        "--no-embargo", action="store_true",
        help="Drop the embargo event from every scenario (control arm).",
    )
    grid_mode = p.add_mutually_exclusive_group()
    grid_mode.add_argument(
        "--param-grid", type=Path, default=None,
        help="JSON grid file for an OAT scan of US-policy knobs on top "
             "of the baseline policy (--policies must be a single file "
             "when this is used). See module docstring for format.",
    )
    grid_mode.add_argument(
        "--param-grid-factorial", type=Path, default=None,
        help="JSON grid file (same format as --param-grid) interpreted "
             "as a full Cartesian-product cross of all listed knobs. "
             "Yields prod(len(values_i)) cells per (policy, seed, mineral, "
             "embargo-duration) tuple, instead of OAT's sum(len(values_i)-1)+1. "
             "Each emitted scenario is tagged param_name='factorial' with "
             "a short cell_id as param_value and a full 'cell' dict.",
    )
    p.add_argument(
        "--output", type=Path, required=True,
        help="Output JSON path; parent directory will be created.",
    )
    return p.parse_args()


def _normalise_grid_entry(key: str, entry):
    """Return ``(values_list, baseline_value)`` for one grid entry."""
    if isinstance(entry, list):
        if len(entry) % 2 == 0:
            raise SystemExit(
                f"Grid entry '{key}' is a list of even length "
                f"({len(entry)}); cannot infer a baseline value. Use the "
                f"explicit {{\"values\": [...], \"baseline\": X}} form."
            )
        return list(entry), entry[len(entry) // 2]
    if isinstance(entry, dict) and "values" in entry and "baseline" in entry:
        values = list(entry["values"])
        baseline = entry["baseline"]
        if baseline not in values:
            raise SystemExit(
                f"Grid entry '{key}': baseline {baseline!r} is not in "
                f"the values list {values}."
            )
        return values, baseline
    raise SystemExit(
        f"Grid entry '{key}' has unsupported shape {type(entry).__name__}. "
        f"Use a plain odd-length list or {{'values': [...], 'baseline': X}}."
    )


def _cell_id(cell: dict) -> str:
    """Short stable ID for a factorial cell.

    Encodes each (knob, value) as ``{abbrev}{value}`` where abbrev is
    the first letter of each underscore-segment of the knob (max 4
    chars). E.g. ``{"substitution_trigger_steps": 6, "max_substitution":
    0.3, "price_spread": 0.5}`` -> ``"ms0.3_ps0.5_sts6"``. Keys are
    sorted for determinism; floats use ``%g`` so trailing zeros are
    dropped. Dotted keys collapse their dots to underscores before
    abbreviation, so ``"strategic_reserve.capacity_tons"`` becomes
    ``"srct<value>"``.
    """
    parts = []
    for key in sorted(cell.keys()):
        normalised = key.replace(".", "_")
        abbrev = "".join(s[0] for s in normalised.split("_") if s)[:4]
        val = cell[key]
        if isinstance(val, float):
            sval = f"{val:g}"
        else:
            sval = str(val)
        parts.append(f"{abbrev}{sval}")
    return "_".join(parts)


def _factorial_variants(policy_spec, grid):
    """Yield ``(cell_id, cell_dict, merged_policy)`` for every Cartesian
    cell of the grid.

    ``grid`` is the normalised ``{key: (values, baseline)}`` map (same
    shape OAT uses). ``baseline`` is informational only here -- every
    cell is emitted (no dedup), so a 4x4x5 grid yields exactly 80
    variants regardless of which value happens to coincide with the
    baseline policy's default.
    """
    import itertools
    keys = sorted(grid.keys())
    value_lists = [grid[k][0] for k in keys]
    for combo in itertools.product(*value_lists):
        cell = dict(zip(keys, combo))
        merged = policy_spec
        for k, v in cell.items():
            merged = _deep_merge_dotted(merged, k, v)
        yield (_cell_id(cell), cell, merged)


def _deep_merge_dotted(base: dict, dotted_key: str, value):
    """Return a deep copy of ``base`` with ``dotted_key`` set to ``value``.

    ``"a.b.c"`` writes into ``base["a"]["b"]["c"]``, creating intermediate
    dicts but not clobbering existing siblings at any level. Mutates only
    the copy.
    """
    out = copy.deepcopy(base)
    cursor = out
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = value
    return out


def main() -> int:
    args = _parse_args()

    # Resolve embargo-duration axis.
    if args.embargo_durations is not None:
        durations = list(args.embargo_durations)
    elif args.embargo_duration is not None:
        durations = [int(args.embargo_duration)]
    else:
        durations = [156]

    # Load policies. When --param-grid is used we require a single
    # baseline policy file -- the OAT variants would otherwise be
    # ambiguous (variants of which baseline?). Underscore-prefixed
    # top-level keys (e.g. ``_comment``) are stripped here so the model's
    # validate_country_overrides doesn't emit a warning per scenario at
    # runtime.
    policies = []
    for pp in args.policies:
        with pp.open() as f:
            spec = json.load(f)
        if not isinstance(spec, dict):
            raise SystemExit(
                f"Policy file '{pp}' must contain a JSON object "
                f"(got {type(spec).__name__})."
            )
        spec = {k: v for k, v in spec.items() if not k.startswith("_")}
        policies.append((pp.stem, spec))

    grid = None
    factorial = args.param_grid_factorial is not None
    grid_path = args.param_grid_factorial if factorial else args.param_grid
    if grid_path is not None:
        if len(policies) != 1:
            mode = "--param-grid-factorial" if factorial else "--param-grid"
            raise SystemExit(
                f"{mode} requires exactly one --policies file (the "
                f"baseline to scan around); got {len(policies)} policies."
            )
        with grid_path.open() as f:
            raw_grid = json.load(f)
        if not isinstance(raw_grid, dict):
            raise SystemExit(
                f"Grid file '{grid_path}' must contain a JSON object "
                f"(got {type(raw_grid).__name__})."
            )
        # Skip underscore-prefixed keys (used for inline _comment fields)
        # so the grid file can self-document without breaking the loop.
        grid = {
            k: _normalise_grid_entry(k, v)
            for k, v in raw_grid.items()
            if not k.startswith("_")
        }

    # Build the list of (param_name, param_value, cell_dict, merged_policy)
    # variants we'll emit for each baseline policy.
    #
    # OAT mode: one baseline cell + one cell per off-baseline knob value.
    #   Tag: param_name = knob name, param_value = scalar.
    #   cell = None for OAT rows.
    # Factorial mode: full Cartesian product across all knobs.
    #   Tag: param_name = "factorial", param_value = short cell_id string.
    #   cell = dict of {knob: value} for direct downstream filtering.
    def _variants_for(policy_name, policy_spec):
        if grid is None:
            yield ("baseline", None, None, policy_spec)
            return
        if factorial:
            for cell_id, cell, merged in _factorial_variants(policy_spec, grid):
                yield ("factorial", cell_id, cell, merged)
            return
        # OAT mode (unchanged behaviour).
        yield ("baseline", None, None, policy_spec)
        for key, (values, baseline) in grid.items():
            for v in values:
                if v == baseline:
                    continue   # same as the baseline cell, dedup it
                merged = _deep_merge_dotted(policy_spec, key, v)
                yield (key, v, None, merged)

    scenarios = []
    for policy_name, policy_spec in policies:
        for param_name, param_value, cell, merged in _variants_for(policy_name, policy_spec):
            for seed in args.seeds:
                for mineral in args.minerals:
                    for duration in durations:
                        events = []
                        if not args.no_embargo:
                            events.append({
                                "country":    "China",
                                "start_step": int(args.embargo_start),
                                "duration":   int(duration),
                            })
                        sc = {
                            "mineral":           mineral,
                            "policy_name":       policy_name,
                            "param_name":        param_name,
                            "param_value":       param_value,
                            "random_seed":       int(seed),
                            "n_steps":           int(args.n_steps),
                            "embargo_duration":  int(duration),
                            "embargoes":         events,
                            "chokepoint_crises": [],
                            "us_policy":         merged,
                        }
                        if cell is not None:
                            sc["cell"] = cell
                        scenarios.append(sc)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as f:
        json.dump(scenarios, f, indent=2)

    n_pol = len(policies)
    n_seed = len(args.seeds)
    n_mineral = len(args.minerals)
    n_dur = len(durations)
    if grid is None:
        n_variants_per_policy = 1
        mode = "no-grid"
    elif factorial:
        n_variants_per_policy = 1
        for (vals, _baseline) in grid.values():
            n_variants_per_policy *= len(vals)
        mode = "factorial " + "x".join(str(len(v)) for v, _ in grid.values())
    else:
        n_variants_per_policy = 1 + sum(
            sum(1 for v in vals if v != baseline)
            for (vals, baseline) in grid.values()
        )
        mode = "OAT"
    print(
        f"Wrote {len(scenarios)} scenarios to {args.output}\n"
        f"  Mode: {mode}\n"
        f"  Axes: {n_pol} polic{'y' if n_pol==1 else 'ies'}"
        f" x {n_variants_per_policy} variant{'' if n_variants_per_policy==1 else 's'}/policy"
        f" x {n_seed} seed{'' if n_seed==1 else 's'}"
        f" x {n_mineral} mineral{'' if n_mineral==1 else 's'}"
        f" x {n_dur} embargo duration{'' if n_dur==1 else 's'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
