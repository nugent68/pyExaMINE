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
  {'mean_price_in_window_mean': 14892.7,
   'mean_price_in_window_std':    725.1,
   ...
   '_warnings': []}

* **As a CLI.** ``scripts/predict_surrogate.py`` accepts a JSON file
  or inline JSON and prints the prediction dict.

The output schema depends on which bundle was loaded:

* **Phase-1 single-seed** (``MineralModelBundle.ensemble == False``):
  one float per target.
* **Phase-2 ensemble** (``MineralModelBundle.ensemble == True``):
  ``<target>_mean`` and ``<target>_std`` per target.
* **CQR quantile** (``MineralQuantileBundle``):
  ``<target>_lo``, ``<target>_med``, ``<target>_hi`` per target,
  conformal-corrected to ``1 - alpha`` marginal coverage.

The returned dict always includes a ``_warnings`` key with any
``support_check`` flags (e.g., out-of-distribution durations, unknown
countries). Per the project's "extrapolate with warning" policy, the
surrogate still returns a numeric prediction in those cases; the
warning string surfaces it so callers don't act blindly.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any, Union

from . import features as ft
from . import train_scalar as ts
from . import quantile as qt


#: Bundle types ``predict`` knows how to dispatch on.
Bundle = Union[ts.MineralModelBundle, qt.MineralQuantileBundle]


def load_models(
    models_dir: Path,
    kind: str = "auto",
) -> dict[str, Bundle]:
    """Load every trained bundle under a directory.

    Filename conventions:
      * ``<mineral>_scalar.pkl``    -- Phase-1 / Phase-2 mean+std bundle.
      * ``<mineral>_quantile.pkl``  -- Phase-2 quantile (CQR) bundle.

    Args:
        models_dir: directory holding the pickled bundles.
        kind: which bundle layout to prefer when both exist for the
            same mineral. One of ``"point"`` (load only ``*_scalar.pkl``),
            ``"quantile"`` (load only ``*_quantile.pkl``), or ``"auto"``
            (load both; quantile takes precedence per mineral).

    Returns:
        Dict keyed by mineral name. Missing minerals are silently
        omitted so the API works even with partial training.
    """
    if kind not in {"auto", "point", "quantile"}:
        raise ValueError(f"kind must be one of auto/point/quantile, got {kind}")
    out: dict[str, Bundle] = {}
    models_dir = Path(models_dir)
    if kind in {"auto", "point"}:
        for path in sorted(models_dir.glob("*_scalar.pkl")):
            with path.open("rb") as f:
                bundle = pickle.load(f)
            out[bundle.mineral] = bundle
    if kind in {"auto", "quantile"}:
        # Loaded second so quantile bundles override scalar ones under
        # ``kind="auto"``.
        for path in sorted(models_dir.glob("*_quantile.pkl")):
            with path.open("rb") as f:
                bundle = pickle.load(f)
            out[bundle.mineral] = bundle
    return out


def predict(
    scenario: dict,
    models: dict[str, Bundle],
    n_steps: int = ft.DEFAULT_N_STEPS,
) -> dict[str, Any]:
    """Predict targets for ``scenario`` using the appropriate bundle.

    Dispatch is by bundle type:

    * :class:`train_scalar.MineralModelBundle` with ``ensemble=False``
      -> ``<target>: float``  (Phase-1 layout).
    * :class:`train_scalar.MineralModelBundle` with ``ensemble=True``
      -> ``<target>_mean: float`` and ``<target>_std: float``.
    * :class:`quantile.MineralQuantileBundle`
      -> ``<target>_lo / _med / _hi: float`` (conformal-corrected).

    Raises ``KeyError`` if ``scenario['mineral']`` has no trained model
    in ``models``.
    """
    mineral = scenario.get("mineral")
    if mineral not in models:
        raise KeyError(
            f"No trained surrogate for mineral '{mineral}' "
            f"(available: {list(models)})"
        )
    bundle = models[mineral]

    if isinstance(bundle, qt.MineralQuantileBundle):
        return qt.predict_quantile(bundle, scenario, n_steps=n_steps)

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
    models: dict[str, Bundle],
    n_steps: int = ft.DEFAULT_N_STEPS,
) -> list[dict[str, Any]]:
    """Predict for a list of scenarios; per-element output mirrors ``predict``."""
    return [predict(s, models, n_steps=n_steps) for s in scenarios]
