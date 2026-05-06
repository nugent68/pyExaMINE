# Trained surrogate model bundles

This directory holds the `*.metrics.json` side-cars for every trained
per-mineral LightGBM bundle. Two bundle layouts coexist:

* **Point bundles** &mdash; `<mineral>_scalar.pkl` &mdash; one mean
  booster + one std booster per scalar target. Returns
  `(predicted_mean, predicted_std)` per target.
* **CQR bundles** &mdash; `<mineral>_quantile.pkl` &mdash; three
  LightGBM quantile boosters (q&#x2080;.&#x2080;&#x2085;, q&#x2080;.&#x2085;,
  q&#x2080;.&#x2089;&#x2085;) plus a held-out conformal calibration
  offset. Returns `[lo, median, hi]` with a distribution-free 90%
  marginal-coverage guarantee.

The `.pkl` pickles are gitignored (10&ndash;25 MB each, regenerable
from the per-mineral parquets). Canonical copies live on NERSC CFS;
fetch them before calling `load_models`.

## Canonical location

```
/global/cfs/cdirs/amsc001/www/surrogate_data/surrogate_models/
```

Web-portal mirror:
<https://portal.nersc.gov/project/amsc001/surrogate_data/surrogate_models/>.

## Pulling locally

```bash
mkdir -p surrogate_models
scp 'perlmutter.nersc.gov:/global/cfs/cdirs/amsc001/www/surrogate_data/surrogate_models/*.pkl' \
    surrogate_models/
```

Or over HTTP:

```bash
BASE=https://portal.nersc.gov/project/amsc001/surrogate_data/surrogate_models
for m in lithium nickel platinum; do
  curl -O "$BASE/${m}_scalar.pkl"
  curl -O "$BASE/${m}_quantile.pkl"
done
```

## Loading

```python
from src.surrogate.predict import load_models, predict

# Point bundle: returns <target>_mean + <target>_std.
pt = load_models("surrogate_models/", kind="point")
predict({"mineral": "lithium",
         "embargoes": [{"country": "Chile",
                        "start_step": 676, "duration": 52}]}, pt)

# CQR bundle: returns <target>_lo + <target>_med + <target>_hi
# (90% interval, conformal-corrected).
qq = load_models("surrogate_models/", kind="quantile")
predict({...}, qq)

# Auto: load both; quantile takes precedence per mineral.
models = load_models("surrogate_models/", kind="auto")
```

## What's currently trained

Phase-2 ensemble training at `seeds_per_scenario = 20` over 2,000
unique scenarios per mineral.

### Point bundles &mdash; held-out R&sup2; on the test split

| Target | Li | Ni | Pt |
|---|---:|---:|---:|
| mean_price_in_window | 0.947 | 0.938 | 0.900 |
| peak_price | 0.960 | 0.952 | 0.906 |
| unfulfilled_fraction | 0.867 | 0.741 | 0.920 |
| recovered (probability) | 0.817 | 0.774 | 0.696 |
| recovery_time_if_recovered | 0.201 | 0.461 | 0.382 |

Mean MAPEs on the price targets sit at 2&ndash;6%.

### CQR bundles &mdash; conformal coverage at &alpha; = 0.10

Target = 90% interval coverage. Mean conformal coverage = **90.2%**;
mean width-to-y-range = **0.41**.

See the `*_quantile.metrics.json` side-cars for the full
target-by-target breakdown (raw vs conformal coverage, mean width,
median RMSE).

## Regenerating from scratch

If you have access to the raw runs on CFS:

```bash
# 1. Build per-mineral parquets from runs/ + scenarios/.
uv run python scripts/build_dataset.py \
    --runs $CFS/amsc001/www/surrogate_data/runs \
    --scenarios $CFS/amsc001/www/surrogate_data/scenarios \
    --out $CFS/amsc001/www/surrogate_data/datasets

# 2a. Train point ensembles.
uv run python scripts/train_surrogate.py \
    --datasets $CFS/amsc001/www/surrogate_data/datasets \
    --out surrogate_models/

# 2b. Train CQR ensembles (~1 min for all three minerals).
uv run python scripts/train_quantile.py \
    --datasets $CFS/amsc001/www/surrogate_data/datasets \
    --out surrogate_models/ \
    --alpha 0.10
```

`scripts/perlmutter_build_train.slurm` runs steps 1 and 2a inside
Shifter on Perlmutter; step 2b is fast enough to run on a login node.
