#!/bin/bash
# pyExaMINE -- MPI/parallel-HDF5 sweep on Perlmutter via podman-hpc.
#
# One MPI rank per core, block-partitioned scenario assignment, direct
# parallel writes to a single sweep.h5. No per-scenario files, no
# post-job compact step.
#
# Image: pyexamine:mpi-podman. Three equivalent ways to populate this
# tag in your local podman-hpc squash cache, in order of preference:
#
#   1. Pull from the NERSC project registry (recommended, fast):
#        podman-hpc pull registry.nersc.gov/library/amsc001/pyexamine:mpi-podman
#        podman-hpc tag  registry.nersc.gov/library/amsc001/pyexamine:mpi-podman \
#                        pyexamine:mpi-podman
#        podman-hpc migrate pyexamine:mpi-podman
#
#   2. Pull from Docker Hub (works off-NERSC):
#        podman-hpc pull docker.io/nugent68/pyexamine:mpi-podman
#        podman-hpc tag  docker.io/nugent68/pyexamine:mpi-podman \
#                        pyexamine:mpi-podman
#        podman-hpc migrate pyexamine:mpi-podman
#
#   3. Rebuild from source on a login node (~30 min):
#        cd /pscratch/sd/n/nugent/pyexamine
#        podman-hpc build -f Containerfile.podman-mpi -t pyexamine:mpi-podman .
#        podman-hpc migrate pyexamine:mpi-podman
#
# All three end up at the same local tag `pyexamine:mpi-podman` so the
# srun line below doesn't need editing per-distribution-path.
#
# Verified smoke (job 53073504): --user 0 is required so the
# in-container Cray-MPICH PMI/PALS shim can read the mode-600 root-owned
# /var/spool/slurmd/mpi_cray_shasta/<job>.<step>/apinfo file. Without
# --user 0 each rank fails with PMI_Init returned 1.
#
# Submit with:
#   SCENARIOS=scenarios/sweep_bufix.json sbatch scripts/sweep_mpi.slurm
#
#SBATCH --account=amsc001
#SBATCH --constraint=cpu
#SBATCH --qos=debug
#SBATCH --nodes=8
#SBATCH --ntasks-per-node=128
#SBATCH --cpus-per-task=1
#SBATCH --time=00:30:00
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

echo "=== pyExaMINE MPI sweep (podman-hpc) ==="
echo "Image:     localhost/pyexamine:mpi-podman"
echo "Job:       $SLURM_JOB_ID on $(hostname)"
echo "Scenarios: $REPO/$SCENARIOS_REL  ($N_SCENARIOS)"
echo "Nodes:     $SLURM_JOB_NUM_NODES  ranks/node: $SLURM_NTASKS_PER_NODE  total ranks: $N_RANKS"
echo "Rundir:    $RUNDIR"
echo "Output:    $SWEEP_H5"
echo "Started:   $(date)"
echo

# Bare srun -- no --mpi=pmi2, no --cpu-bind. Perlmutter's default MPI
# plugin (cray_shasta/PALS) is what podman-hpc --mpi expects. --user 0
# is required to read the host's slurmd apinfo file.
srun podman-hpc run --rm --mpi --entrypoint= --user 0 \
        --volume="$RUNDIR:/data" \
        --volume="$REPO:/work:ro" \
        pyexamine:mpi-podman \
    /app/.venv/bin/python /work/scripts/run_sweep_mpi.py \
        --scenarios   /work/$SCENARIOS_REL \
        --output      /data/sweep.h5 \
        --compression none

echo
echo "=== Aggregating KPIs ==="
# One rank reads the single sweep.h5 and produces summary.csv.
srun --nodes=1 --ntasks=1 podman-hpc run --rm --entrypoint= --user 0 \
        --volume="$RUNDIR:/data" \
        --volume="$REPO:/work:ro" \
        pyexamine:mpi-podman \
    /app/.venv/bin/python /work/scripts/summarize_policy_sweep.py \
        --scenarios /work/$SCENARIOS_REL \
        --sweep-h5  /data/sweep.h5 \
        --output    /data/summary.csv

echo
echo "Finished: $(date)"
echo "Results:  $RUNDIR"
echo "Sweep:    $SWEEP_H5"
echo "Summary:  $RUNDIR/summary.csv"
