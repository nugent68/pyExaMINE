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

    # Price parameters ($/ton). The hard floor / ceiling are
    # outermost catastrophe bounds; in normal operation the
    # cost-curve soft band (price_floor_cost_fraction *
    # cheapest_active_cost ... price_ceiling_mc_multiple *
    # marginal_cost) does the real work.
    "initial_price": 17000,
    "price_floor": 3000,       # ~18% of initial; soft floor anchors at cheapest cost.
    "price_ceiling": 200000,   # ~12x initial; lets a true crisis show as a level.

    # Cost-anchored price model knobs (see src/model/market_mechanism.py).
    "price_elasticity": 0.25,           # move per unit log(supply/demand)
    "price_max_step_pct": 0.08,          # cap on per-step move
    "price_anchor_strength": 0.10,       # log-pull toward marginal cost
    "price_ceiling_mc_multiple": 8.0,    # soft ceiling = N x marginal cost
    "price_floor_cost_fraction": 0.6,    # soft floor = f x cheapest cost
    
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
    "consumer_price_sensitivity": -0.8,      # Demand elasticity (vs product price)
    "consumer_demand_threshold_multiplier": 2.0,  # Max acceptable price multiplier
    # Non-mineral component of the finished-product price ($/unit).
    # For Li the product unit is an EV; ~$40k base before battery-mineral
    # content. Used to apply elasticity to the actual product price
    # (mineral + base) rather than the bare mineral price.
    "consumer_product_base_price": 40000,
    
    # Retailer inventory policy
    "retailer_reorder_point_multiplier": 2.0,  # times average demand
    "retailer_order_quantity_multiplier": 3.0,
    "retailer_lead_time": 3,
    
    # Manufacturer inventory
    "manufacturer_target_inventory_weeks": 4,
    
    # Capacity expansion + reserve replacement
    # Li mine production grew ~30%/yr 2020-24; assume gradual moderation
    # to ~7-8%/yr CAGR over 2024-50 (consistent with IEA NetZero ramp).
    "mine_capacity_growth_per_year": 0.075,
    # Exploration replaces ~70% of extraction (Li reserves grew over the
    # past decade despite production growth).
    "reserve_replacement_rate": 0.70,
    # Restart lag for a mothballed mine: ~6 months to dewater, rehire,
    # recommission for hard-rock; less for brine. Use 26 weeks across.
    "mine_restart_lag_steps": 26,
    # Demand scenario to interpolate against (matches scenario column in
    # demand.csv). Use the IEA NetZero rows by default.
    "demand_scenario": "NetZero",

    # Simulation parameters
    "n_steps": 200,
    "random_seed": 42,
    "steps_per_year": 52,  # Weekly time steps
}
