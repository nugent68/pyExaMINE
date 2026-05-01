"""
Visualization module for supply chain model results.
Creates 6-panel dashboard with matplotlib.
"""

import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import numpy as np


def plot_supply_chain_analysis(model_data, config, output_path=None):
    """Create comprehensive 6-panel visualization of supply chain dynamics.
    
    Args:
        model_data: DataFrame from model.get_model_data()
        config: Configuration dictionary
        output_path: Path to save figure (if None, displays instead)
    
    Returns:
        matplotlib Figure object
    """
    # Set style
    sns.set_style("whitegrid")
    
    # Create figure with 2x3 subplots
    fig, axes = plt.subplots(2, 3, figsize=(18, 10))
    fig.suptitle(f"{config['mineral_type']} Supply Chain Analysis - {len(model_data)} Steps", 
                 fontsize=16, fontweight='bold')
    
    # Plot each panel
    _plot_price(axes[0, 0], model_data, config)
    _plot_inventory(axes[0, 1], model_data, config)
    _plot_supply_comparison(axes[0, 2], model_data, config)
    _plot_demand(axes[1, 0], model_data, config)
    _plot_disruptions(axes[1, 1], model_data, config)
    _plot_substitution(axes[1, 2], model_data, config)
    
    # Adjust layout
    plt.tight_layout()
    
    # Save or show
    if output_path:
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        print(f"Saved visualization to {output_path}")
    else:
        plt.show()
    
    return fig


def _plot_price(ax, data, config):
    """Plot mineral price over time."""
    ax.plot(data.index, data['Global_Price'], linewidth=2, color='#2E86AB', label='Market Price')
    
    # Add price bounds as dashed lines
    ax.axhline(y=config['price_floor'], color='red', linestyle='--', 
               linewidth=1, alpha=0.7, label='Price Floor')
    ax.axhline(y=config['price_ceiling'], color='red', linestyle='--', 
               linewidth=1, alpha=0.7, label='Price Ceiling')
    ax.axhline(y=config['initial_price'], color='gray', linestyle=':', 
               linewidth=1, alpha=0.5, label='Initial Price')
    
    ax.set_xlabel('Time Step', fontsize=11)
    ax.set_ylabel('Price ($/ton)', fontsize=11)
    ax.set_title('Mineral Price Dynamics', fontsize=12, fontweight='bold')
    ax.legend(loc='best', fontsize=9)
    ax.grid(True, alpha=0.3)
    
    # Format y-axis as currency
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))


def _plot_inventory(ax, data, config):
    """Plot total processor inventory over time."""
    ax.fill_between(data.index, 0, data['Total_Processor_Inventory'], 
                     alpha=0.6, color='#06A77D', label='Processor Inventory')
    ax.plot(data.index, data['Total_Processor_Inventory'], 
            linewidth=2, color='#048A61')
    
    # Add reference line for "healthy" inventory
    if 'Total_Consumer_Demand' in data.columns:
        healthy_level = data['Total_Consumer_Demand'].mean() * 2
        ax.axhline(y=healthy_level, color='orange', linestyle='--', 
                   linewidth=1, alpha=0.7, label='Healthy Level (2x avg demand)')
    
    ax.set_xlabel('Time Step', fontsize=11)
    ax.set_ylabel('Inventory (tons)', fontsize=11)
    ax.set_title('Processor Inventory Levels', fontsize=12, fontweight='bold')
    ax.legend(loc='best', fontsize=9)
    ax.grid(True, alpha=0.3)


def _plot_supply_comparison(ax, data, config):
    """Plot mine output vs. recycled supply."""
    ax.plot(data.index, data['Total_Mine_Output'], 
            linewidth=2, color='#A23B72', label='Mine Output', linestyle='-')
    ax.plot(data.index, data['Total_Recycled_Supply'], 
            linewidth=2, color='#18B0B0', label='Recycled Supply', linestyle='--')
    
    # Calculate and show recycling rate at end
    if len(data) > 10:
        recent_mine = data['Total_Mine_Output'].iloc[-10:].mean()
        recent_recycled = data['Total_Recycled_Supply'].iloc[-10:].mean()
        if recent_mine + recent_recycled > 0:
            recycling_rate = recent_recycled / (recent_mine + recent_recycled) * 100
            ax.text(0.05, 0.95, f'Recycling Rate (final 10 steps): {recycling_rate:.1f}%',
                   transform=ax.transAxes, fontsize=9, verticalalignment='top',
                   bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    
    ax.set_xlabel('Time Step', fontsize=11)
    ax.set_ylabel('Supply (tons/step)', fontsize=11)
    ax.set_title('Primary vs. Secondary Supply', fontsize=12, fontweight='bold')
    ax.legend(loc='best', fontsize=9)
    ax.grid(True, alpha=0.3)


def _plot_demand(ax, data, config):
    """Plot fulfilled vs. unfulfilled demand."""
    # Stacked area chart
    ax.fill_between(data.index, 0, data['Fulfilled_Demand'], 
                     alpha=0.7, color='#4CAF50', label='Fulfilled')
    ax.fill_between(data.index, data['Fulfilled_Demand'], 
                     data['Fulfilled_Demand'] + data['Unfulfilled_Demand'],
                     alpha=0.7, color='#F44336', label='Unfulfilled')
    
    # Calculate fulfillment rate
    total_fulfilled = data['Fulfilled_Demand'].sum()
    total_demand = data['Fulfilled_Demand'].sum() + data['Unfulfilled_Demand'].sum()
    if total_demand > 0:
        fulfillment_rate = total_fulfilled / total_demand * 100
        ax.text(0.05, 0.95, f'Overall Fulfillment: {fulfillment_rate:.1f}%',
               transform=ax.transAxes, fontsize=9, verticalalignment='top',
               bbox=dict(boxstyle='round', facecolor='lightblue', alpha=0.5))
    
    ax.set_xlabel('Time Step', fontsize=11)
    ax.set_ylabel('Demand (tons/step)', fontsize=11)
    ax.set_title('Demand Fulfillment', fontsize=12, fontweight='bold')
    ax.legend(loc='best', fontsize=9)
    ax.grid(True, alpha=0.3)


def _plot_disruptions(ax, data, config):
    """Plot number of disrupted mines over time."""
    ax.bar(data.index, data['Disrupted_Mines_Count'], 
           color='#E63946', alpha=0.7, width=1.0)
    
    # Add line showing average
    avg_disrupted = data['Disrupted_Mines_Count'].mean()
    ax.axhline(y=avg_disrupted, color='darkred', linestyle='--', 
               linewidth=1, alpha=0.7, label=f'Average: {avg_disrupted:.1f}')
    
    ax.set_xlabel('Time Step', fontsize=11)
    ax.set_ylabel('Number of Mines Disrupted', fontsize=11)
    ax.set_title('Mine Disruptions', fontsize=12, fontweight='bold')
    ax.legend(loc='best', fontsize=9)
    ax.grid(True, alpha=0.3, axis='y')


def _plot_substitution(ax, data, config):
    """Plot manufacturer mineral intensity showing substitution effect."""
    initial_intensity = config.get('manufacturer_mineral_intensity', 0.08)
    
    ax.plot(data.index, data['Avg_Manufacturer_Intensity'], 
            linewidth=2, color='#6A4C93')
    ax.axhline(y=initial_intensity, color='gray', linestyle=':', 
               linewidth=1, alpha=0.5, label='Initial Intensity')
    
    # Calculate reduction
    if len(data) > 10:
        final_intensity = data['Avg_Manufacturer_Intensity'].iloc[-10:].mean()
        reduction = (initial_intensity - final_intensity) / initial_intensity * 100
        ax.text(0.05, 0.05, f'Intensity Reduction: {reduction:.1f}%',
               transform=ax.transAxes, fontsize=9, verticalalignment='bottom',
               bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.5))
    
    ax.set_xlabel('Time Step', fontsize=11)
    ax.set_ylabel('Mineral Intensity (tons/unit)', fontsize=11)
    ax.set_title('Material Substitution Effect', fontsize=12, fontweight='bold')
    ax.legend(loc='best', fontsize=9)
    ax.grid(True, alpha=0.3)


def create_summary_statistics(model_data, config):
    """Create summary statistics text file.
    
    Args:
        model_data: DataFrame from model
        config: Configuration dictionary
    
    Returns:
        Dictionary of summary statistics
    """
    stats = {
        'mineral': config['mineral_type'],
        'n_steps': len(model_data),
        'avg_price': model_data['Global_Price'].mean(),
        'final_price': model_data['Global_Price'].iloc[-1],
        'price_volatility': model_data['Global_Price'].std(),
        'avg_processor_inventory': model_data['Total_Processor_Inventory'].mean(),
        'avg_mine_output': model_data['Total_Mine_Output'].mean(),
        'avg_recycled_supply': model_data['Total_Recycled_Supply'].mean(),
        'total_demand': (model_data['Fulfilled_Demand'] + model_data['Unfulfilled_Demand']).sum(),
        'fulfillment_rate': model_data['Fulfilled_Demand'].sum() / 
                           (model_data['Fulfilled_Demand'].sum() + model_data['Unfulfilled_Demand'].sum()) * 100,
        'avg_disrupted_mines': model_data['Disrupted_Mines_Count'].mean(),
        'final_intensity': model_data['Avg_Manufacturer_Intensity'].iloc[-1],
        'intensity_reduction': (config['manufacturer_mineral_intensity'] - 
                               model_data['Avg_Manufacturer_Intensity'].iloc[-1]) / 
                               config['manufacturer_mineral_intensity'] * 100,
    }
    
    # Calculate recycling rate
    total_mine = model_data['Total_Mine_Output'].sum()
    total_recycled = model_data['Total_Recycled_Supply'].sum()
    if total_mine + total_recycled > 0:
        stats['recycling_rate'] = total_recycled / (total_mine + total_recycled) * 100
    else:
        stats['recycling_rate'] = 0.0
    
    return stats


def save_summary_statistics(stats, output_path):
    """Save summary statistics to text file.
    
    Args:
        stats: Dictionary of statistics
        output_path: Path to save file
    """
    with open(output_path, 'w') as f:
        f.write(f"{'='*60}\n")
        f.write(f"Supply Chain Model Summary: {stats['mineral']}\n")
        f.write(f"{'='*60}\n\n")
        
        f.write(f"Simulation Length: {stats['n_steps']} steps\n\n")
        
        f.write("PRICE DYNAMICS:\n")
        f.write(f"  Average Price: ${stats['avg_price']:,.2f}/ton\n")
        f.write(f"  Final Price: ${stats['final_price']:,.2f}/ton\n")
        f.write(f"  Price Volatility (std): ${stats['price_volatility']:,.2f}\n\n")
        
        f.write("SUPPLY:\n")
        f.write(f"  Average Mine Output: {stats['avg_mine_output']:,.2f} tons/step\n")
        f.write(f"  Average Recycled Supply: {stats['avg_recycled_supply']:,.2f} tons/step\n")
        f.write(f"  Recycling Rate: {stats['recycling_rate']:.2f}%\n")
        f.write(f"  Average Processor Inventory: {stats['avg_processor_inventory']:,.2f} tons\n\n")
        
        f.write("DEMAND:\n")
        f.write(f"  Total Demand: {stats['total_demand']:,.2f} tons\n")
        f.write(f"  Fulfillment Rate: {stats['fulfillment_rate']:.2f}%\n\n")
        
        f.write("DISRUPTIONS:\n")
        f.write(f"  Average Disrupted Mines: {stats['avg_disrupted_mines']:.2f}\n\n")
        
        f.write("SUBSTITUTION:\n")
        f.write(f"  Final Mineral Intensity: {stats['final_intensity']:.6f} tons/unit\n")
        f.write(f"  Intensity Reduction: {stats['intensity_reduction']:.2f}%\n")
    
    print(f"Saved summary statistics to {output_path}")
