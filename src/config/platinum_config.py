"""
Configuration for Platinum supply chain simulation.
"""

PLATINUM_CONFIG = {
    # Mineral identification
    "mineral_type": "Platinum",
    
    # Price parameters ($/ton) - Note: Platinum is precious metal, very high prices
    "initial_price": 30000000,    # ~$1000/oz * 31103 g/kg * 1000 kg/ton
    "price_floor": 12000000,      # 40% of initial
    "price_ceiling": 90000000,    # 300% of initial
    
    # Agent counts (fewer due to concentrated production)
    "n_mines": "auto",        # Derived from USGS data (very few producers)
    "n_processors": 3,
    "n_manufacturers": 6,
    "n_retailers": 8,
    "n_consumers": 80,
    "n_recyclers": 2,
    "n_transport": 8,
    
    # Production parameters (precious metal characteristics)
    "avg_ore_grade": 0.55,     # Very low grade ore
    "processor_conversion_efficiency": 0.70,
    "manufacturer_mineral_intensity": 0.00005,  # grams per catalyst (very small)
    
    # Economic parameters (high costs for precious metals)
    "base_extraction_cost": 15000000,  # $/ton base cost
    "processor_energy_cost": 5000000,  # $/ton processing
    "transport_cost_ship": 5000,       # $/ton (high value = high security)
    "transport_cost_rail": 8000,       # $/ton
    "transport_cost_truck": 12000,     # $/ton
    
    # Lead times (steps)
    "transport_lead_time_ship": 5,
    "transport_lead_time_rail": 3,
    "transport_lead_time_truck": 1,
    
    # Recycling parameters (very high for precious metals)
    "collection_rate": 0.75,            # 75% of EOL collected
    "recovery_efficiency": 0.85,        # 85% recovered from collected
    "recycling_processing_cost": 8000000,  # $/ton
    "product_lifetime_steps": 25,
    
    # Market parameters
    "geopolitical_event_probability": 0.01,
    "mine_disruption_probability": 0.02,
    "disruption_duration_min": 3,
    "disruption_duration_max": 5,
    "geopolitical_duration_min": 5,
    "geopolitical_duration_max": 15,
    
    # Manufacturer substitution (less substitution for platinum due to unique properties)
    "substitution_price_threshold": 45000000,  # 150% of initial price
    "substitution_trigger_steps": 12,           # Longer trigger (harder to substitute)
    "substitution_rate": 0.03,                  # 3% reduction per cycle (slower)
    "max_substitution": 0.20,                   # Max 20% reduction (less flexible)
    
    # Consumer behavior (less price-sensitive due to no alternatives)
    "consumer_price_sensitivity": -0.5,
    "consumer_demand_threshold_multiplier": 2.5,
    
    # Retailer inventory policy
    "retailer_reorder_point_multiplier": 2.5,  # Higher safety stock
    "retailer_order_quantity_multiplier": 3.5,
    "retailer_lead_time": 4,
    
    # Manufacturer inventory
    "manufacturer_target_inventory_weeks": 6,  # Longer due to supply concentration
    
    # Simulation parameters
    "n_steps": 200,
    "random_seed": 42,
    "steps_per_year": 52,
}
