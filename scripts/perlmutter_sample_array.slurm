#!/bin/bash
# pyExaMINE -- generate surrogate-training data on Perlmutter.
#
# Submits a Slurm array where each task runs ONE scenario out of the
# per-mineral JSON files produced by sample_scenarios.py. Outputs land
# under $CFS/amsc001/www/surrogate_data/, which is web-accessible so the
# trained model can be reproduced by anyone with the public URL.
#
# Submit with:
#   # 1. Generate scenarios on the login node first (cheap; <1 min):
#   uv run python scripts/sample_scenarios.py --n 2000 \
#       --out $CFS/amsc001/www/surrogate_data/scenarios
#   # 2. Submit the array (one task per scenario per mineral):
#   sbatch --array=0-1999%256 \
#       --export=MINERAL=lithium,N=2000 \
#       scripts/perlmutter_sample_array.slurm
#   sbatch --array=0-1999%256 \
#       --export=MINERAL=nickel,N=2000 scripts/perlmutter_sample_array.slurm
#   sbatch --array=0-1999%256 \
#       --export=MINERAL=platinum,N=2000 scripts/perlmutter_sample_array.slurm
#
# Throughput: each task is one ~18-25 s run (1352 steps). With %256
# concurrency on a 6000-task pool, wall time ~= 6000/256 * 25 s ~= 10
# minutes per mineral.
#
# The --skip-existing flag on run_one_scenario means re-submitting a
# failed array won't re-run completed scenarios, just gap-fills.
#
#SBATCH --image=docker:nugent68/pyexamine:latest
#SBATCH --account=amsc001
#SBATCH --constraint=cpu
#SBATCH --qos=shared                # short tasks; "shared" lets us pack many
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --time=00:15:00
#SBATCH --job-name=pyexamine_sample
#SBATCH --output=logs/sample_%A_%a.log

set -euo pipefail

: "${MINERAL:?must export MINERAL=lithium|nickel|platinum}"
: "${SLURM_ARRAY_TASK_ID:?run me as a Slurm array}"

DATA_ROOT="${CFS}/amsc001/www/surrogate_data"
SCENARIOS="${DATA_ROOT}/scenarios/${MINERAL}.json"
OUTROOT="${DATA_ROOT}/runs"

mkdir -p "${OUTROOT}/${MINERAL}" logs

# Bind-mount: the scenarios + outputs both live on CFS, so the
# container sees them at /data/scenarios + /data/runs respectively.
srun shifter --module=cpu \
    --volume="${DATA_ROOT}:/data" \
    bash -c "
        cd /app
        uv run python scripts/run_one_scenario.py \
            --scenarios /data/scenarios/${MINERAL}.json \
            --index ${SLURM_ARRAY_TASK_ID} \
            --output-root /data/runs \
            --skip-existing
    "
