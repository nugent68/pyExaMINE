#!/bin/bash
# pyExaMINE -- generate surrogate-training data on Perlmutter, scratch I/O.
#
# Variant of perlmutter_sample.slurm sized for 6 nodes / 2 hr / regular
# QoS, writing to /pscratch (Lustre, ~10x lower latency for the 120k
# small CSV writes than CFS GPFS). Same stride-distributed dispatch as
# perlmutter_sample.slurm; only the path and shape change.
#
# Why 6 nodes x 2 hr instead of 4 x 3 hr (the original):
#   - Same node-hours (12) at less wall-clock and finishes inside one
#     allocation. Avoids the platinum tail spilling past the cap.
#   - 6 nodes x 128 = 768 parallel slots; at ~70s/task on the slow
#     mineral that's 40000 / 768 * 70s ~= 1 h per mineral, 3 minerals
#     => ~3 h serial worst case. With Li/Ni faster than Pt, ~1 h 50 min
#     is the realistic landing zone -- comfortable inside 2 h.
#
# Pre-flight (once per dataset version, on a login node):
#   mkdir -p /pscratch/sd/n/nugent/pyExaMINE/surrogate_data
#   cp -r /global/cfs/cdirs/amsc001/www/surrogate_data/scenarios \
#         /pscratch/sd/n/nugent/pyExaMINE/surrogate_data/
#
# Submit:
#   sbatch scripts/perlmutter_sample_scratch.slurm
#
# After the job lands, parquets / models / scenarios stay tidy in the
# small CFS tree; the bulk per-run CSVs live on scratch and can be
# deleted after build_dataset.py distills them.
#
#SBATCH --image=docker:nugent68/pyexamine:2e5a768
#SBATCH --account=amsc001
#SBATCH --constraint=cpu
#SBATCH --qos=regular
#SBATCH --nodes=6
#SBATCH --ntasks=6
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=128
#SBATCH --time=02:00:00
#SBATCH --job-name=pyexamine_sample_scratch
#SBATCH --output=logs/sample_scratch_%j.log

set -euo pipefail

# Knobs (override at submit time via sbatch --export=).
: "${MINERALS:=lithium nickel platinum}"
: "${N_TOTAL_PER_MINERAL:=40000}"
: "${PARALLELISM:=128}"
: "${DATA_ROOT:=/pscratch/sd/n/nugent/pyExaMINE/surrogate_data}"

mkdir -p "${DATA_ROOT}/runs" logs

NODES=${SLURM_JOB_NUM_NODES:-1}

echo "=== pyExaMINE training-data generation (scratch / 6-node) ==="
echo "Job:           $SLURM_JOB_ID on $(hostname)"
echo "DATA_ROOT:     $DATA_ROOT"
echo "Minerals:      $MINERALS"
echo "Total/mineral: $N_TOTAL_PER_MINERAL"
echo "Nodes:         $NODES"
echo "Parallel/node: $PARALLELISM"
echo "Started:       $(date)"
echo

for MINERAL in $MINERALS; do
    SCEN="${DATA_ROOT}/scenarios/${MINERAL}.json"
    if [[ ! -f "$SCEN" ]]; then
        echo "[${MINERAL}] missing scenarios file ${SCEN}; skipping"
        continue
    fi
    mkdir -p "${DATA_ROOT}/runs/${MINERAL}"
    echo "[${MINERAL}] starting ${N_TOTAL_PER_MINERAL} runs at $(date)"

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
