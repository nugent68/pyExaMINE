#!/bin/bash
# pyExaMINE -- Stage 1 (debug-QoS smoke + archetype scan) on Perlmutter.
#
# 1,200 scenarios: 3 archetypes x 20 embargo durations x 20 seeds x lithium.
# 1352-step (2024-2050) runs.
#
# Submit with:
#   sbatch scripts/sweep_stage1.slurm
#
# Before first use:
#   1. Ensure docker:nugent68/pyexamine:us-policy is in the Shifter cache.
#   2. Build scenarios JSON:
#        shifter --image=docker:nugent68/pyexamine:us-policy \
#                --volume=/pscratch/sd/n/nugent/pyexamine:/work \
#          /app/.venv/bin/python /work/scripts/build_policy_scenarios.py \
#              --policies /work/policies/us_default.json \
#                         /work/policies/us_strategic_reserve.json \
#                         /work/policies/us_aggressive.json \
#              --seeds $(seq 0 19) --minerals lithium \
#              --n-steps 1352 --embargo-start 312 \
#              --embargo-durations $(seq 52 52 1040) \
#              --output /work/scenarios/sweep_stage1.json
#
# Outputs:
#   $SCRATCH/pyexamine/sweep_stage1_$SLURM_JOB_ID/
#     scenario_runs/lithium/000000.h5 ... 001199.h5
#     summary.csv
#
#SBATCH --image=docker:nugent68/pyexamine:us-policy
#SBATCH --account=amsc001
#SBATCH --constraint=cpu
#SBATCH --qos=debug
#SBATCH --nodes=4
#SBATCH --ntasks=4
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=128
#SBATCH --time=00:25:00
#SBATCH --job-name=pyx_sweep_s1
#SBATCH --output=/pscratch/sd/n/nugent/pyexamine/logs/pyx_sweep_s1_%j.log

set -euo pipefail

REPO=/pscratch/sd/n/nugent/pyexamine
SCENARIOS_REL=scenarios/sweep_stage1.json
RUNDIR=$REPO/runs/sweep_stage1_$SLURM_JOB_ID

mkdir -p "$RUNDIR/scenario_runs/lithium" "$REPO/logs"

N_SCENARIOS=$(jq 'length' "$REPO/$SCENARIOS_REL")
N_NODES=$SLURM_JOB_NUM_NODES
N_WORKERS=${N_WORKERS:-128}

echo "=== pyExaMINE Stage 1 sweep (debug) ==="
echo "Image:     docker:nugent68/pyexamine:us-policy"
echo "Job:       $SLURM_JOB_ID on $(hostname)"
echo "Scenarios: $REPO/$SCENARIOS_REL  ($N_SCENARIOS)"
echo "Nodes:     $N_NODES   Workers/node: $N_WORKERS"
echo "Rundir:    $RUNDIR"
echo "Started:   $(date)"
echo

# One srun task per node. Each task pulls a contiguous slice of the
# scenario index range and fans out across its 128 cores via xargs -P.
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
echo "=== Aggregating ==="
shifter --image=docker:nugent68/pyexamine:us-policy \
        --volume="$RUNDIR:/data" \
        --volume="$REPO:/work:ro" \
    /app/.venv/bin/python /work/scripts/summarize_policy_sweep.py \
        --scenarios /work/$SCENARIOS_REL \
        --runs-root /data/scenario_runs \
        --output /data/summary.csv

echo
echo "Finished: $(date)"
echo "Results:  $RUNDIR"
echo "Summary:  $RUNDIR/summary.csv"
