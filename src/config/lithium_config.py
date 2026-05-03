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
    
    # Manufacturer mineral intensity (tons Li per EV; ~8 kg / 60-80
    # kWh NMC pack). Used at construction to size the manufacturer
    # capacity headroom and the EOL deposit / recycling baseline.
    "manufacturer_mineral_intensity": 0.008,

    # Per-mode transport unit costs ($/ton; used by transport agent
    # cost field for diagnostics). Mine / processor / recycler / route
    # parameters all live in the per-facility CSVs (data/lithium_*.csv)
    # and the global route table (src/data/routing.py); only knobs
    # that aren't per-facility are tracked here.
    "transport_cost_ship": 10,
    "transport_cost_rail": 25,
    "transport_cost_truck": 50,

    # Recycling collection rate (aggregate share of available EOL
    # picked up by recyclers per step; per-facility recovery efficiency
    # / processing cost are in data/lithium_recyclers.csv).
    "collection_rate": 0.30,
    "product_lifetime_steps": 520,    # ~10 years for EV batteries (8-15y typical)
    
    # Market parameters
    "geopolitical_event_probability": 0.01,  # 1% per step
    # Probability that a geopolitical event hits the refining tier
    # rather than the mining tier. Real-world refinery outages
    # (Indonesian smelters, Chinese power curtailments, sanctioned
    # operators) are less common than mining incidents but still
    # significant; 0.30 reflects roughly the 1:2 ratio of refinery to
    # mine disruption events in 2010-24 industry data.
    "geopolitical_processor_event_share": 0.30,
    "mine_disruption_probability": 0.02,      # 2% per step
    "disruption_duration_min": 3,
    "disruption_duration_max": 5,
    "geopolitical_duration_min": 5,
    "geopolitical_duration_max": 15,
    
    # Manufacturer substitution.
    # Forward (intensity-down) when price stays high. Reverse
    # (intensity-back-up) when price stays low for a longer window:
    # historically LFP took share from NMC during the 2022-23 Li spike,
    # and crash-back-to-cheap-Li would restore some NMC share over
    # multiple years. Reversion is intentionally slower than adoption.
    "substitution_price_threshold": 25500,           # 150% of initial price
    "substitution_revert_threshold": 11333,          # 66.7% of initial; LFP/NMC cell-cost crossover ~$10k
    "substitution_trigger_steps": 10,                # Consecutive high-price steps
    "substitution_revert_trigger_steps": 26,         # ~6 months of below-revert pricing
    "substitution_rate": 0.05,                       # 5% reduction per cycle
    "substitution_revert_rate": 0.03,                # 3%/cycle re-adoption (slower than going to LFP)
    "max_substitution": 0.30,                        # Max 30% reduction
    
    # Consumer behavior
    "consumer_price_sensitivity": -0.8,      # Demand elasticity (vs product price)
    "consumer_demand_threshold_multiplier": 2.0,  # Max acceptable price multiplier
    # Non-mineral component of the finished-product price ($/unit).
    # For Li the product unit is an EV; ~$40k base before battery-mineral
    # content. Used to apply elasticity to the actual product price
    # (mineral + base) rather than the bare mineral price.
    "consumer_product_base_price": 40000,
    
    # Retailer inventory policy. (s, Q) thresholds are expressed in
    # weeks of the EWMA of realised consumer demand (see
    # RetailerAgent.demand_ewma); multiplier 4.0 covers ~3 wk of
    # ship lead time plus a week of safety stock.
    "retailer_reorder_point_multiplier": 4.0,
    "retailer_order_quantity_multiplier": 3.0,
    
    # Manufacturer inventory
    "manufacturer_target_inventory_weeks": 4,
    
    # Capacity expansion + reserve replacement
    # Li mine production grew ~30%/yr 2020-24; assume gradual moderation
    # to ~7-8%/yr CAGR over 2024-50 (consistent with IEA NetZero ramp).
    "mine_capacity_growth_per_year": 0.075,
    # Price-responsive accelerated growth: when price stays above
    # extraction_cost x mine_expansion_price_threshold for
    # mine_expansion_trigger_steps consecutive (sticky-net) weeks,
    # per-step capacity growth flips from base to "high". 0.15 reflects
    # the upper end of realistic spike-driven build-out (the 2020-23
    # actuals briefly hit ~30%/yr but a sustained 15% across the
    # portfolio is a more defensible long-run cap).
    "mine_capacity_growth_per_year_high": 0.15,
    "mine_expansion_price_threshold": 2.0,
    "mine_expansion_trigger_steps": 52,
    # Li chemical conversion capacity has been growing in lock-step with
    # mining (China-led brownfield + greenfield builds). Use the same
    # 7.5%/yr blended CAGR so refining keeps pace with mine growth.
    "processor_capacity_growth_per_year": 0.075,
    # Exploration replaces ~70% of extraction (Li reserves grew over the
    # past decade despite production growth).
    "reserve_replacement_rate": 0.70,
    # Restart lag for a mothballed mine: ~6 months to dewater, rehire,
    # recommission for hard-rock; less for brine. Use 26 weeks across.
    "mine_restart_lag_steps": 26,
    # Warm restart -- equipment in place, rehiring within 3 months -- if
    # the mine was mothballed within the last year. Real-world warm
    # restarts are noticeably faster than cold ones (e.g., Mt Cattlin
    # restart was ~10 weeks after a <1y pause).
    "mine_warm_restart_lag_steps": 12,
    "mine_warm_restart_window_steps": 52,
    # Sustained-pressure mothball: mine only mothballs after ~12 months
    # of price below cash cost. Real Li mines almost never full-shutter
    # on a single below-cost week; offtake contracts and care-and-
    # maintenance avoidance keep them running. Even in the 2022-24
    # crash, only marginal Australian operations (Bald Hill, Mt Cattlin)
    # paused, and only after several months of sustained weakness.
    "mothball_trigger_steps": 52,
    # Cash cost = ~65% of extraction (AISC) cost. Mines stay open
    # above cash cost; mothball trigger only counts steps below it.
    "mine_cash_cost_fraction": 0.65,
    # Demand scenario to interpolate against (matches scenario column in
    # demand.csv). Use the IEA NetZero rows by default.
    "demand_scenario": "NetZero",

    # Simulation parameters
    "n_steps": 200,
    "random_seed": 42,
    "steps_per_year": 52,  # Weekly time steps
}
