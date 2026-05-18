"""
Configuration for Cobalt supply chain simulation.

Cobalt is dominated by the DRC (~70 % of mined output) and by China
on the refining side (~70 %). The model treats Co as a standalone
mineral whose substitution path is "less Co-rich NMC chemistry"
(NMC8.1.1 -> NMC5.3.2 -> NMCA -> LFP). The substitution knobs below
are calibrated for that pathway: max_substitution = 0.45 reflects
the ~50 % Co-intensity reduction observed in production EV cathodes
since 2020 (Tesla LFP shift, GM Ultium NMCA, BYD Blade LFP). The
threshold trigger fires at 1.4x initial price -- somewhat tighter
than Li/Ni's 1.5x because automakers have already demonstrated the
willingness and capability to swap Co out aggressively.

Calibration provenance: USGS Mineral Commodity Summaries 2025 +
IEA Critical Minerals Outlook 2024 + Benchmark Mineral Intelligence
+ S&P Global Mobility. Same hand-curated-estimate provenance as the
Li / Ni / Pt configs.
"""

COBALT_CONFIG = {
    # Mineral identification
    "mineral_type": "Cobalt",

    # Fallback annual mineral demand (tonnes/year) used only if the USGS
    # demand column for this mineral is absent. Global mined Co 2024 was
    # ~230 kt with battery sector taking ~70%.
    "default_annual_demand_tons": 230000.0,

    # Price parameters ($/ton). Cobalt has been volatile: the 2018 peak
    # hit ~$95k/t, the 2024 low ~$25k/t. Use ~$30k as a mid-cycle anchor.
    "initial_price": 30000,
    "price_floor": 8000,        # outer catastrophe bound
    "price_ceiling": 250000,    # outer catastrophe bound

    # Cost-anchored price model knobs.
    "price_elasticity": 0.25,
    "price_max_step_pct": 0.10,    # Co has historically swung faster than Ni
    "price_anchor_strength": 0.10,
    "price_ceiling_mc_multiple": 8.0,
    "price_floor_cost_fraction": 0.6,

    # Manufacturer mineral intensity (tons Co per EV). NMC811 packs
    # ~5-8 kg/EV; NMC532 ~12 kg; LFP ~0 kg. Use 0.012 t/EV as a portfolio
    # average reflecting the gradual NMC->LFP shift through the period.
    "manufacturer_mineral_intensity": 0.012,

    # Per-mode transport unit costs ($/ton; diagnostics only).
    "transport_cost_ship": 14,
    "transport_cost_rail": 30,
    "transport_cost_truck": 60,

    # Recycling collection rate. Co-bearing batteries are highly
    # valuable for recycling (Co is the highest $/kg cathode mineral),
    # so collection is materially better than Li (which has low
    # per-kg value). Calibration: Umicore + Glencore + Li-Cycle +
    # GEM together claim ~50% effective collection on Co-bearing
    # battery scrap; the rest goes to landfill or unprocessed
    # accumulation.
    "collection_rate": 0.50,
    "product_lifetime_steps": 520,    # ~10 years EV battery

    # Market parameters
    "geopolitical_event_probability": 0.01,
    # Probability that a geopolitical event hits the refining tier
    # rather than the mining tier. Co refining is even more
    # concentrated than Ni (China-dominant), so a high processor
    # share is defensible; keep at 0.35.
    "geopolitical_processor_event_share": 0.35,
    "mine_disruption_probability": 0.02,
    "disruption_duration_min": 3,
    "disruption_duration_max": 6,
    "geopolitical_duration_min": 5,
    "geopolitical_duration_max": 20,    # DRC political shocks tend to run long

    # Manufacturer substitution: NMC -> low-Co NMC / NCMA / LFP shift.
    # Threshold tighter than Li/Ni (1.4x not 1.5x) because the
    # substitution path is already industry-proven.
    "substitution_price_threshold": 42000,           # 140% of initial $30k
    "substitution_revert_threshold": 20000,          # 66.7% of initial
    "substitution_trigger_steps": 8,                 # automakers react faster on Co
    "substitution_revert_trigger_steps": 26,
    "substitution_rate": 0.06,
    "substitution_revert_rate": 0.025,                # reverting to Co-rich NMC is slow
    "max_substitution": 0.45,                         # Co is the most substitutable cathode mineral

    # Consumer behavior. Co price is a smaller share of finished-EV
    # price than Li (intensity-times-price gives ~$360 vs Li's ~$135;
    # but EV BOM is ~$10k for the pack so Co is ~3-4%). Use a slightly
    # less elastic response than Li.
    "consumer_price_sensitivity": -0.6,
    "consumer_demand_threshold_multiplier": 2.0,
    # Non-mineral component of the finished-product price ($/unit).
    "consumer_product_base_price": 40000,

    # Retailer inventory policy.
    "retailer_reorder_point_multiplier": 4.0,
    "retailer_order_quantity_multiplier": 3.0,

    # Manufacturer inventory
    "manufacturer_target_inventory_weeks": 4,

    # Capacity expansion + reserve replacement
    # DRC capacity has grown ~6%/yr through 2020-24; Indonesian
    # HPAL byproduct Co is ramping ~12%/yr. Blended ~5%.
    "mine_capacity_growth_per_year": 0.05,
    # Under sustained high prices, Indonesian HPAL byproduct and
    # DRC artisanal output can ramp ~15%/yr.
    "mine_capacity_growth_per_year_high": 0.15,
    "mine_expansion_price_threshold": 2.0,
    "mine_expansion_trigger_steps": 52,
    # Refining capacity tracks China + Finland builds; ~5% blended.
    "processor_capacity_growth_per_year": 0.05,
    # Co exploration is mature on the DRC copperbelt; modest replacement.
    "reserve_replacement_rate": 0.50,
    # Restart lag: DRC artisanal can swing fast; industrial (Tenke,
    # Mutanda) takes longer.
    "mine_restart_lag_steps": 26,
    "mine_warm_restart_lag_steps": 12,
    "mine_warm_restart_window_steps": 52,
    # Sustained-pressure mothball. Glencore Mutanda mothballed in
    # 2019 after ~12 months of sub-cost prices.
    "mothball_trigger_steps": 52,
    # Cash cost ~60% of all-in cost for DRC industrial; higher for
    # Indonesian byproduct (where the byproduct is essentially free).
    "mine_cash_cost_fraction": 0.60,
    "demand_scenario": "NetZero",

    # Per-country agent fan-out (see lithium_config.py for full notes).
    "agents_per_gdp_billion": 500.0,

    # Simulation parameters
    "n_steps": 200,
    "random_seed": 42,
    "steps_per_year": 52,

    # Per-country heuristic overrides; see lithium_config.py for the
    # full set of supported keys. Empty by default.
    "country_overrides": {},
}
