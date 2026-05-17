#!/bin/bash
# pyExaMINE -- Stage 2 (regular-QoS production OAT sensitivity scan) on Perlmutter.
#
# 19,600 scenarios: 49 (param, value) cells x 20 embargo durations x 20 seeds x lithium.
# Anchored on policies/us_strategic_reserve.json as the baseline.
# 1352-step (2024-2050) runs.
#
# Submit with:
#   sbatch scripts/sweep_stage2.slurm
#
# Before first use:
#   1. Build scenarios JSON (inside the container):
#        shifter --image=docker:nugent68/pyexamine:us-policy \
#                --volume=/pscratch/sd/n/nugent/pyexamine:/work \
#          /app/.venv/bin/python /work/scripts/build_policy_scenarios.py \
#              --policies /work/policies/us_strategic_reserve.json \
#              --param-grid /work/scenarios/sweep_stage2_param_grid.json \
#              --seeds $(seq 0 19) --minerals lithium \
#              --n-steps 1352 --embargo-start 312 \
#              --embargo-durations $(seq 52 52 1040) \
#              --output /work/scenarios/sweep_stage2.json
#
# Outputs:
#   $SCRATCH/pyexamine/sweep_stage2_$SLURM_JOB_ID/
#     scenario_runs/lithium/000000.h5 ... 019599.h5
#     summary.csv
#     compacted/lithium.h5    (single (N, T) HDF5 from compact step)
#
#SBATCH --image=docker:nugent68/pyexamine:us-policy
#SBATCH --account=amsc001
#SBATCH --constraint=cpu
#SBATCH --qos=regular
#SBATCH --nodes=16
#SBATCH --ntasks=16
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=128
#SBATCH --time=01:00:00
#SBATCH --job-name=pyx_sweep_s2
#SBATCH --output=/pscratch/sd/n/nugent/pyexamine/logs/pyx_sweep_s2_%j.log

set -euo pipefail

REPO=/pscratch/sd/n/nugent/pyexamine
SCENARIOS_REL=scenarios/sweep_stage2.json
RUNDIR=$REPO/runs/sweep_stage2_$SLURM_JOB_ID

mkdir -p "$RUNDIR/scenario_runs/lithium" "$RUNDIR/compacted" "$REPO/logs"

N_SCENARIOS=$(jq 'length' "$REPO/$SCENARIOS_REL")
N_NODES=$SLURM_JOB_NUM_NODES
N_WORKERS=${N_WORKERS:-128}

echo "=== pyExaMINE Stage 2 sweep (regular) ==="
echo "Image:     docker:nugent68/pyexamine:us-policy"
echo "Job:       $SLURM_JOB_ID on $(hostname)"
echo "Scenarios: $REPO/$SCENARIOS_REL  ($N_SCENARIOS)"
echo "Nodes:     $N_NODES   Workers/node: $N_WORKERS"
echo "Rundir:    $RUNDIR"
echo "Started:   $(date)"
echo

# One srun task per node. xargs -P fans out scenarios across cores.
srun --cpu-bind=cores \
    shifter \
        --volume="$RUNDIR:/data" \
        --volume="$REPO:/work:ro" \
    bash -c '
        N_SCENARIOS='"$N_SCENARIOS"'
        N_NODES='"$N_NODES"'
        N_WORKERS='"$N_WORKERS"'
        START=$(( SLURM_PROCID       * N_SCENARIOS / N_NODES ))
        END=$((  (SLURM_PROCID + 1) * N_SCENARIOS / N_NODES - 1 ))
        echo "[task $SLURM_PROCID on $(hostname)] indices $START..$END"
        cd /work
        seq $START $END | xargs -n1 -P $N_WORKERS -I{} \
            /app/.venv/bin/python scripts/run_one_scenario.py \
                --scenarios /work/'"$SCENARIOS_REL"' \
                --index {} \
                --output-root /data/scenario_runs \
                --format h5 \
                --skip-existing
        echo "[task $SLURM_PROCID on $(hostname)] done"
    '

echo
echo "=== Aggregating per-scenario KPIs ==="
shifter --image=docker:nugent68/pyexamine:us-policy \
        --volume="$RUNDIR:/data" \
        --volume="$REPO:/work:ro" \
    /app/.venv/bin/python /work/scripts/summarize_policy_sweep.py \
        --scenarios /work/$SCENARIOS_REL \
        --runs-root /data/scenario_runs \
        --output /data/summary.csv

echo
echo "=== Compacting per-scenario H5 into per-mineral (N, T) HDF5 ==="
# compact_csvs_to_hdf5.py wants the scenarios JSON at <scenarios>/<mineral>.json
# (per-mineral scenario files), but we have a single combined JSON. Pass the
# combined file as scenarios/lithium.json via a temporary symlink dir so
# the script's per-mineral lookup hits it.
shifter --image=docker:nugent68/pyexamine:us-policy \
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

echo
echo "Finished: $(date)"
echo "Results:  $RUNDIR"
echo "Summary:  $RUNDIR/summary.csv"
echo "Compact:  $RUNDIR/compacted/lithium.h5"
echo
echo "If the compact step succeeded and you don't need the per-scenario tree,"
echo "you can clean it up to reclaim the inodes:"
echo "  rm -rf $RUNDIR/scenario_runs/lithium"
