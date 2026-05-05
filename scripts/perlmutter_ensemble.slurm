#!/bin/bash
# pyExaMINE -- 20-seed ensemble regen on Perlmutter via Shifter.
#
# Submit with:
#   sbatch scripts/perlmutter_ensemble.slurm
#
# Before first use:
#   1. Replace <YOUR_REPO> below with your NERSC project repo (m1234, etc.).
#   2. (One time, then again after each `docker push`):
#        shifterimg pull docker:nugent68/pyexamine:latest
#
# Outputs land in $SCRATCH/pyexamine_run_$SLURM_JOB_ID/. The container
# writes to /data which is bind-mounted onto that host path, so the
# read-only Shifter image FS is never touched.
#
# A 128-core CPU node + 64 parallel uv workers regenerates the full
# 20-seed canonical sweep (5 Li embargoes + 6 chokepoints + 6 2050
# scenarios x 3 minerals) in roughly 3-5 minutes -- vs ~15 min on a
# 10-core laptop. Tune --n-workers to the node CPU budget you have.
#
#SBATCH --image=docker:nugent68/pyexamine:latest
#SBATCH --account=<YOUR_REPO>
#SBATCH --constraint=cpu
#SBATCH --qos=regular
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128
#SBATCH --time=00:30:00
#SBATCH --job-name=pyexamine_ensemble
#SBATCH --output=pyexamine_%j.log

set -euo pipefail

# Writable host path (Shifter image FS is read-only).
RUNDIR="$SCRATCH/pyexamine_run_$SLURM_JOB_ID"
mkdir -p "$RUNDIR"

echo "=== pyExaMINE Perlmutter ensemble run ==="
echo "Image:   docker:nugent68/pyexamine:latest"
echo "Job:     $SLURM_JOB_ID on $(hostname)"
echo "RUNDIR:  $RUNDIR"
echo "Started: $(date)"
echo

# Run the full 20-seed regen inside the container.
# --volume=$RUNDIR:/data overlays the writable host scratch path on
# the read-only image /data; the container's PYEXAMINE_OUTPUT_DIR=/data
# env var (set in the Dockerfile) means every script lands its output
# under /data without any extra --output-root flag.
#
# Note: Perlmutter Shifter has no ``--module=cpu``. On a CPU node the
# default module set is correct.
# Use /app/.venv/bin/python directly rather than ``uv run python``.
# ``uv run`` tries to acquire a lock in $HOME/.cache/uv, which on the
# GPFS-backed Perlmutter $HOME has flaky flock support. The image's
# venv is fully populated at build time, so uv has no work to do here.
srun --cpu-bind=cores \
    shifter --volume="$RUNDIR:/data" bash -c "
        cd /app
        /app/.venv/bin/python scripts/regenerate_outputs.py \
            --n-seeds 20 \
            --n-workers 64
    "

echo
echo "Finished: $(date)"
echo "Results:  $RUNDIR"
echo
echo "Top-level summary CSVs:"
find "$RUNDIR" -name 'ensemble_summary.csv' | sort
