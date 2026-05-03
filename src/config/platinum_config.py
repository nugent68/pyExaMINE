"""
Configuration for Platinum supply chain simulation.
"""

PLATINUM_CONFIG = {
    # Mineral identification
    "mineral_type": "Platinum",
    
    # Price parameters ($/ton). Pt is a precious metal so prices are high.
    # Cost-curve soft band does the real work; hard limits are catastrophe bounds.
    "initial_price": 30000000,     # ~$1000/oz * 31103 g/kg * 1000 kg/ton
    "price_floor":     5000000,    # outer catastrophe bound (~17% of initial)
    "price_ceiling": 400000000,    # outer catastrophe bound (~13x initial)

    # Cost-anchored price model knobs.
    "price_elasticity": 0.25,
    "price_max_step_pct": 0.08,
    "price_anchor_strength": 0.10,
    "price_ceiling_mc_multiple": 8.0,
    "price_floor_cost_fraction": 0.6,
    
    # Agent counts (fewer due to concentrated production)
    "n_mines": "auto",        # Derived from USGS data (very few producers)
    "n_processors": 3,
    "n_manufacturers": 6,
    "n_retailers": 8,
    "n_consumers": 80,
    "n_recyclers": 2,
    "n_transport": 8,
    
    # Fallback annual mineral demand (tonnes/year) used only if USGS_CMM.csv
    # has no Platinum_Global_Demand_2024 column. Real global Pt demand is
    # ~250 t/yr (autocatalysts, jewelry, fuel cells).
    "default_annual_demand_tons": 250.0,

    # Production parameters (precious metal characteristics)
    "avg_ore_grade": 0.55,     # Very low grade ore
    "processor_conversion_efficiency": 0.70,
    "manufacturer_mineral_intensity": 0.000003,  # ~3 g Pt per autocatalyst (1-7 g PGM typical)
    
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
    "product_lifetime_steps": 624,    # ~12 years for autocatalysts (10-15y typical)
    
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
    # Non-mineral component of finished-product price ($/unit). The Pt
    # consumer unit is roughly a vehicle whose autocat carries Pt; use
    # a vehicle base price so elasticity sees the realistic share of
    # mineral cost in the consumer-facing good.
    "consumer_product_base_price": 30000,
    
    # Retailer inventory policy
    "retailer_reorder_point_multiplier": 2.5,  # Higher safety stock
    "retailer_order_quantity_multiplier": 3.5,
    "retailer_lead_time": 4,
    
    # Manufacturer inventory
    "manufacturer_target_inventory_weeks": 6,  # Longer due to supply concentration
    
    # Capacity expansion + reserve replacement
    # Pt is very mature; Bushveld output is roughly flat to slightly
    # declining. Allow modest 1%/yr nominal growth to keep pace with
    # demand from fuel-cell adoption.
    "mine_capacity_growth_per_year": 0.01,
    "reserve_replacement_rate": 0.30,
    # Pt mining is deep underground; restart is slower (~9-12 months).
    "mine_restart_lag_steps": 40,
    "demand_scenario": "NetZero",

    # Simulation parameters
    "n_steps": 200,
    "random_seed": 42,
    "steps_per_year": 52,
}
