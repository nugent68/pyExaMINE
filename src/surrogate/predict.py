"""Inference API for the trained scalar surrogate.

Two ways to use:

* **As a Python API.** Load a bundle once, call ``predict`` on
  scenario dicts:

  >>> from src.surrogate.predict import load_models, predict
  >>> models = load_models("surrogate_models/")
  >>> predict({"mineral": "lithium",
  ...          "embargoes": [{"country": "Chile",
  ...                         "start_step": 676, "duration": 52}]},
  ...         models)
  {'mean_price_in_window': 18234.7,
   'delta_pct_vs_baseline': 12.4,
   ...
   '_warnings': []}

* **As a CLI.** ``scripts/predict_surrogate.py`` accepts a JSON file
  or inline JSON and prints the prediction dict.

The returned dict includes a ``_warnings`` key with any
``support_check`` flags (e.g., out-of-distribution durations, unknown
countries). Per the project's "extrapolate with warning" policy, the
surrogate still returns a numeric prediction in those cases; the
warning string surfaces it so callers don't act blindly.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

import numpy as np

from . import features as ft
from . import train_scalar as ts


def load_models(models_dir: Path) -> dict[str, ts.MineralModelBundle]:
    """Load every ``<mineral>_scalar.pkl`` under a directory.

    Returns a dict keyed by mineral name. Missing minerals are silently
    omitted so the API works even with partial training.
    """
    out: dict[str, ts.MineralModelBundle] = {}
    models_dir = Path(models_dir)
    for path in sorted(models_dir.glob("*_scalar.pkl")):
        with path.open("rb") as f:
            bundle = pickle.load(f)
        out[bundle.mineral] = bundle
    return out


def predict(
    scenario: dict,
    models: dict[str, ts.MineralModelBundle],
    n_steps: int = ft.DEFAULT_N_STEPS,
) -> dict[str, Any]:
    """Predict scalar targets for ``scenario`` using the loaded models.

    Args:
        scenario: scenario dict (same schema ``run_simulation.py`` accepts).
        models: dict from ``load_models``.
        n_steps: simulation horizon (must match training).

    Returns:
        For Phase-1 single-seed bundles, one float per target (the
        bundle's mean prediction) plus a ``_warnings`` list.

        For Phase-2 ensemble bundles, two floats per target -- the
        predicted ensemble mean and the predicted seed-to-seed std --
        suffixed ``"_mean"`` / ``"_std"``. Where the std-prediction
        booster wasn't trained (e.g. recovery_time_if_recovered when
        too few rows had a finite mean), the std field is NaN. The
        ``recovered_mean`` field is interpretable as the predicted
        probability that the price returns to within +/-5% of the
        pre-event baseline before the run ends.

    Raises ``KeyError`` if ``scenario['mineral']`` has no trained
    model in ``models``.
    """
    mineral = scenario.get("mineral")
    if mineral not in models:
        raise KeyError(
            f"No trained surrogate for mineral '{mineral}' "
            f"(available: {list(models)})"
        )
    bundle = models[mineral]

    expected_features = ft.feature_names(mineral)
    if expected_features != bundle.feature_names:
        raise ValueError(
            f"Feature schema mismatch for mineral={mineral}: "
            f"the loaded bundle was trained on a different version of "
            f"surrogate.features. Retrain or downgrade the bundle."
        )

    X = ft.encode(scenario, n_steps=n_steps).reshape(1, -1)
    out: dict[str, Any] = {}

    if getattr(bundle, "ensemble", False):
        # Phase-2 layout: bundle.boosters[target] = {'mean': B, 'std': B}
        for target, kind_to_booster in bundle.boosters.items():
            for kind in ("mean", "std"):
                booster = kind_to_booster.get(kind)
                key = f"{target}_{kind}"
                if booster is None:
                    out[key] = float("nan")
                else:
                    out[key] = float(
                        booster.predict(
                            X, num_iteration=booster.best_iteration
                        )[0]
                    )
    else:
        # Phase-1 layout: bundle.boosters[target] = Booster
        for target, booster in bundle.boosters.items():
            out[target] = float(
                booster.predict(X, num_iteration=booster.best_iteration)[0]
            )

    out["_warnings"] = ft.support_check(scenario)
    return out


def predict_batch(
    scenarios: list[dict],
    models: dict[str, ts.MineralModelBundle],
    n_steps: int = ft.DEFAULT_N_STEPS,
) -> list[dict[str, Any]]:
    """Predict for a list of scenarios; per-element output mirrors ``predict``."""
    return [predict(s, models, n_steps=n_steps) for s in scenarios]
