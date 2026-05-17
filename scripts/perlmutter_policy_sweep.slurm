#!/bin/bash
# pyExaMINE -- US-policy sweep on Perlmutter via Shifter.
#
# Runs every (policy x seed x mineral) scenario built by
# scripts/build_policy_scenarios.py in parallel on one CPU node, then
# aggregates the per-scenario trajectories into a single summary CSV.
#
# Submit with:
#   sbatch scripts/perlmutter_policy_sweep.slurm
#
# Before first use:
#   1. Edit --account=<YOUR_REPO> below (your NERSC project repo).
#   2. (Once) shifterimg pull docker:nugent68/pyexamine:us-policy
#   3. Build the scenarios JSON on the login node:
#        uv run python scripts/build_policy_scenarios.py \
#            --policies policies/us_default.json \
#                       policies/us_strategic_reserve.json \
#                       policies/us_aggressive.json \
#            --seeds 0 1 2 3 4 5 6 7 8 9 \
#            --minerals lithium \
#            --output scenarios/policy_sweep.json
#      (30 scenarios in this example: 3 policies x 10 seeds x 1 mineral.)
#
# Outputs land in $SCRATCH/policy_sweep_$SLURM_JOB_ID/
#   ├── lithium/000000.h5 ... NNNN.h5     (per-scenario trajectories)
#   ├── summary.csv                       (one row per scenario, KPIs)
#   └── pyexamine_<jobid>.log
#
#SBATCH --image=docker:nugent68/pyexamine:us-policy
#SBATCH --account=<YOUR_REPO>
#SBATCH --constraint=cpu
#SBATCH --qos=regular
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128
#SBATCH --time=00:30:00
#SBATCH --job-name=pyexamine_policy_sweep
#SBATCH --output=pyexamine_%j.log

set -euo pipefail

# -- inputs ----------------------------------------------------------------
SCENARIOS="${SCENARIOS:-scenarios/policy_sweep.json}"
N_WORKERS="${N_WORKERS:-64}"      # Parallel scenario runners; leave half the
                                  # CPUs for numpy threading inside each run.
FORMAT="${FORMAT:-h5}"            # Per-scenario output format; h5 keeps a
                                  # large sweep manageable on Lustre.

# -- writable host path (Shifter image FS is read-only) --------------------
RUNDIR="$SCRATCH/policy_sweep_$SLURM_JOB_ID"
mkdir -p "$RUNDIR"

# Count scenarios so the worker loop knows the index range. Done on the
# host (the JSON sits on the writable filesystem; jq is in $PATH on
# Perlmutter login + compute nodes).
N_SCENARIOS=$(jq 'length' "$SCENARIOS")

echo "=== pyExaMINE policy sweep ==="
echo "Image:       docker:nugent68/pyexamine:us-policy"
echo "Job:         $SLURM_JOB_ID on $(hostname)"
echo "Scenarios:   $SCENARIOS  ($N_SCENARIOS scenarios)"
echo "Workers:     $N_WORKERS"
echo "Format:      $FORMAT"
echo "RUNDIR:      $RUNDIR"
echo "Started:     $(date)"
echo

# Pre-create the per-mineral output dirs so the per-scenario writes
# don't race on mkdir.
shifter --volume="$RUNDIR:/data" bash -c "
    mkdir -p /data/scenario_runs
    cd /app
    # Best-effort: pick out unique mineral names from the scenarios JSON
    # and create their subdirs.
    /app/.venv/bin/python -c '
import json, sys, pathlib
scenarios = json.load(open(\"$SCENARIOS\"))
out = pathlib.Path(\"/data/scenario_runs\")
for s in scenarios:
    (out / s[\"mineral\"]).mkdir(parents=True, exist_ok=True)
'
"

# Drive the sweep with xargs -P inside the container. Each worker
# pulls one scenario index out of the queue, runs it via
# run_one_scenario.py, and writes the trajectory to
# /data/scenario_runs. --skip-existing makes this resilient to
# restart after a partial job. xargs -P is in every base image
# (Debian / Perlmutter); no GNU parallel dependency.
srun --cpu-bind=cores \
    shifter --volume="$RUNDIR:/data" \
            --volume="$PWD/$SCENARIOS:/scenarios.json:ro" bash -c "
        cd /app
        seq 0 $((N_SCENARIOS - 1)) | xargs -n1 -P $N_WORKERS -I{} \
            /app/.venv/bin/python scripts/run_one_scenario.py \
                --scenarios /scenarios.json \
                --index {} \
                --output-root /data/scenario_runs \
                --format $FORMAT \
                --skip-existing
    "

echo
echo "Aggregating sweep results..."
srun --cpu-bind=cores \
    shifter --volume="$RUNDIR:/data" \
            --volume="$PWD/$SCENARIOS:/scenarios.json:ro" bash -c "
        cd /app
        /app/.venv/bin/python scripts/summarize_policy_sweep.py \
            --scenarios /scenarios.json \
            --runs-root /data/scenario_runs \
            --output /data/summary.csv
    "

echo
echo "Finished: $(date)"
echo "Results:  $RUNDIR"
echo "Summary:  $RUNDIR/summary.csv"
