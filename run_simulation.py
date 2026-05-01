#!/usr/bin/env python3
"""
Main script to run critical minerals supply chain simulations.
Usage: python run_simulation.py --mineral lithium --steps 200
"""

import argparse
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
        default='outputs',
        help='Output directory for results'
    )
    
    parser.add_argument(
        '--no-viz',
        action='store_true',
        help='Skip visualization generation'
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


def run_single_mineral(mineral_name, n_steps=None, geo_prob=None, seed=None, 
                       output_dir='outputs', generate_viz=True):
    """Run simulation for a single mineral.
    
    Args:
        mineral_name: Name of mineral
        n_steps: Number of steps to run
        geo_prob: Geopolitical event probability
        seed: Random seed
        output_dir: Output directory
        generate_viz: Whether to generate visualizations
    
    Returns:
        Model instance
    """
    print(f"\n{'='*70}")
    print(f"RUNNING {mineral_name.upper()} SIMULATION")
    print(f"{'='*70}")
    
    # Get configuration
    config = get_config(mineral_name)
    if config is None:
        raise ValueError(f"Unknown mineral: {mineral_name}")
    
    # Override config parameters if specified
    config = config.copy()
    if n_steps is not None:
        config['n_steps'] = n_steps
    if geo_prob is not None:
        config['geopolitical_event_probability'] = geo_prob
    if seed is not None:
        config['random_seed'] = seed
    
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
                     output_dir='outputs', generate_viz=True):
    """Run simulations for all three minerals.
    
    Args:
        n_steps: Number of steps to run
        geo_prob: Geopolitical event probability
        seed: Random seed
        output_dir: Output directory
        generate_viz: Whether to generate visualizations
    
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
            mineral, n_steps, geo_prob, seed, output_dir, generate_viz
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
    
    try:
        if args.all:
            # Run all minerals
            run_all_minerals(
                n_steps=args.steps,
                geo_prob=args.geo_prob,
                seed=args.seed,
                output_dir=args.output_dir,
                generate_viz=not args.no_viz
            )
        else:
            # Run single mineral
            run_single_mineral(
                mineral_name=args.mineral,
                n_steps=args.steps,
                geo_prob=args.geo_prob,
                seed=args.seed,
                output_dir=args.output_dir,
                generate_viz=not args.no_viz
            )
        
        print("\n✓ Simulation successful!")
        
    except Exception as e:
        print(f"\n✗ Error during simulation: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
