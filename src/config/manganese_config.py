"""
Configuration for Manganese supply chain simulation.

This config models the battery-grade Manganese supply chain
(high-purity manganese sulphate monohydrate, HPMSM, plus electrolytic
manganese metal feeding battery-grade producers). The far larger
ferro-manganese / steel-grade Mn market is OUT OF SCOPE -- the
consumer-side share CSV reflects battery / EV demand only, mirroring
how Li is modeled despite having other uses.

Mn is the lowest-cost cathode mineral and the only one that gets
substituted INTO rather than out of (LMFP, LMO, NMC9.5.5 / NMC9.0.5.5
chemistries all increase Mn content relative to Co/Ni). The
max_substitution = 0.25 reflects that "substitute away from Mn" is
the unusual direction -- if anything, automakers want more Mn-rich
cathodes, not less.

Calibration provenance: USGS Mineral Commodity Summaries 2025 +
IEA Critical Minerals Outlook 2024 + Benchmark Mineral Intelligence
+ Euro Manganese / Element 25 / Manganese X investor disclosures.
"""

MANGANESE_CONFIG = {
    # Mineral identification
    "mineral_type": "Manganese",

    # Fallback annual mineral demand (tonnes/year). HPMSM market was
    # ~80 kt Mn-content in 2024; total Mn (steel-grade dominant) is
    # ~20 Mt globally. We model the battery-grade slice so 80 kt is
    # the right scale.
    "default_annual_demand_tons": 80000.0,

    # Price parameters ($/ton, HPMSM equivalent contained Mn).
    # Battery-grade HPMSM has been $4-7k/t through 2022-24; well above
    # the ~$1.5k/t for electrolytic Mn metal or ferro-Mn.
    "initial_price": 5000,
    # Battery-grade HPMSM price floor; well above steel-grade ferro-Mn
    # (~$1500/t). With ~$1800-2200 mine + ~$1700-2200 processor cost
    # the all-in marginal is ~$3500-4400, so a $3000 absolute floor sits
    # comfortably below realistic cost-curve bottom.
    "price_floor": 3000,
    "price_ceiling": 30000,     # outer catastrophe bound

    # Cost-anchored price model knobs.
    "price_elasticity": 0.20,    # Mn-grade premium is sticky
    "price_max_step_pct": 0.06,
    "price_anchor_strength": 0.10,
    "price_ceiling_mc_multiple": 8.0,
    "price_floor_cost_fraction": 0.6,

    # Manufacturer mineral intensity (tons Mn per EV). NMC532 packs
    # ~15-18 kg Mn; LMFP and NMC9.5.5 push higher. Use 0.018 t/EV.
    "manufacturer_mineral_intensity": 0.018,

    # Per-mode transport unit costs ($/ton; diagnostics only).
    "transport_cost_ship": 10,
    "transport_cost_rail": 25,
    "transport_cost_truck": 50,

    # Recycling collection rate. Mn is the lowest-value cathode mineral
    # so dedicated Mn recycling is nascent; most Mn in EOL batteries
    # ends up in slag during pyrometallurgical recovery of Ni/Co.
    "collection_rate": 0.10,
    "product_lifetime_steps": 520,

    # Market parameters
    "geopolitical_event_probability": 0.01,
    # Battery-grade Mn refining concentrated in China (~90%); high
    # processor event share. South African and Australian mining
    # are reasonably diversified.
    "geopolitical_processor_event_share": 0.40,
    "mine_disruption_probability": 0.02,
    "disruption_duration_min": 3,
    "disruption_duration_max": 5,
    "geopolitical_duration_min": 5,
    "geopolitical_duration_max": 15,

    # Manufacturer substitution. Since Mn is the lowest-cost cathode
    # mineral, automakers don't substitute OUT of Mn under price stress
    # -- they substitute IN to Mn (away from Co/Ni). The threshold
    # below fires only on extreme Mn price spikes.
    "substitution_price_threshold": 8000,            # 160% of initial $5k
    "substitution_revert_threshold": 3300,           # 66% of initial
    "substitution_trigger_steps": 13,                # slower to fire (Mn rarely the binding constraint)
    "substitution_revert_trigger_steps": 26,
    "substitution_rate": 0.03,
    "substitution_revert_rate": 0.04,                 # but quick to revert
    "max_substitution": 0.25,                         # bounded; nobody designs Mn-free EVs

    # Consumer behavior. Mn is a small share of EV BOM (~$90 at
    # 0.018 t * $5k/t per EV), so consumer elasticity to Mn-price
    # is low.
    "consumer_price_sensitivity": -0.4,
    "consumer_demand_threshold_multiplier": 2.5,      # higher than Li/Ni - Mn rarely binds
    "consumer_product_base_price": 40000,

    # Retailer inventory policy. Battery-grade Mn supply chain is
    # newer and thinner; retailers carry a slightly fatter buffer.
    "retailer_reorder_point_multiplier": 5.0,
    "retailer_order_quantity_multiplier": 4.0,

    # Manufacturer inventory
    "manufacturer_target_inventory_weeks": 5,

    # Capacity expansion + reserve replacement
    # HPMSM capacity has been growing ~25%/yr off a small base
    # through 2022-24 (China + Euro Manganese + Element 25). Mature
    # mining grows ~3%/yr.
    "mine_capacity_growth_per_year": 0.03,
    # Sustained high prices accelerate HPMSM buildout dramatically;
    # 20%/yr is achievable from new green-field plants.
    "mine_capacity_growth_per_year_high": 0.20,
    "mine_expansion_price_threshold": 2.0,
    "mine_expansion_trigger_steps": 52,
    "processor_capacity_growth_per_year": 0.08,       # HPMSM faster than mining
    # Mn reserves are abundant; replacement is essentially trivial.
    "reserve_replacement_rate": 0.80,
    "mine_restart_lag_steps": 26,
    "mine_warm_restart_lag_steps": 12,
    "mine_warm_restart_window_steps": 52,
    "mothball_trigger_steps": 52,
    "mine_cash_cost_fraction": 0.65,
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
