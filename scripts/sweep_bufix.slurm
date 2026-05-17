#!/bin/bash
# pyExaMINE -- focused substitution-knob sweep with the buffer-fix image.
#
# Compares against the previous Stage 2 v2 (sweep_stage2v2_53039588) to
# test whether decoupling manufacturer target_inventory from the
# post-substitution mineral_intensity flips the "substitute less = better"
# paradox.
#
# 1,300 scenarios: 13 (param, value) cells x 5 embargo durations x 20 seeds.
# 1352-step (2024-2050) runs.
#
# Image:  docker:nugent68/pyexamine:us-policy-bufix
#         (manufacturer_agent.target_inventory now uses
#          initial_mineral_intensity instead of the substituted value)
#
# Before first use:
#   shifterimg pull docker:nugent68/pyexamine:us-policy-bufix
#
#   shifter --image=docker:nugent68/pyexamine:us-policy-bufix \
#           --volume=/pscratch/sd/n/nugent/pyexamine:/work \
#     /app/.venv/bin/python /work/scripts/build_policy_scenarios.py \
#         --policies /work/policies/us_aggressive.json \
#         --param-grid /work/scenarios/sweep_bufix_param_grid.json \
#         --seeds 0 1 ... 19 --minerals lithium \
#         --n-steps 1352 --embargo-start 312 \
#         --embargo-durations 52 156 520 832 1040 \
#         --output /work/scenarios/sweep_bufix.json
#
#SBATCH --image=docker:nugent68/pyexamine:us-policy-bufix
#SBATCH --account=amsc001
#SBATCH --constraint=cpu
#SBATCH --qos=debug
#SBATCH --nodes=4
#SBATCH --ntasks=4
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=128
#SBATCH --time=00:15:00
#SBATCH --job-name=pyx_bufix
#SBATCH --output=/pscratch/sd/n/nugent/pyexamine/logs/pyx_bufix_%j.log

set -euo pipefail

REPO=/pscratch/sd/n/nugent/pyexamine
SCENARIOS_REL=scenarios/sweep_bufix.json
RUNDIR=$REPO/runs/sweep_bufix_$SLURM_JOB_ID

mkdir -p "$RUNDIR/scenario_runs/lithium" "$REPO/logs"

N_SCENARIOS=$(jq 'length' "$REPO/$SCENARIOS_REL")
N_NODES=$SLURM_JOB_NUM_NODES
N_WORKERS=${N_WORKERS:-128}

echo "=== pyExaMINE bufix substitution sweep ==="
echo "Image:     docker:nugent68/pyexamine:us-policy-bufix"
echo "Job:       $SLURM_JOB_ID on $(hostname)"
echo "Scenarios: $REPO/$SCENARIOS_REL  ($N_SCENARIOS)"
echo "Nodes:     $N_NODES   Workers/node: $N_WORKERS"
echo "Rundir:    $RUNDIR"
echo "Started:   $(date)"
echo

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
echo "=== Aggregating KPIs ==="
shifter --image=docker:nugent68/pyexamine:us-policy-bufix \
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
