#!/bin/bash
# Re-run JUST the compact step for an existing sweep run dir whose
# inline compaction was killed by the slurm wall clock. Reads the
# 19,600 per-scenario H5 files in $RUNDIR/scenario_runs/lithium/ and
# writes a single (N, T) HDF5 to $RUNDIR/compacted/lithium.h5.
#
# Usage:
#   RUNDIR=/pscratch/.../runs/sweep_stage2_53038786 \
#     sbatch scripts/compact_only.slurm
#
#SBATCH --image=docker:nugent68/pyexamine:us-policy
#SBATCH --account=amsc001
#SBATCH --constraint=cpu
#SBATCH --qos=debug
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=128
#SBATCH --time=00:15:00
#SBATCH --job-name=pyx_compact
#SBATCH --output=/pscratch/sd/n/nugent/pyexamine/logs/pyx_compact_%j.log

set -euo pipefail

REPO=/pscratch/sd/n/nugent/pyexamine
: "${RUNDIR:?must export RUNDIR=/pscratch/.../runs/sweep_stage2_NNNN}"
SCENARIOS_REL=scenarios/sweep_stage2.json

echo "=== compact-only ==="
echo "Job:     $SLURM_JOB_ID on $(hostname)"
echo "Rundir:  $RUNDIR"
echo "Started: $(date)"

# Remove any partial / corrupt lithium.h5 from a previous timed-out run.
rm -f "$RUNDIR/compacted/lithium.h5"
mkdir -p "$RUNDIR/compacted"

srun --cpu-bind=cores \
    shifter \
        --volume="$RUNDIR:/data" \
        --volume="$REPO:/work:ro" \
    bash -c '
        mkdir -p /data/_scenarios_per_mineral
        cp /work/'"$SCENARIOS_REL"' /data/_scenarios_per_mineral/lithium.json
        /app/.venv/bin/python /work/scripts/compact_csvs_to_hdf5.py \
            --runs /data/scenario_runs \
            --scenarios /data/_scenarios_per_mineral \
            --out /data/compacted \
            --mineral lithium \
            --n-steps 1352
    '

echo "Finished: $(date)"
echo "Compact:  $RUNDIR/compacted/lithium.h5"
