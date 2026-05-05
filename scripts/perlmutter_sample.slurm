#!/bin/bash
# pyExaMINE -- generate surrogate-training data on Perlmutter.
#
# Runs N_PER_MINERAL scenarios per mineral, each replicated
# SEEDS_PER_SCENARIO times. With the defaults below (2000 scenarios x
# 20 seeds x 3 minerals = 120,000 runs at ~25 s each) the workload
# needs roughly 833 CPU-hours; we spread it across 4 CPU nodes (512
# physical cores) for a wall time of ~2 hours.
#
# Distribution strategy:
#   - One srun task per node (--ntasks=NODES, --ntasks-per-node=1).
#   - Each task runs GNU parallel locally with --jobs PARALLELISM
#     (default 128 = full node).
#   - Tasks coordinate by stride: node N processes flat indices
#     j where j % NODES == N. This is a fair, no-communication split.
#   - Each task uses --skip-existing on run_one_scenario.py so a
#     resubmit gap-fills only the missing CSVs.
#
# Outputs land at $CFS/amsc001/www/surrogate_data/runs/<mineral>/
# named NNNNNN.csv where NNNNNN is the flat index in the expanded
# scenarios JSON. Underlying scenario K occupies indices
# [K*SEEDS_PER_SCENARIO, K*SEEDS_PER_SCENARIO + SEEDS - 1].
#
# Submit with:
#   # 1. Generate scenarios on the login node first (cheap; <1 min):
#   shifter --image=docker:nugent68/pyexamine:latest \
#       --volume=$CFS/amsc001/www/surrogate_data:/data \
#       bash -c "cd /app && /app/.venv/bin/python scripts/sample_scenarios.py \
#           --n 2000 --seeds-per-scenario 20 --out /data/scenarios"
#
#   # 2. Submit the job (defaults to all 3 minerals, 40000 runs each):
#   sbatch scripts/perlmutter_sample.slurm
#
# Override defaults via --export, e.g.:
#   sbatch --export=MINERALS=lithium,N_TOTAL_PER_MINERAL=40000 ...
#
#SBATCH --image=docker:nugent68/pyexamine:latest
#SBATCH --account=amsc001
#SBATCH --constraint=cpu
#SBATCH --qos=regular
#SBATCH --nodes=4
#SBATCH --ntasks=4
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=128
#SBATCH --time=03:00:00
#SBATCH --job-name=pyexamine_sample
#SBATCH --output=logs/sample_%j.log

set -euo pipefail

# Knobs (override at submit time via sbatch --export=).
: "${MINERALS:=lithium nickel platinum}"
: "${N_TOTAL_PER_MINERAL:=40000}"     # 2000 scenarios * 20 seeds
: "${PARALLELISM:=128}"               # parallel slots per node

DATA_ROOT="${CFS}/amsc001/www/surrogate_data"
mkdir -p "${DATA_ROOT}/runs" logs

NODES=${SLURM_JOB_NUM_NODES:-1}

echo "=== pyExaMINE training-data generation (multi-node) ==="
echo "Job:          $SLURM_JOB_ID on $(hostname)"
echo "DATA_ROOT:    $DATA_ROOT"
echo "Minerals:     $MINERALS"
echo "Total/mineral: $N_TOTAL_PER_MINERAL"
echo "Nodes:        $NODES"
echo "Parallel/node:$PARALLELISM"
echo "Started:      $(date)"
echo

for MINERAL in $MINERALS; do
    SCEN="${DATA_ROOT}/scenarios/${MINERAL}.json"
    if [[ ! -f "$SCEN" ]]; then
        echo "[${MINERAL}] missing scenarios file ${SCEN}; skipping"
        continue
    fi
    mkdir -p "${DATA_ROOT}/runs/${MINERAL}"
    echo "[${MINERAL}] starting ${N_TOTAL_PER_MINERAL} runs at $(date)"

    # Launch one parallel pool per node. Each pool processes a stride
    # of size NODES through the flat task list, so the workload is
    # split fairly across nodes with no communication.
    #
    # Per-task wrapper (run_task) is exported here; srun + bash will
    # see it on each node. We keep the function small and self-
    # contained: it builds and dispatches a single shifter call.
    export DATA_ROOT MINERAL N_TOTAL_PER_MINERAL PARALLELISM NODES

    srun --ntasks="${NODES}" --ntasks-per-node=1 --cpu-bind=none bash <<'NODE_BASH'
        set -euo pipefail
        NODE_ID=${SLURM_PROCID}
        N_NODES=${SLURM_NTASKS}

        run_task() {
            local mineral=$1 idx=$2
            shifter --volume="${DATA_ROOT}:/data" \
                bash -c "cd /app && /app/.venv/bin/python scripts/run_one_scenario.py \
                    --scenarios /data/scenarios/${mineral}.json \
                    --index ${idx} \
                    --output-root /data/runs \
                    --skip-existing"
        }
        export -f run_task

        # Build this node's slice via stride NODE_ID, N_NODES.
        seq "${NODE_ID}" "${N_NODES}" $((N_TOTAL_PER_MINERAL - 1)) \
          | parallel \
                --jobs "${PARALLELISM}" \
                --joblog "logs/parallel_${SLURM_JOB_ID}_${MINERAL}_node${NODE_ID}.log" \
                --halt=soon,fail=20 \
                run_task "${MINERAL}" {} \
          || echo "[${MINERAL} node${NODE_ID}] some tasks failed"
NODE_BASH

    echo "[${MINERAL}] finished at $(date)"
done

echo
echo "Finished: $(date)"
echo "Outputs:  ${DATA_ROOT}/runs/"
echo "Web URL:  https://portal.nersc.gov/project/amsc001/surrogate_data/runs/"
