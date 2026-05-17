"""Per-country configuration overrides.

A small helper layered on top of the existing ``model.config`` dict so
agents can read their heuristic parameters with an optional country-
specific override:

    self._cfg_mothball_trigger_steps = int(
        cfg_for(model, self.country, "mothball_trigger_steps", 13)
    )

If ``model.config["country_overrides"][country][key]`` exists it wins;
otherwise the lookup falls back to the global key, and finally to the
``default`` supplied by the caller. With an empty ``country_overrides``
dict every agent sees exactly the same values it would have read before
this helper existed -- the change is backwards-compatible.

This module also exposes ``procurement_avoid_list`` which combines the
static ``procurement_avoid_countries`` list with any currently active
embargo (when ``procurement_avoid_embargoed`` is enabled) so US buyers
can be configured to refuse purchases from embargoed origins.
"""

from __future__ import annotations

# Keys inside a per-country override dict that are NOT scalar agent
# parameters and should be skipped by the typo-catcher validator.
_NON_SCALAR_OVERRIDE_KEYS = {
    "strategic_reserve",
    "procurement_avoid_countries",
    "procurement_avoid_embargoed",
}


def cfg_for(model, country, key, default):
    """Resolve a config key for ``country``, falling back to global.

    Lookup order:
        1. ``model.config["country_overrides"][country][key]``
        2. ``model.config[key]``
        3. ``default``

    ``country`` may be None (e.g. for tier-wide singletons that don't
    belong to a country); the global lookup is used in that case.
    """
    if country is not None:
        overrides = model.config.get("country_overrides", {})
        country_cfg = overrides.get(country)
        if country_cfg and key in country_cfg:
            return country_cfg[key]
    return model.config.get(key, default)


def price_for(model, country):
    """Regional mineral price seen by agents in ``country``.

    Returns ``model.current_price * (1 + price_spread)`` where
    ``price_spread`` comes from
    ``model.config["country_overrides"][country]["price_spread"]``
    (default 0.0 -- no wedge, identical to the global anchor).

    The global ``current_price`` stays the single anchor that the
    market mechanism updates each step from globally-summed supply and
    demand. Spreads are static config-level multiplicative offsets
    layered on top of that anchor; they model permanent regional
    policy wedges (US IRA, EU CRMA, China's domestic premium, etc.)
    that decouple regional Li prices from a uniform global spot.

    Agents call this when making price-sensitive decisions (mine
    utilization, manufacturer substitution, consumer demand, recycler
    sell trigger, strategic reserve buy/release). The aggregated
    supply / demand signals that feed the global price update already
    reflect regional behaviour because each agent's decision keys off
    its own ``price_for(country)`` before contributing to the global
    aggregate.
    """
    if country is None:
        return model.current_price
    overrides = model.config.get("country_overrides", {})
    country_cfg = overrides.get(country)
    if not country_cfg:
        return model.current_price
    spread = float(country_cfg.get("price_spread", 0.0))
    if spread == 0.0:
        return model.current_price
    return model.current_price * (1.0 + spread)


def procurement_avoid_list(model, country):
    """Countries this ``country``'s agents should refuse to buy from.

    Combines the static ``procurement_avoid_countries`` list in the
    country's override block with any currently active embargo when
    ``procurement_avoid_embargoed`` is truthy. The returned set is
    consulted per-step (cheap: a small set membership check) by
    procurement loops in ProcessorAgent / ManufacturerAgent /
    RetailerAgent.

    Returns an empty set when the country has no override or the
    relevant fields are absent -- so the default behaviour (no
    procurement-side filtering) is preserved.
    """
    if country is None:
        return frozenset()
    overrides = model.config.get("country_overrides", {})
    country_cfg = overrides.get(country)
    if not country_cfg:
        return frozenset()
    avoid = set(country_cfg.get("procurement_avoid_countries", []))
    if country_cfg.get("procurement_avoid_embargoed", False):
        avoid.update(model.active_embargoes.keys())
    return frozenset(avoid)


def validate_country_overrides(config, recognised_keys, on_warn=print):
    """Warn about leaf keys in country_overrides that aren't recognised.

    ``recognised_keys`` is the set of scalar config keys an agent might
    read via ``cfg_for``. Catches typos like ``mothbal_trigger_steps``
    before they silently no-op. Nested-dict keys (e.g. strategic_reserve)
    and procurement-policy keys are allowed and skipped.

    Called once at model construction; emits one warning per unknown
    key per country via ``on_warn`` (default: print).
    """
    overrides = config.get("country_overrides", {})
    if not overrides:
        return
    for country, country_cfg in overrides.items():
        if not isinstance(country_cfg, dict):
            on_warn(
                f"country_overrides[{country!r}] is not a dict; ignored."
            )
            continue
        for key in country_cfg:
            if key in _NON_SCALAR_OVERRIDE_KEYS:
                continue
            # Underscore-prefixed keys are reserved for inline _comment
            # fields in policy JSON files; skip them silently.
            if key.startswith("_"):
                continue
            if key not in recognised_keys:
                on_warn(
                    f"country_overrides[{country!r}][{key!r}] is not a "
                    f"recognised config key; the override will be ignored."
                )


# Set of every scalar config key the agents read through cfg_for. Kept
# here so validate_country_overrides can be called from the model
# without each agent having to register itself. Maintenance contract:
# any new cfg_for(...) lookup added to an agent should add the key
# here too.
RECOGNISED_OVERRIDE_KEYS = frozenset({
    # MineAgent
    "steps_per_year",
    "mine_capacity_growth_per_year",
    "mine_capacity_growth_per_year_high",
    "mine_expansion_price_threshold",
    "mine_expansion_trigger_steps",
    "mine_disruption_probability",
    "disruption_duration_min",
    "disruption_duration_max",
    "mine_restart_margin",
    "mine_restart_lag_steps",
    "mine_warm_restart_lag_steps",
    "mine_warm_restart_window_steps",
    "mothball_trigger_steps",
    "mine_cash_cost_fraction",
    "reserve_replacement_rate",
    "post_embargo_release_steps",
    "mine_baseline_utilization",
    "mine_min_utilization",
    "mine_max_utilization",
    "mine_max_utilization_ratio",
    # ProcessorAgent
    "processor_safety_stock_weeks",
    "processor_inventory_cap_weeks",
    "processor_capacity_growth_per_year",
    # ManufacturerAgent
    "manufacturer_target_inventory_weeks",
    "substitution_price_threshold",
    "substitution_revert_threshold",
    "substitution_trigger_steps",
    "substitution_revert_trigger_steps",
    "substitution_rate",
    "substitution_revert_rate",
    "max_substitution",
    "manufacturer_order_rate",
    # RetailerAgent
    "retailer_reorder_point_multiplier",
    "retailer_order_quantity_multiplier",
    "retailer_max_pending_orders",
    "retailer_demand_ewma_alpha",
    # ConsumerAgent
    "consumer_demand_threshold_multiplier",
    "consumer_price_sensitivity",
    # RecyclingAgent
    "recycling_capacity_ramp_per_year",
    # TransportAgent
    "transport_max_deferral_steps",
    # Regional price wedge (Addendum 3). Used by price_for() to scale
    # the global current_price into a country-specific price seen by
    # that country's agents. Multiplier form: 0.50 = +50% premium.
    "price_spread",
})
