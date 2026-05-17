#!/bin/bash
# pyExaMINE -- MPI/parallel-HDF5 sweep on Perlmutter.
#
# 1 rank per core (128 per node), block-partitioned scenario assignment,
# direct parallel writes to a single sweep.h5. No per-scenario files,
# no post-job compact step.
#
# Submit with:
#   SCENARIOS=scenarios/sweep_bufix.json sbatch scripts/sweep_mpi.slurm
#
# Before first use:
#   shifterimg pull docker:nugent68/pyexamine:us-policy-mpi
#
#SBATCH --image=docker:nugent68/pyexamine:us-policy-mpi
#SBATCH --account=amsc001
#SBATCH --constraint=cpu
#SBATCH --qos=debug
#SBATCH --nodes=8
#SBATCH --ntasks-per-node=128
#SBATCH --cpus-per-task=1
#SBATCH --time=00:20:00
#SBATCH --job-name=pyx_sweep_mpi
#SBATCH --output=/pscratch/sd/n/nugent/pyexamine/logs/pyx_sweep_mpi_%j.log

set -euo pipefail

REPO=/pscratch/sd/n/nugent/pyexamine
SCENARIOS_REL=${SCENARIOS:-scenarios/sweep_bufix.json}
RUNDIR=$REPO/runs/sweep_mpi_$SLURM_JOB_ID
SWEEP_H5=$RUNDIR/sweep.h5

mkdir -p "$RUNDIR" "$REPO/logs"

N_SCENARIOS=$(jq 'length' "$REPO/$SCENARIOS_REL")
N_RANKS=$(( SLURM_JOB_NUM_NODES * SLURM_NTASKS_PER_NODE ))

echo "=== pyExaMINE MPI sweep ==="
echo "Image:     docker:nugent68/pyexamine:us-policy-mpi"
echo "Job:       $SLURM_JOB_ID on $(hostname)"
echo "Scenarios: $REPO/$SCENARIOS_REL  ($N_SCENARIOS)"
echo "Nodes:     $SLURM_JOB_NUM_NODES  ranks/node: $SLURM_NTASKS_PER_NODE  total ranks: $N_RANKS"
echo "Rundir:    $RUNDIR"
echo "Output:    $SWEEP_H5"
echo "Started:   $(date)"
echo

# --mpi=pmi2 is the correct PMI bootstrap for in-container MPICH on
# Perlmutter Shifter. Verified via bootstrap test (job 53051038):
# cray_shasta and pmix both produce singleton COMM_WORLD (size=1 per
# rank) which silently masquerades as "successful" startup, then races
# on shared file creation downstream. pmi2 correctly gives all 1024
# srun tasks a single COMM_WORLD.
srun --cpu-bind=cores --mpi=pmi2 \
    shifter \
        --module=mpich \
        --volume="$RUNDIR:/data" \
        --volume="$REPO:/work:ro" \
    /app/.venv/bin/python /work/scripts/run_sweep_mpi.py \
        --scenarios /work/$SCENARIOS_REL \
        --output    /data/sweep.h5

echo
echo "=== Aggregating KPIs ==="
# One rank for the summarize step (reads /data/sweep.h5 instead of a
# per-scenario tree). --sweep-h5 alternative path added to the
# summarizer in this commit.
srun --nodes=1 --ntasks=1 --cpu-bind=cores \
    shifter \
        --volume="$RUNDIR:/data" \
        --volume="$REPO:/work:ro" \
    /app/.venv/bin/python /work/scripts/summarize_policy_sweep.py \
        --scenarios /work/$SCENARIOS_REL \
        --sweep-h5  /data/sweep.h5 \
        --output    /data/summary.csv

echo
echo "Finished: $(date)"
echo "Results:  $RUNDIR"
echo "Sweep:    $SWEEP_H5"
echo "Summary:  $RUNDIR/summary.csv"
