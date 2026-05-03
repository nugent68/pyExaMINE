"""
Configuration for Nickel supply chain simulation.
"""

NICKEL_CONFIG = {
    # Mineral identification
    "mineral_type": "Nickel",

    # Fallback annual mineral demand (tonnes/year) used only if the USGS
    # demand column for this mineral is absent. USGS_CMM.csv currently
    # supplies Nickel_Global_Demand_2024 (~3.5 Mt), so this fallback is
    # informational unless the column is removed.
    "default_annual_demand_tons": 3500000.0,

    # Price parameters ($/ton). Cost-curve soft band does the real work.
    "initial_price": 18000,
    "price_floor": 3000,       # outer catastrophe bound
    "price_ceiling": 250000,   # outer catastrophe bound

    # Cost-anchored price model knobs.
    "price_elasticity": 0.25,
    "price_max_step_pct": 0.08,
    "price_anchor_strength": 0.10,
    "price_ceiling_mc_multiple": 8.0,
    "price_floor_cost_fraction": 0.6,
    
    # Manufacturer mineral intensity (tons Ni per EV).
    "manufacturer_mineral_intensity": 0.04,

    # Per-mode transport unit costs ($/ton; diagnostics only).
    # Mine / processor / recycler parameters live in the per-facility
    # CSVs (data/nickel_*.csv).
    "transport_cost_ship": 12,
    "transport_cost_rail": 28,
    "transport_cost_truck": 55,

    # Recycling collection rate (aggregate share of available EOL
    # picked up per step; per-facility recovery efficiency / processing
    # cost are in data/nickel_recyclers.csv).
    "collection_rate": 0.60,
    "product_lifetime_steps": 520,    # ~10 years (EV battery mix; stainless 20-30y)
    
    # Market parameters
    "geopolitical_event_probability": 0.01,
    # Probability that a geopolitical event hits the refining tier
    # rather than the mining tier. Ni refining is unusually
    # concentrated (Indonesian RKEF, Chinese HPAL); a higher share
    # would be defensible for Ni specifically, but 0.30 keeps the
    # cross-mineral comparison consistent.
    "geopolitical_processor_event_share": 0.30,
    "mine_disruption_probability": 0.02,
    "disruption_duration_min": 3,
    "disruption_duration_max": 5,
    "geopolitical_duration_min": 5,
    "geopolitical_duration_max": 15,
    
    # Manufacturer substitution. Forward fires on sustained high
    # prices (NMC -> LFP shift); reversion fires on sustained low
    # prices (LFP -> back to NMC, or low-Co NMC chemistries reverting).
    # Reversion threshold uses 0.667x initial as the symmetric dual of
    # the 1.5x forward trigger.
    "substitution_price_threshold": 27000,           # 150% of initial $18000
    "substitution_revert_threshold": 12000,          # 66.7% of initial
    "substitution_trigger_steps": 10,
    "substitution_revert_trigger_steps": 26,         # ~6 months of below-revert pricing
    "substitution_rate": 0.05,
    "substitution_revert_rate": 0.03,
    "max_substitution": 0.30,
    
    # Consumer behavior
    "consumer_price_sensitivity": -0.7,     # Slightly less elastic than Li
    "consumer_demand_threshold_multiplier": 2.0,
    # Non-mineral component of the finished-product price ($/unit).
    # The Ni model treats all consumption as EV-equivalents; base car
    # price ~$40k pre-battery mineral content.
    "consumer_product_base_price": 40000,
    
    # Retailer inventory policy. (s, Q) thresholds in weeks of the
    # demand-EWMA; ~3 wk ship lead time + 1 wk safety stock.
    "retailer_reorder_point_multiplier": 4.0,
    "retailer_order_quantity_multiplier": 3.0,
    
    # Manufacturer inventory
    "manufacturer_target_inventory_weeks": 4,
    
    # Capacity expansion + reserve replacement
    # Indonesian Ni capacity has been growing ~15%/yr; global Ni more like
    # 3-4%/yr blended. Use 0.04 as a globally representative CAGR.
    "mine_capacity_growth_per_year": 0.04,
    # Price-responsive accelerated growth: under sustained price >
    # 2x extraction cost, capacity ramp accelerates to ~10%/yr -- in
    # line with what Indonesian RKEF buildout achieved during the
    # 2021-23 EV-grade Ni demand surge.
    "mine_capacity_growth_per_year_high": 0.10,
    "mine_expansion_price_threshold": 2.0,
    "mine_expansion_trigger_steps": 52,
    # Refining capacity tracks mining (Indonesian RKEF + HPAL builds);
    # use the same blended CAGR as mining.
    "processor_capacity_growth_per_year": 0.04,
    # Ni exploration is mature; modest replacement.
    "reserve_replacement_rate": 0.50,
    # Restart lag (open-pit Ni laterite restart is somewhat faster than
    # underground; HPAL plants take longer). 26 weeks is a fair average.
    "mine_restart_lag_steps": 26,
    # Warm restart for recently-mothballed Ni operations. Indonesian
    # RKEF can warm-restart in ~2 months; Australian sulphide closer
    # to 4. Use 12 weeks as a portfolio average.
    "mine_warm_restart_lag_steps": 12,
    "mine_warm_restart_window_steps": 52,
    # Sustained-pressure mothball: ~12 months of below-cash-cost. The
    # 2024 Australian Ni mothballs (BHP Nickel West, Wyloo Kambalda)
    # followed roughly 9-12 months of below-cost prices; Indonesian
    # operations have ridden out longer dips. 52 weeks reflects the
    # realistic strategic-decision horizon rather than a knife-edge
    # quarterly trigger.
    "mothball_trigger_steps": 52,
    # Cash cost ~65% of all-in cost; varies (Indonesian RKEF lower,
    # Australian sulphide higher) but 0.65 is a reasonable blend.
    "mine_cash_cost_fraction": 0.65,
    "demand_scenario": "NetZero",

    # Per-country agent fan-out (see lithium_config.py for full notes).
    "agents_per_gdp_billion": 500.0,

    # Simulation parameters
    "n_steps": 200,
    "random_seed": 42,
    "steps_per_year": 52,
}
