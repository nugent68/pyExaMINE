# Trained surrogate model bundles

This directory holds the `*_scalar.metrics.json` side-cars for every
trained per-mineral LightGBM bundle. The actual `*_scalar.pkl`
pickles are **gitignored** (10+ MB each, regenerable from the
per-mineral parquets). To use the surrogate, fetch them from NERSC
first.

## Canonical location

The trained bundles live on NERSC CFS at:

```
/global/cfs/cdirs/amsc001/www/surrogate_data/surrogate_models/
```

which is web-portal visible at
<https://portal.nersc.gov/project/amsc001/surrogate_data/surrogate_models/>.

## Pulling locally

```bash
# Adjust to your scp profile / NERSC user as needed.
scp -r perlmutter.nersc.gov:/global/cfs/cdirs/amsc001/www/surrogate_data/surrogate_models/*.pkl \
    surrogate_models/
```

After that, `from src.surrogate.predict import load_models, predict`
discovers the bundles by glob:

```python
from src.surrogate.predict import load_models, predict
models = load_models("surrogate_models/")
out = predict({"mineral": "lithium",
               "embargoes": [{"country": "Chile",
                              "start_step": 676, "duration": 52}]},
              models)
```

## What's currently trained

The committed `*.metrics.json` side-cars summarise the held-out
test-set performance per (target, kind) for the latest bundle. As of
the Phase-2 ensemble training (commit will follow this README):

* All three minerals trained at `seeds_per_scenario = 20` over
  ~2,000 unique scenarios per mineral.
* Headline mean targets (`mean_price_in_window_mean`,
  `peak_price_mean`, `unfulfilled_fraction_in_window_mean`) hit
  R² 0.87-0.96 on held-out test rows.
* `recovered_mean` (recovery probability) hits R² 0.64-0.82.
* The `*_std` side of each booster is intrinsically noisier
  (R² 0.45-0.78) but lets `predict()` return a real
  ``(predicted_mean, predicted_std)`` tuple per target on a brand-new
  scenario.
* `recovery_time_if_recovered` is the weakest target (R² 0.20-0.46);
  treat it as advisory.

See the metrics JSONs themselves for the full per-target /
per-`kind` breakdown.

## Regenerating from scratch

If you have access to the raw runs on CFS:

```bash
# 1. Build per-mineral parquets from runs/ + scenarios/.
uv run python scripts/build_dataset.py \
    --runs $CFS/amsc001/www/surrogate_data/runs \
    --scenarios $CFS/amsc001/www/surrogate_data/scenarios \
    --out $CFS/amsc001/www/surrogate_data/datasets

# 2. Train the per-mineral ensembles.
uv run python scripts/train_surrogate.py \
    --datasets $CFS/amsc001/www/surrogate_data/datasets \
    --out surrogate_models/
```

Or submit `scripts/perlmutter_build_train.slurm` to do both inside
Shifter on Perlmutter.
