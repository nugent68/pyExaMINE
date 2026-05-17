# Running US-policy sweeps on Perlmutter (NERSC)

End-to-end recipe for running the China-embargo / US-policy study on
Perlmutter via Shifter. Three changes vs. the base
[INSTALL.md#docker](INSTALL.md#docker) Perlmutter recipe:

1. **One self-contained image with the new code baked in** — instead of
   the 2-layer setup we used for the local Mac test, push a single
   `nugent68/pyexamine:us-policy` tag. Shifter pulls one image.
2. **Built for linux/amd64** — Perlmutter is x86_64; the image must
   carry an amd64 manifest. Apple Silicon hosts need buildx.
3. **A policy-aware Slurm-array workflow** —
   `build_policy_scenarios.py` → `run_one_scenario.py` (xargs‑P inside
   the container) → `summarize_policy_sweep.py`.

---

## 1. Build the image for linux/amd64 (one time)

From the project root **on your laptop / build host**:

```bash
# Build the amd64 image. ~150 s warm; ~25 s cached.
docker buildx build --platform linux/amd64 \
    -t nugent68/pyexamine:us-policy \
    --load .

# Push to Docker Hub so Perlmutter Shifter can pull it.
docker login                             # only the first time
docker push nugent68/pyexamine:us-policy
```

Notes:
- The standard `Dockerfile` already `COPY . .`s the entire worktree
  in, so the new `src/config/overrides.py`,
  `src/agents/strategic_reserve_agent.py`, the modified agents, and the
  `policies/` directory are baked in automatically — there's no
  separate `Dockerfile.us-policy` to maintain on Perlmutter.
- If you maintain your own registry, replace `nugent68/...` with your
  namespace.

## 2. Pull the image on Perlmutter (one time per push)

```bash
ssh perlmutter.nersc.gov 'shifterimg pull docker:nugent68/pyexamine:us-policy'
```

## 3. Generate the scenario file

On a Perlmutter login node (or any host with the repo checked out):

```bash
cd pyExaMINE
mkdir -p scenarios

uv run python scripts/build_policy_scenarios.py \
    --policies policies/us_default.json \
               policies/us_strategic_reserve.json \
               policies/us_aggressive.json \
    --seeds 0 1 2 3 4 5 6 7 8 9 \
    --minerals lithium \
    --n-steps 500 \
    --embargo-start 312 \
    --embargo-duration 156 \
    --output scenarios/policy_sweep.json
# -> Wrote 30 scenarios (3 policies x 10 seeds x 1 mineral)
```

Each scenario in the JSON carries `mineral`, `random_seed`, `embargoes`,
and a `us_policy` block — exactly what `run_one_scenario.py` expects.

## 4. Submit the sweep

Edit `scripts/perlmutter_policy_sweep.slurm` to set your NERSC project
repo:
```bash
#SBATCH --account=<YOUR_REPO>
```

Then:
```bash
sbatch scripts/perlmutter_policy_sweep.slurm
```

The script:
- Mounts `$SCRATCH/policy_sweep_$SLURM_JOB_ID` at `/data` inside the
  container (Shifter image FS is read-only).
- Mounts your `scenarios/policy_sweep.json` at `/scenarios.json` (read
  only).
- Uses `xargs -n1 -P 64` to fan the 30 scenarios out across half of the
  128-core node; the other half is available for numpy threading inside
  each run.
- `--skip-existing` makes the array resumable — re-submitting the same
  job after a timeout or failure picks up where it left off.
- Runs `summarize_policy_sweep.py` after the sweep to write `summary.csv`.

A 30-scenario sweep (3 × 10 seeds, lithium, 500 steps) finishes in
~3 min on one CPU node. Scaling the seeds count is linear; widening
to all three minerals roughly triples the runtime.

## 5. Inspect results

```bash
ls $SCRATCH/policy_sweep_<jobid>/
# scenario_runs/lithium/000000.h5 ... 000029.h5
# summary.csv

head -5 $SCRATCH/policy_sweep_<jobid>/summary.csv
# index,policy_name,random_seed,mineral,peak_price,mean_price,...
```

The summary table prints to the Slurm log too — grouped by
`(policy_name, mineral)` with the mean across seeds, so you see the
policy comparison at a glance.

## 6. Resubmitting after a code change

Whenever you edit `src/`, `policies/`, or any other file baked into the
image:

```bash
# On the build host:
docker buildx build --platform linux/amd64 \
    -t nugent68/pyexamine:us-policy --load .
docker push nugent68/pyexamine:us-policy

# On Perlmutter:
shifterimg pull docker:nugent68/pyexamine:us-policy

# Resubmit (re-uses scenarios JSON; per-scenario H5 outputs land in a
# fresh $SCRATCH/policy_sweep_$SLURM_JOB_ID).
sbatch scripts/perlmutter_policy_sweep.slurm
```

The `scenarios.json` you generated in step 3 is portable and doesn't
need to be regenerated unless you change policies, seeds, minerals, or
the embargo window.

## What lands in `summary.csv`

One row per (policy, seed, mineral) with these KPIs over the embargo
window:

| Column | Meaning |
|---|---|
| `peak_price` | max `$/t` during embargo |
| `mean_price` | mean `$/t` during embargo |
| `peak_unfulfilled` | max units of demand left unmet in any step |
| `mean_unfulfilled` | average per-step unmet demand |
| `cumulative_unfulfilled` | total unmet demand summed across the window |
| `reserve_released_total` | total mineral released from the strategic reserve |
| `reserve_min_stock` | minimum reserve stock during embargo (0 = fully drained) |
| `recovery_steps` | steps after embargo for price to return within 10% of pre-embargo level |

`cumulative_unfulfilled` is the policy-comparison metric to lead with —
it captures both the depth of shortage and how long it lasts, which is
what a policymaker actually cares about. `peak_*` columns are noisy at
small seed counts; run 20+ seeds before reading anything off the peaks.

## Tuning

- **More seeds / minerals**: edit the `--seeds` and `--minerals` flags
  in step 3. The sweep is embarrassingly parallel so wall-clock scales
  with `total_scenarios / N_WORKERS`.
- **Different scenarios**: change `--embargo-start` / `--embargo-duration`,
  or pass `--no-embargo` for a baseline arm with no shock.
- **New policies**: drop another JSON into `policies/` and add it to
  `--policies`. Look at `src/config/overrides.py:RECOGNISED_OVERRIDE_KEYS`
  for the full list of tunable knobs.
- **Worker count**: `N_WORKERS=128 sbatch scripts/perlmutter_policy_sweep.slurm`
  uses the whole node for scenario parallelism (no threading
  headroom). Best when each run is short-lived; for long
  (1352-step) runs leave 64 workers / 64 threads.

## Caveats

- Single-seed `peak_*` numbers are not a reliable policy ranking
  signal; the worst-week shortage depends sensitively on which RNG
  events line up with the embargo edge. Use the **mean over seeds**
  printed in the Slurm log and `summary.csv` for the actual
  comparison.
- The image's amd64 numpy uses different SIMD paths from arm64 macOS
  numpy. A run with seed 42 on Perlmutter is *not* bit-identical to
  the same seed on your Mac (though they're statistically equivalent).
- The strategic reserve runs between processors and manufacturers in
  the tier order. If you're comparing to a published baseline that
  predates the US-policy plumbing, the empty-policy `summary.csv`
  reproduces the original behaviour bit-identically (verified in
  Phase-1 regression).
