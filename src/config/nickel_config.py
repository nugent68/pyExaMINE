"""
Configuration for Nickel supply chain simulation.
"""

NICKEL_CONFIG = {
    # Mineral identification
    "mineral_type": "Nickel",

    # Fallback annual mineral demand (tonnes/year) used only if the USGS
    # demand column for this mineral is absent. USGS_CMM.csv currently
    # supplies Nickel_Global_Demand_2024 (562 kt), so this fallback is
    # informational unless the column is removed.
    "default_annual_demand_tons": 561724.0,

    # USGS_CMM.csv reports Nickel production and reserves in tonnes already.
    "usgs_units_to_tons": 1.0,

    # Price parameters ($/ton)
    "initial_price": 18000,
    "price_floor": 7200,      # 40% of initial
    "price_ceiling": 54000,   # 300% of initial
    
    # Agent counts
    "n_mines": "auto",        # Derived from USGS data
    "n_processors": 5,
    "n_manufacturers": 10,
    "n_retailers": 15,
    "n_consumers": 120,
    "n_recyclers": 4,
    "n_transport": 12,
    
    # Production parameters
    "avg_ore_grade": 0.65,    # Lower than Lithium
    "processor_conversion_efficiency": 0.75,
    "manufacturer_mineral_intensity": 0.04,  # tons Ni per EV
    
    # Economic parameters
    "base_extraction_cost": 9000,     # $/ton base cost
    "processor_energy_cost": 2000,    # $/ton processing
    "transport_cost_ship": 12,        # $/ton
    "transport_cost_rail": 28,        # $/ton
    "transport_cost_truck": 55,       # $/ton
    
    # Lead times (steps)
    "transport_lead_time_ship": 6,
    "transport_lead_time_rail": 4,
    "transport_lead_time_truck": 2,
    
    # Recycling parameters (higher for established nickel recycling)
    "collection_rate": 0.60,           # 60% of EOL collected
    "recovery_efficiency": 0.75,       # 75% recovered from collected
    "recycling_processing_cost": 4500, # $/ton
    "product_lifetime_steps": 25,
    
    # Market parameters
    "geopolitical_event_probability": 0.01,
    "mine_disruption_probability": 0.02,
    "disruption_duration_min": 3,
    "disruption_duration_max": 5,
    "geopolitical_duration_min": 5,
    "geopolitical_duration_max": 15,
    
    # Manufacturer substitution
    "substitution_price_threshold": 27000,  # 150% of initial price
    "substitution_trigger_steps": 10,
    "substitution_rate": 0.05,
    "max_substitution": 0.30,
    
    # Consumer behavior
    "consumer_price_sensitivity": -0.7,     # Slightly less elastic than Li
    "consumer_demand_threshold_multiplier": 2.0,
    
    # Retailer inventory policy
    "retailer_reorder_point_multiplier": 2.0,
    "retailer_order_quantity_multiplier": 3.0,
    "retailer_lead_time": 3,
    
    # Manufacturer inventory
    "manufacturer_target_inventory_weeks": 4,
    
    # Simulation parameters
    "n_steps": 200,
    "random_seed": 42,
    "steps_per_year": 52,
}
