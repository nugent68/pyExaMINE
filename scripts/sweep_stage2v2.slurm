#!/bin/bash
# pyExaMINE -- Stage 2 v2 (China-shuts-US-out, debug-QoS) on Perlmutter.
#
# 19,600 scenarios anchored on policies/us_aggressive.json:
#   - China embargo (existing: Chinese mines stockpile)
#   - US procurement-avoid China (us_aggressive: refuses Chinese suppliers
#     both during and after the embargo window) -- the model-level
#     approximation of "China refuses to export refined Li to the US"
#
# 49 (param, value) cells x 20 embargo durations (52..1040 wk) x 20 seeds.
# 1352-step (2024-2050) runs.
#
# Wall bumped to 30 min (max debug) so the inline compaction fits.
# Stage 2 v1 took 25:02 with the compact step still running when killed;
# the standalone compact follow-on took 4:38, so 30 min should fit
# everything with a few minutes of margin.
#
# Submit with:
#   sbatch scripts/sweep_stage2v2.slurm
#
# Before first use:
#   shifter --image=docker:nugent68/pyexamine:us-policy \
#           --volume=/pscratch/sd/n/nugent/pyexamine:/work \
#     /app/.venv/bin/python /work/scripts/build_policy_scenarios.py \
#         --policies /work/policies/us_aggressive.json \
#         --param-grid /work/scenarios/sweep_stage2v2_param_grid.json \
#         --seeds 0 1 ... 19 --minerals lithium \
#         --n-steps 1352 --embargo-start 312 \
#         --embargo-durations 52 104 ... 1040 \
#         --output /work/scenarios/sweep_stage2v2.json
#
#SBATCH --image=docker:nugent68/pyexamine:us-policy
#SBATCH --account=amsc001
#SBATCH --constraint=cpu
#SBATCH --qos=debug
#SBATCH --nodes=8
#SBATCH --ntasks=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=128
#SBATCH --time=00:30:00
#SBATCH --job-name=pyx_sweep_s2v2
#SBATCH --output=/pscratch/sd/n/nugent/pyexamine/logs/pyx_sweep_s2v2_%j.log

set -euo pipefail

REPO=/pscratch/sd/n/nugent/pyexamine
SCENARIOS_REL=scenarios/sweep_stage2v2.json
RUNDIR=$REPO/runs/sweep_stage2v2_$SLURM_JOB_ID

mkdir -p "$RUNDIR/scenario_runs/lithium" "$RUNDIR/compacted" "$REPO/logs"

N_SCENARIOS=$(jq 'length' "$REPO/$SCENARIOS_REL")
N_NODES=$SLURM_JOB_NUM_NODES
N_WORKERS=${N_WORKERS:-128}

echo "=== pyExaMINE Stage 2 v2 (China-shuts-US-out, debug) ==="
echo "Image:     docker:nugent68/pyexamine:us-policy"
echo "Job:       $SLURM_JOB_ID on $(hostname)"
echo "Scenarios: $REPO/$SCENARIOS_REL  ($N_SCENARIOS)"
echo "Baseline:  policies/us_aggressive.json (procurement_avoid_countries=['China'])"
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
