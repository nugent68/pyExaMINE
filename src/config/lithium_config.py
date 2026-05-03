"""
Configuration for Lithium supply chain simulation.
"""

LITHIUM_CONFIG = {
    # Mineral identification
    "mineral_type": "Lithium",

    # Fallback annual mineral demand (tonnes/year) used only if the USGS
    # demand column for this mineral is absent. USGS_CMM.csv currently
    # supplies Lithium_Global_Demand_2024 (~150 kt), so this fallback is
    # informational unless the column is removed.
    "default_annual_demand_tons": 150000.0,

    # Price parameters ($/ton)
    "initial_price": 17000,
    "price_floor": 6800,      # 40% of initial
    "price_ceiling": 51000,   # 300% of initial
    
    # Agent counts
    "n_mines": "auto",        # Derived from USGS data
    "n_processors": 5,
    "n_manufacturers": 8,
    "n_retailers": 12,
    "n_consumers": 100,
    "n_recyclers": 3,
    "n_transport": 10,
    
    # Production parameters
    "avg_ore_grade": 0.85,
    "processor_conversion_efficiency": 0.80,
    "manufacturer_mineral_intensity": 0.008,  # tons Li per EV (~8 kg, 60-80 kWh NMC pack)
    
    # Economic parameters
    "base_extraction_cost": 8000,     # $/ton base cost
    "processor_energy_cost": 1500,    # $/ton processing
    "transport_cost_ship": 10,         # $/ton
    "transport_cost_rail": 25,         # $/ton
    "transport_cost_truck": 50,        # $/ton
    
    # Lead times (steps)
    "transport_lead_time_ship": 7,
    "transport_lead_time_rail": 4,
    "transport_lead_time_truck": 2,
    
    # Recycling parameters
    "collection_rate": 0.30,           # 30% of EOL collected
    "recovery_efficiency": 0.70,       # 70% recovered from collected
    "recycling_processing_cost": 5000, # $/ton
    "product_lifetime_steps": 520,    # ~10 years for EV batteries (8-15y typical)
    
    # Market parameters
    "geopolitical_event_probability": 0.01,  # 1% per step
    "mine_disruption_probability": 0.02,      # 2% per step
    "disruption_duration_min": 3,
    "disruption_duration_max": 5,
    "geopolitical_duration_min": 5,
    "geopolitical_duration_max": 15,
    
    # Manufacturer substitution
    "substitution_price_threshold": 25500,  # 150% of initial price
    "substitution_trigger_steps": 10,        # Consecutive high-price steps
    "substitution_rate": 0.05,               # 5% reduction per cycle
    "max_substitution": 0.30,                # Max 30% reduction
    
    # Consumer behavior
    "consumer_price_sensitivity": -0.8,      # Demand elasticity
    "consumer_demand_threshold_multiplier": 2.0,  # Max acceptable price multiplier
    
    # Retailer inventory policy
    "retailer_reorder_point_multiplier": 2.0,  # times average demand
    "retailer_order_quantity_multiplier": 3.0,
    "retailer_lead_time": 3,
    
    # Manufacturer inventory
    "manufacturer_target_inventory_weeks": 4,
    
    # Simulation parameters
    "n_steps": 200,
    "random_seed": 42,
    "steps_per_year": 52,  # Weekly time steps
}
