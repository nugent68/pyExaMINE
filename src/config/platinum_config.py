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
    
    # Fallback annual mineral demand (tonnes/year) used only if USGS_CMM.csv
    # has no Platinum_Global_Demand_2024 column. Real global Pt demand is
    # ~250 t/yr (autocatalysts, jewelry, fuel cells).
    "default_annual_demand_tons": 250.0,

    # Manufacturer mineral intensity (~3 g Pt per autocat; 1-7 g PGM typical).
    "manufacturer_mineral_intensity": 0.000003,

    # Per-mode transport unit costs ($/ton; precious-metal cargo so
    # security premium is high). Mine / processor / recycler parameters
    # live in the per-facility CSVs (data/platinum_*.csv).
    "transport_cost_ship": 5000,
    "transport_cost_rail": 8000,
    "transport_cost_truck": 12000,

    # Recycling collection rate (autocat scrap is the most-recycled
    # commodity stream globally; per-facility recovery efficiency /
    # processing cost are in data/platinum_recyclers.csv).
    "collection_rate": 0.75,
    "product_lifetime_steps": 624,    # ~12 years for autocatalysts (10-15y typical)
    
    # Market parameters
    "geopolitical_event_probability": 0.01,
    # Probability that a geopolitical event hits the refining tier
    # (PGM smelter / refinery) rather than the mining tier. Anglo
    # American Platinum smelter outages are a real-world precedent
    # (e.g., 2020 ACP Phase A converter explosion).
    "geopolitical_processor_event_share": 0.30,
    "mine_disruption_probability": 0.02,
    "disruption_duration_min": 3,
    "disruption_duration_max": 5,
    "geopolitical_duration_min": 5,
    "geopolitical_duration_max": 15,
    
    # Manufacturer substitution (less substitution for platinum due to
    # unique properties). Forward fires on sustained high prices
    # (autocat thrifting + Pt -> Pd swap); reversion fires on sustained
    # low prices (Pd-rich -> back to Pt-rich when relative pricing
    # reverses, as has happened multiple times in 2000-2024). Reversion
    # is much slower than for Li/Ni because PGM autocat washcoat
    # tooling has long requalification cycles.
    "substitution_price_threshold": 45000000,        # 150% of initial $30M
    "substitution_revert_threshold": 20000000,       # 66.7% of initial
    "substitution_trigger_steps": 12,                # Longer trigger (harder to substitute)
    "substitution_revert_trigger_steps": 39,         # ~9 months of below-revert pricing
    "substitution_rate": 0.03,                       # 3%/cycle (slower)
    "substitution_revert_rate": 0.02,                # 2%/cycle reversion (even slower)
    "max_substitution": 0.20,                        # Max 20% reduction (less flexible)
    
    # Consumer behavior (less price-sensitive due to no alternatives)
    "consumer_price_sensitivity": -0.5,
    "consumer_demand_threshold_multiplier": 2.5,
    # Non-mineral component of finished-product price ($/unit). The Pt
    # consumer unit is roughly a vehicle whose autocat carries Pt; use
    # a vehicle base price so elasticity sees the realistic share of
    # mineral cost in the consumer-facing good.
    "consumer_product_base_price": 30000,
    
    # Retailer inventory policy. (s, Q) thresholds in weeks of the
    # demand-EWMA. Pt uses a slightly larger reorder point + order
    # quantity than Li/Ni to reflect the higher transport security
    # cost (high-value cargo) -- carrying a few extra weeks of buffer
    # is cheap relative to insuring extra shipments.
    "retailer_reorder_point_multiplier": 5.0,
    "retailer_order_quantity_multiplier": 3.5,
    
    # Manufacturer inventory
    "manufacturer_target_inventory_weeks": 6,  # Longer due to supply concentration
    
    # Capacity expansion + reserve replacement
    # Pt is very mature; Bushveld output is roughly flat to slightly
    # declining. Allow modest 1%/yr nominal growth to keep pace with
    # demand from fuel-cell adoption.
    "mine_capacity_growth_per_year": 0.01,
    # Price-responsive accelerated growth: deep-shaft Pt mining is
    # capex-heavy and slow to scale, but sustained 2x+ pricing has
    # historically pulled out brownfield expansion + recommissioning
    # of mothballed shafts (Bushveld 2008-09 cycle, Sibanye-Stillwater
    # 2020-22 PGM ramp). 5%/yr is achievable under sustained spike
    # conditions even though baseline build is near-zero.
    "mine_capacity_growth_per_year_high": 0.05,
    "mine_expansion_price_threshold": 2.0,
    "mine_expansion_trigger_steps": 52,
    # PGM smelting/refining capacity is similarly mature (Anglo, Sibanye
    # base loads). Match the 1%/yr nominal growth.
    "processor_capacity_growth_per_year": 0.01,
    "reserve_replacement_rate": 0.30,
    # Pt mining is deep underground; restart is slower (~9-12 months).
    "mine_restart_lag_steps": 40,
    # Even a "warm" Pt restart takes longer than other minerals
    # because of underground reconditioning. ~6 months.
    "mine_warm_restart_lag_steps": 26,
    "mine_warm_restart_window_steps": 78,
    # Pt operations very rarely full-shutter; mothballs at the shaft
    # level (e.g., Sibanye Stillwater 2024) typically follow ~12 months
    # of below-cost. 52 weeks matches the historical pattern from the
    # 2008-09 crash and 2014-16 weakness, both of which produced
    # closures only after roughly a year of sustained pressure.
    "mothball_trigger_steps": 52,
    # Pt cash cost is meaningfully below all-in cost thanks to
    # by-product credits (Pd, Rh, Ni). 0.6 keeps the trigger sensitive
    # to truly bad markets without firing on normal margin compression.
    "mine_cash_cost_fraction": 0.60,
    "demand_scenario": "NetZero",

    # Simulation parameters
    "n_steps": 200,
    "random_seed": 42,
    "steps_per_year": 52,
}
