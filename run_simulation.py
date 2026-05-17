#!/usr/bin/env python3
"""
Main script to run critical minerals supply chain simulations.
Usage: python run_simulation.py --mineral lithium --steps 200
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.model.supply_chain_model import MineralSupplyChainModel
from src.visualization.visualizer import (
    plot_supply_chain_analysis,
    create_summary_statistics,
    save_summary_statistics
)
from src.config.lithium_config import LITHIUM_CONFIG
from src.config.nickel_config import NICKEL_CONFIG
from src.config.platinum_config import PLATINUM_CONFIG


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Run critical minerals supply chain ABM simulation'
    )
    
    parser.add_argument(
        '--mineral',
        type=str,
        choices=['lithium', 'nickel', 'platinum'],
        help='Mineral to simulate'
    )
    
    parser.add_argument(
        '--all',
        action='store_true',
        help='Run all three minerals'
    )
    
    parser.add_argument(
        '--steps',
        type=int,
        default=None,
        help='Number of simulation steps (overrides config)'
    )
    
    parser.add_argument(
        '--geo-prob',
        type=float,
        default=None,
        help='Geopolitical event probability (overrides config)'
    )
    
    parser.add_argument(
        '--seed',
        type=int,
        default=None,
        help='Random seed for reproducibility'
    )
    
    parser.add_argument(
        '--output-dir',
        type=str,
        default=os.environ.get('PYEXAMINE_OUTPUT_DIR', 'outputs'),
        help=(
            'Output directory for results. Defaults to '
            '$PYEXAMINE_OUTPUT_DIR if set (the Docker / Shifter image '
            'sets this to /data so users can bind-mount a writable host '
            'directory there), otherwise the local "outputs/" folder.'
        ),
    )
    
    parser.add_argument(
        '--no-viz',
        action='store_true',
        help='Skip visualization generation'
    )

    parser.add_argument(
        '--embargo',
        action='append',
        default=None,
        metavar='COUNTRY:START_STEP:DURATION',
        help=(
            'Schedule a political embargo. Format COUNTRY:START_STEP:DURATION '
            '(e.g., "Chile:624:52" -> Chile withholds exports starting step '
            '624 for 52 steps). May be repeated for multiple embargoes. '
            'Country must match the country column in data/{mineral}_mines.csv.'
        ),
    )

    parser.add_argument(
        '--us-policy',
        type=str,
        default=None,
        metavar='PATH',
        help=(
            'Path to a JSON file of US policy parameters. Loaded into '
            'config["country_overrides"]["USA"] and consulted at agent '
            'init for any key in src/config/overrides.py. Supports '
            'scalar agent knobs (e.g. retailer_reorder_point_multiplier), '
            'a "strategic_reserve" block to instantiate a US '
            'StrategicReserveAgent, and "procurement_avoid_countries" / '
            '"procurement_avoid_embargoed" to gate US buyers. Example '
            'policy files live in policies/*.json.'
        ),
    )

    parser.add_argument(
        '--chokepoint-crisis',
        action='append',
        default=None,
        metavar='NAME:START_STEP:DURATION',
        help=(
            'Schedule a chokepoint closure. Format NAME:START_STEP:DURATION '
            '(e.g., "Suez Canal:624:8" -> Suez Canal closed for 8 weeks '
            'starting step 624). Any in-transit shipment whose route uses '
            'the named chokepoint is delayed until it reopens, and new '
            'shipments are dispatched onto an alternate route if one '
            'exists. Known chokepoints: Strait of Hormuz, Suez Canal, '
            'Malacca Strait, Panama Canal, Cape of Good Hope.'
        ),
    )

    args = parser.parse_args()
    
    # Validate arguments
    if not args.all and not args.mineral:
        parser.error('Must specify either --mineral or --all')
    
    return args


def get_config(mineral_name):
    """Get configuration for a mineral.
    
    Args:
        mineral_name: Name of mineral (lithium, nickel, platinum)
    
    Returns:
        Configuration dictionary
    """
    configs = {
        'lithium': LITHIUM_CONFIG,
        'nickel': NICKEL_CONFIG,
        'platinum': PLATINUM_CONFIG
    }
    
    return configs.get(mineral_name.lower())


def _parse_embargo_specs(specs):
    """Parse a list of "COUNTRY:START_STEP:DURATION" strings.

    Returns a list of dicts in the schema expected by
    MineralSupplyChainModel (config['political_embargoes']).
    """
    if not specs:
        return []
    parsed = []
    for spec in specs:
        parts = spec.rsplit(':', 2)
        if len(parts) != 3:
            raise ValueError(
                f"Invalid --embargo value '{spec}'. "
                f"Expected COUNTRY:START_STEP:DURATION (e.g., Chile:624:52)."
            )
        country, start_step, duration = parts
        try:
            start_step = int(start_step)
            duration = int(duration)
        except ValueError as e:
            raise ValueError(f"Invalid --embargo value '{spec}': {e}")
        if duration <= 0 or start_step < 0:
            raise ValueError(
                f"Invalid --embargo value '{spec}': start_step must be >=0, "
                f"duration must be >0."
            )
        parsed.append({
            'country': country.strip(),
            'start_step': start_step,
            'duration': duration,
        })
    return parsed


def _parse_chokepoint_specs(specs):
    """Parse a list of "NAME:START_STEP:DURATION" strings.

    Returns dicts in the schema expected by MineralSupplyChainModel
    (config['chokepoint_crises']).
    """
    if not specs:
        return []
    parsed = []
    for spec in specs:
        parts = spec.rsplit(':', 2)
        if len(parts) != 3:
            raise ValueError(
                f"Invalid --chokepoint-crisis value '{spec}'. "
                f"Expected NAME:START_STEP:DURATION (e.g., 'Suez Canal:624:8')."
            )
        name, start_step, duration = parts
        try:
            start_step = int(start_step)
            duration = int(duration)
        except ValueError as e:
            raise ValueError(f"Invalid --chokepoint-crisis value '{spec}': {e}")
        if duration <= 0 or start_step < 0:
            raise ValueError(
                f"Invalid --chokepoint-crisis value '{spec}': start_step >=0, duration >0."
            )
        parsed.append({
            'chokepoint': name.strip(),
            'start_step': start_step,
            'duration': duration,
        })
    return parsed


def _load_us_policy(path):
    """Read a US policy JSON file (or return None if path is empty)."""
    if not path:
        return None
    with open(path, 'r') as f:
        policy = json.load(f)
    if not isinstance(policy, dict):
        raise ValueError(
            f"US policy file '{path}' must contain a JSON object at the "
            f"top level (got {type(policy).__name__})."
        )
    return policy


def run_single_mineral(mineral_name, n_steps=None, geo_prob=None, seed=None,
                       output_dir='outputs', generate_viz=True,
                       embargoes=None, chokepoint_crises=None,
                       us_policy=None):
    """Run simulation for a single mineral.

    Args:
        mineral_name: Name of mineral
        n_steps: Number of steps to run
        geo_prob: Geopolitical event probability
        seed: Random seed
        output_dir: Output directory
        generate_viz: Whether to generate visualizations
        embargoes: Optional list of political-embargo dicts to add to config
        chokepoint_crises: Optional list of chokepoint-crisis dicts to add

    Returns:
        Model instance
    """
    print(f"\n{'='*70}")
    print(f"RUNNING {mineral_name.upper()} SIMULATION")
    print(f"{'='*70}")

    config = get_config(mineral_name)
    if config is None:
        raise ValueError(f"Unknown mineral: {mineral_name}")

    config = config.copy()
    if n_steps is not None:
        config['n_steps'] = n_steps
    if geo_prob is not None:
        config['geopolitical_event_probability'] = geo_prob
    if seed is not None:
        config['random_seed'] = seed
    if embargoes:
        config['political_embargoes'] = list(config.get('political_embargoes', [])) + list(embargoes)
    if chokepoint_crises:
        config['chokepoint_crises'] = list(config.get('chokepoint_crises', [])) + list(chokepoint_crises)
    if us_policy is not None:
        # Patch the per-country override block. We copy the parent dict
        # because get_config() returned a shared module-level constant
        # and config.copy() above is shallow -- mutating
        # country_overrides in place would persist across runs.
        country_overrides = dict(config.get('country_overrides', {}))
        country_overrides['USA'] = us_policy
        config['country_overrides'] = country_overrides
        print(f"  US policy: {len(us_policy)} override key(s) applied")
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Initialize model
    print("\nInitializing model...")
    model = MineralSupplyChainModel(config)
    
    # Run model
    model.run_model(config['n_steps'])
    
    # Get results
    print("\nCollecting results...")
    model_data = model.get_model_data()
    
    # Save CSV
    csv_path = os.path.join(output_dir, f"{mineral_name}_model_data.csv")
    model_data.to_csv(csv_path)
    print(f"Saved data to {csv_path}")
    
    # Generate visualizations
    if generate_viz:
        print("\nGenerating visualizations...")
        viz_path = os.path.join(output_dir, f"{mineral_name}_supply_chain_analysis.png")
        plot_supply_chain_analysis(model_data, config, viz_path)
    
    # Generate summary statistics
    print("\nGenerating summary statistics...")
    stats = create_summary_statistics(model_data, config)
    stats_path = os.path.join(output_dir, f"{mineral_name}_summary_stats.txt")
    save_summary_statistics(stats, stats_path)
    
    print(f"\n{mineral_name.upper()} simulation complete!")
    print(f"Results saved to {output_dir}/")
    
    return model


def run_all_minerals(n_steps=None, geo_prob=None, seed=None,
                     output_dir='outputs', generate_viz=True,
                     embargoes=None, chokepoint_crises=None,
                     us_policy=None):
    """Run simulations for all three minerals.

    Args:
        n_steps: Number of steps to run
        geo_prob: Geopolitical event probability
        seed: Random seed
        output_dir: Output directory
        generate_viz: Whether to generate visualizations
        embargoes: Optional list of political-embargo dicts (applied to
            every mineral; mines whose jurisdiction does not appear in
            that mineral's data simply ignore the entry).

    Returns:
        Dictionary of model instances
    """
    minerals = ['lithium', 'nickel', 'platinum']
    models = {}

    print(f"\n{'='*70}")
    print(f"RUNNING ALL MINERAL SIMULATIONS")
    print(f"{'='*70}")

    for mineral in minerals:
        models[mineral] = run_single_mineral(
            mineral, n_steps, geo_prob, seed, output_dir, generate_viz,
            embargoes=embargoes,
            chokepoint_crises=chokepoint_crises,
            us_policy=us_policy,
        )
    
    print(f"\n{'='*70}")
    print(f"ALL SIMULATIONS COMPLETE")
    print(f"{'='*70}")
    print(f"\nResults for all minerals saved to {output_dir}/")
    
    return models


def main():
    """Main entry point."""
    args = parse_arguments()
    
    # Print configuration
    print("\n" + "="*70)
    print("CRITICAL MINERALS SUPPLY CHAIN ABM")
    print("="*70)
    print(f"Configuration:")
    print(f"  Steps: {args.steps if args.steps else 'default (200)'}")
    print(f"  Geo probability: {args.geo_prob if args.geo_prob else 'default (0.01)'}")
    print(f"  Seed: {args.seed if args.seed else 'default (42)'}")
    print(f"  Output dir: {args.output_dir}")
    print(f"  Generate visualizations: {not args.no_viz}")
    
    embargoes = _parse_embargo_specs(args.embargo)
    chokepoint_crises = _parse_chokepoint_specs(args.chokepoint_crisis)
    us_policy = _load_us_policy(args.us_policy)
    if embargoes:
        print(f"  Embargoes: {embargoes}")
    if chokepoint_crises:
        print(f"  Chokepoint crises: {chokepoint_crises}")
    if us_policy is not None:
        print(f"  US policy file: {args.us_policy}")

    try:
        if args.all:
            run_all_minerals(
                n_steps=args.steps,
                geo_prob=args.geo_prob,
                seed=args.seed,
                output_dir=args.output_dir,
                generate_viz=not args.no_viz,
                embargoes=embargoes,
                chokepoint_crises=chokepoint_crises,
                us_policy=us_policy,
            )
        else:
            run_single_mineral(
                mineral_name=args.mineral,
                n_steps=args.steps,
                geo_prob=args.geo_prob,
                seed=args.seed,
                output_dir=args.output_dir,
                generate_viz=not args.no_viz,
                embargoes=embargoes,
                chokepoint_crises=chokepoint_crises,
                us_policy=us_policy,
            )
        
        print("\n✓ Simulation successful!")
        
    except Exception as e:
        print(f"\n✗ Error during simulation: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
