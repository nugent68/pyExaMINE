"""
MineAgent: Extracts raw minerals from reserves.
Behavior: Produces if profitable, subject to disruptions and embargoes.
"""

from mesa import Agent

from ..config.overrides import cfg_for, price_for


class MineAgent(Agent):
    """Agent representing a mine that extracts raw minerals."""

    def __init__(self, unique_id, model, jurisdiction, facility,
                 production_capacity, extraction_cost, reserves):
        """Initialize a MineAgent.

        Args:
            unique_id: Unique identifier
            model: Model instance
            jurisdiction: Country/region name (also exposed as .country)
            facility: Facility / mine name (e.g. 'Greenbushes')
            production_capacity: Maximum tons/step (contained mineral)
            extraction_cost: Cost per ton to extract ($/ton)
            reserves: Total remaining reserves (tons)
        """
        super().__init__(unique_id, model)

        # Core attributes
        self.jurisdiction = jurisdiction
        self.country = jurisdiction
        self.facility = facility
        self.label = f"{jurisdiction}/{facility}"
        self.production_capacity = production_capacity
        self.extraction_cost = extraction_cost
        self.reserves = reserves
        self.initial_reserves = reserves

        # Operational state. Two independent state machines:
        #   disruption_counter: >0 means temporarily shut down by a random
        #     incident or geopolitical event; counts down per step and
        #     auto-recovers at zero.
        #   mothballed: True means the mine has voluntarily shut down
        #     because the price fell below cash cost for a sustained
        #     window (see low_price_counter). It restarts when price
        #     recovers above extraction_cost * restart_margin.
        self.disruption_counter = 0
        self.mothballed = False
        # Restart lag: real mines can't simply re-open the moment price
        # crosses the restart threshold. Rehiring, dewatering, equipment
        # recommissioning take months to a year. While restart_counter
        # > 0 the mine is actively restarting (no production yet);
        # extraction resumes once it ticks down to zero. Aborts back to
        # 0 if price drops below the restart threshold mid-restart.
        self.restart_counter = 0

        # Sustained-pressure mothball logic. Real mines very rarely
        # full-shutter on a single below-cost step -- offtake contracts,
        # care-and-maintenance avoidance, and integrated operations keep
        # them running at reduced rates until price has been below the
        # *cash* cost (typically ~65% of all-in extraction cost) for
        # several months. ``low_price_counter`` accumulates consecutive
        # below-cash-cost steps and decrements on recovery; mothball
        # fires only when it crosses ``mothball_trigger_steps``.
        self.low_price_counter = 0
        # Step at which this mine was last mothballed. Used to pick a
        # warm restart lag (~3 months; equipment + crews still around)
        # vs the cold default (~6 months) for mines that have been
        # offline for over a year. Initialized very-negative so the
        # first restart is always a cold one.
        self.last_mothball_step = -10**9

        # Production tracking. Three views per step:
        #   extracted_this_step: tonnes newly extracted from reserves this
        #     step (set in _produce; 0 if mothballed/disrupted/no reserves).
        #     This is the *true* per-step new production; sums correctly
        #     across steps (no pithead double-counting).
        #   available_production_this_step: gross output OFFERED to the
        #     international market this step (= pithead carry-over + new
        #     extraction + post-embargo release chunk). NOT decremented as
        #     processors buy. Used for inspection / debugging only -- do
        #     NOT cumulate this across steps; it double-counts pithead
        #     carry-over.
        #   production_this_step: same starting value as available_*, but
        #     decremented by processor purchases as the step progresses;
        #     used by the processor-purchase loop to discover what is
        #     still for sale.
        # pithead_stockpile holds extracted-but-unsold mineral. Real mines
        # stockpile at site when the buyer doesn't take the full lift; the
        # material isn't lost the way it would be if production_this_step
        # were simply reset to zero each step. The stockpile is offered
        # back into production_this_step at the start of the next step.
        # While the country is embargoed, the pithead does NOT flow out
        # (the embargo would also bottle up the stockpile).
        self.production_this_step = 0
        self.available_production_this_step = 0
        self.extracted_this_step = 0.0
        self.cumulative_production = 0
        self.embargoed_production_this_step = 0
        self.domestic_stockpile = 0
        self.pithead_stockpile = 0.0
        # Constant chunk size used by ``_release_stockpile`` for the
        # linear post-embargo bleed. Set when an embargo lifts (= the
        # initial stockpile divided by ``post_embargo_release_steps``)
        # and reset to 0 when the stockpile is fully drained, so a
        # subsequent embargo cycle starts a fresh release.
        self._release_chunk_size = 0.0

        # Price-responsive capacity expansion. Sticky counter that
        # mirrors the substitution/mothball pattern: increments while
        # price > extraction_cost * mine_expansion_price_threshold,
        # decrements otherwise. Once it crosses
        # mine_expansion_trigger_steps, per-step capacity growth
        # switches from the baseline rate to the "high" rate (matches
        # the real-world capex response to sustained spikes that the
        # static mine_capacity_growth_per_year knob misses -- e.g. Li
        # capacity grew ~30%/yr in 2020-23 spike, vs the 7.5%/yr
        # blended baseline). Per-mine counter so the cheapest mines
        # cross the threshold first and start expanding earliest --
        # which is the correct merit-order behaviour.
        self.high_price_capex_counter = 0

        # Cache config values referenced from step() / helpers. Config is
        # immutable post-construction so reading once per agent at init
        # saves a per-step dict.get for each of these knobs across every
        # mine. Names mirror the original config keys for clarity.
        # cfg_for consults country_overrides[self.country] first so US-
        # specific policy values land on US mines only.
        c = self.country
        self._cfg_steps_per_year = int(cfg_for(model, c, "steps_per_year", 52))
        self._cfg_cap_growth_base = float(
            cfg_for(model, c, "mine_capacity_growth_per_year", 0.0)
        )
        self._cfg_cap_growth_high = float(
            cfg_for(model, c, "mine_capacity_growth_per_year_high",
                    self._cfg_cap_growth_base)
        )
        self._cfg_expansion_threshold_mult = float(
            cfg_for(model, c, "mine_expansion_price_threshold", 2.0)
        )
        self._cfg_expansion_trigger_steps = int(
            cfg_for(model, c, "mine_expansion_trigger_steps", 52)
        )
        self._cfg_disruption_probability = float(
            cfg_for(model, c, "mine_disruption_probability", 0.02)
        )
        self._cfg_disruption_duration_min = int(
            cfg_for(model, c, "disruption_duration_min", 3)
        )
        self._cfg_disruption_duration_max = int(
            cfg_for(model, c, "disruption_duration_max", 5)
        )
        self._cfg_restart_margin = float(cfg_for(model, c, "mine_restart_margin", 1.2))
        self._cfg_cold_restart_lag = int(cfg_for(model, c, "mine_restart_lag_steps", 26))
        self._cfg_warm_restart_lag = int(
            cfg_for(model, c, "mine_warm_restart_lag_steps", 12)
        )
        self._cfg_warm_window = int(
            cfg_for(model, c, "mine_warm_restart_window_steps", 52)
        )
        self._cfg_mothball_trigger_steps = int(
            cfg_for(model, c, "mothball_trigger_steps", 13)
        )
        self._cfg_cash_cost_fraction = float(
            cfg_for(model, c, "mine_cash_cost_fraction", 0.65)
        )
        self._cfg_reserve_replacement_rate = float(
            cfg_for(model, c, "reserve_replacement_rate", 0.0)
        )
        self._cfg_post_embargo_release_steps = max(
            1, int(cfg_for(model, c, "post_embargo_release_steps", 26))
        )
        # Utilization curve knobs.
        self._cfg_util_baseline = float(
            cfg_for(model, c, "mine_baseline_utilization", 0.75)
        )
        self._cfg_util_min = float(cfg_for(model, c, "mine_min_utilization", 0.5))
        self._cfg_util_max = float(cfg_for(model, c, "mine_max_utilization", 1.0))
        self._cfg_util_ratio_max = float(
            cfg_for(model, c, "mine_max_utilization_ratio", 2.5)
        )

    @property
    def operational(self):
        """True iff the mine is currently producing."""
        return self.disruption_counter == 0 and not self.mothballed

    def step(self):
        """Execute one time step of mine behavior."""
        # 0. Capacity expansion. Only operational mines (not mothballed,
        #    not currently disrupted) sink capex into expansion -- a
        #    care-and-maintenance shaft doesn't grow nameplate. Reserves
        #    must also be > 0 (no point expanding a depleted asset).
        #    Reserve replacement is now applied *inside* _produce, scaled
        #    to actual extraction this step -- previously it ran every
        #    step regardless of operational status, so a mothballed mine
        #    was silently gifted exploration reserves out of nothing.
        #
        #    Growth rate is price-responsive: sustained price >
        #    extraction_cost * mine_expansion_price_threshold for
        #    mine_expansion_trigger_steps consecutive (sticky-net) steps
        #    flips the per-step rate from baseline to "high". Reverts to
        #    baseline when the counter relaxes; already-built capacity
        #    stays (you don't unbuild a shaft).
        # Regional price: in a config with no price_spread override
        # this is the global current_price (backwards-compat). With a
        # country override it's the multiplier-adjusted regional price
        # the mine actually receives for its ore.
        price = price_for(self.model, self.country)
        if (self.extraction_cost > 0
                and price > self.extraction_cost * self._cfg_expansion_threshold_mult):
            self.high_price_capex_counter += 1
        else:
            self.high_price_capex_counter = max(
                0, self.high_price_capex_counter - 1,
            )

        cap_growth_yr = (
            self._cfg_cap_growth_high
            if self.high_price_capex_counter >= self._cfg_expansion_trigger_steps
            else self._cfg_cap_growth_base
        )

        if cap_growth_yr > 0 and self.operational and self.reserves > 0:
            self.production_capacity *= (1.0 + cap_growth_yr / self._cfg_steps_per_year)

        # 1. Carry forward any unsold production from last step into the
        #    pithead stockpile before resetting per-step counters. This
        #    keeps mineral mass conserved when processors don't lift the
        #    full available production (e.g. inventory backpressure).
        if self.production_this_step > 0:
            self.pithead_stockpile += self.production_this_step

        self.production_this_step = 0
        self.available_production_this_step = 0
        self.extracted_this_step = 0.0
        self.embargoed_production_this_step = 0

        # 2. Offer pithead stockpile to the international market unless
        #    the country is currently embargoed (in which case the
        #    stockpile waits in place along with new domestic output).
        if self.pithead_stockpile > 0 and not self.model.is_embargoed(self.jurisdiction):
            self.production_this_step = self.pithead_stockpile
            self.available_production_this_step = self.pithead_stockpile
            self.pithead_stockpile = 0.0

        # 3. Drain any post-embargo stockpile onto the market each step,
        #    regardless of disruption / mothball status (the material is
        #    already extracted and sitting in a warehouse). Skipped while
        #    the country is still embargoed. Whatever isn't bought this
        #    step rolls into pithead via the carry-forward in step 1 of
        #    the next step.
        if self.domestic_stockpile > 0 and not self.model.is_embargoed(self.jurisdiction):
            self._release_stockpile()

        # Tick down any active disruption. A disruption blocks new
        # production this step regardless of profitability.
        if self.disruption_counter > 0:
            self.disruption_counter -= 1
            return

        # Random disruption check (uses the model's seeded RNG so runs
        # are reproducible from the seed).
        rng = self.model.random_state
        if rng.random() < self._cfg_disruption_probability:
            self._trigger_disruption()
            return

        # Mothball / restart logic. "Shut down for low price" is a state
        # separate from disruption so the mine actually reopens when the
        # price recovers.
        #
        # Trigger:
        #   - Cash cost = extraction_cost * mine_cash_cost_fraction
        #     (default 0.65; real cash costs are typically 50-70% of
        #     AISC). Real mines stay open above cash cost via offtake
        #     contracts and care-and-maintenance avoidance.
        #   - Mothball fires only after low_price_counter >=
        #     mothball_trigger_steps consecutive (net) below-cash-cost
        #     steps (default 13 ~ 1 quarter). A single bad week
        #     decrements again on recovery, so transient dips don't
        #     accumulate.
        #
        # Restart:
        #   - Same threshold as before: price > extraction_cost *
        #     restart_margin (default 1.2x).
        #   - Lag is ``warm_restart_lag_steps`` (default 12 weeks ~ 3
        #     months) if the mine was mothballed within the last
        #     ``warm_restart_window_steps`` (default 52); otherwise
        #     ``mine_restart_lag_steps`` (default 26 ~ 6 months).
        #     Real-world warm restarts are faster because crews and
        #     equipment are still in place; cold restarts require
        #     rehiring + recommissioning.
        #   - Restart aborts if price falls back below the threshold
        #     during the lag.
        cash_cost = self.extraction_cost * self._cfg_cash_cost_fraction
        price = price_for(self.model, self.country)

        if self.mothballed:
            if price > self.extraction_cost * self._cfg_restart_margin:
                if self.restart_counter <= 0:
                    steps_since = self.model.current_step - self.last_mothball_step
                    effective_lag = (
                        self._cfg_warm_restart_lag if steps_since <= self._cfg_warm_window
                        else self._cfg_cold_restart_lag
                    )
                    self.restart_counter = max(1, effective_lag)
                self.restart_counter -= 1
                if self.restart_counter <= 0:
                    self.mothballed = False
                    self.low_price_counter = 0
                else:
                    return
            else:
                # Price slipped back below the trigger -- abort restart.
                self.restart_counter = 0
                return
        else:
            if price < cash_cost:
                self.low_price_counter += 1
                if self.low_price_counter >= self._cfg_mothball_trigger_steps:
                    self.mothballed = True
                    self.last_mothball_step = self.model.current_step
                    self.restart_counter = 0
                    self.low_price_counter = 0
                    return
            else:
                # Net-decrement on recovery: a quarter of "fine, fine,
                # bad, fine, fine" doesn't slowly accumulate to a
                # mothball. Sustained pressure is what matters.
                self.low_price_counter = max(0, self.low_price_counter - 1)

        # Produce minerals if reserves remain.
        if self.reserves > 0:
            self._produce()

    def _produce(self):
        """Produce minerals for this step.

        USGS production figures are reported as contained-mineral output, so
        production_capacity is already in tonnes of mineral. Refining yield
        loss is modeled separately on the processor side via conversion_efficiency.
        ore_grade is retained as metadata for cost/reporting only.

        Output flexes with price via _utilization_factor: at the
        anchored "normal" price multiple of extraction cost, the mine
        produces production_capacity (the USGS-reported baseline). Above
        that it can ramp up to ~max/baseline of nameplate; below it it
        ramps down toward min/baseline, with hard mothball below cost
        handled in step().

        If this mine's jurisdiction is under a political embargo, the
        produced material is routed to a domestic_stockpile rather than
        being made available on the international market. Reserves are
        debited either way (the country still extracted the ore).
        """
        factor = self._utilization_factor()
        output = min(self.production_capacity * factor, self.reserves)

        self.reserves -= output
        self.cumulative_production += output
        self.extracted_this_step = output

        # Reserve replacement scales with *actual* this-step extraction
        # (was: gross production_capacity each step, regardless of
        # operational status). Exploration spend tracks production --
        # a mine that doesn't produce this step doesn't grow reserves.
        # The added tonnage is also booked on the model's
        # cumulative_reserve_replacement counter so the conservation
        # diagnostic accounts for the new mineral being introduced.
        if self._cfg_reserve_replacement_rate > 0 and output > 0:
            replaced = output * self._cfg_reserve_replacement_rate
            self.reserves += replaced
            self.model.cumulative_reserve_replacement += replaced

        if self.model.is_embargoed(self.jurisdiction):
            self.domestic_stockpile += output
            self.embargoed_production_this_step = output
        else:
            # `+=` (not `=`) so this step's pithead carry-forward and any
            # post-embargo stockpile release are preserved. Assignment
            # here silently zeroed those flows -- breaking mineral mass
            # conservation whenever a mine had inventory waiting and
            # also produced new material the same step.
            self.production_this_step += output
            self.available_production_this_step += output

    def _utilization_factor(self):
        """Return a multiplier on production_capacity based on price.

        production_capacity is treated as the *baseline-utilization*
        output (matching USGS-reported actual production). Real mines
        run between ~50% and ~100% of nameplate depending on margin.
        We linearly interpolate utilization between umin and umax over
        the price/extraction-cost ratio range [1.0, ratio_max], then
        rescale by 1/baseline so that at the anchored "normal" price
        the factor lands at 1.0 and aggregate output matches the
        original USGS figure.
        """
        if self.extraction_cost <= 0:
            # Pathological config -- pin to baseline so we don't divide by 0.
            return 1.0

        ratio = price_for(self.model, self.country) / self.extraction_cost
        umin = self._cfg_util_min
        umax = self._cfg_util_max
        ratio_max = self._cfg_util_ratio_max

        if ratio <= 1.0:
            utilization = umin
        elif ratio >= ratio_max:
            utilization = umax
        else:
            utilization = umin + (umax - umin) * (ratio - 1.0) / (ratio_max - 1.0)

        baseline = self._cfg_util_baseline
        return utilization / baseline if baseline > 0 else 1.0

    def _release_stockpile(self):
        """Release the post-embargo stockpile linearly onto the market.

        Bled out over ``post_embargo_release_steps`` steps (default 26 ~
        6 months) rather than dumped at once, so a long embargo doesn't
        translate into a single-step supply shock when it lifts.

        ``_release_chunk_size`` is frozen the first step a release runs
        (i.e. embargo just lifted) at ``initial_stockpile /
        release_steps``, so each subsequent step releases the same
        absolute tonnage and the stockpile reaches zero in exactly
        ``release_steps`` steps. The previous version recomputed
        ``stockpile / release_steps`` each step, producing geometric
        decay (~63% drained in ``release_steps`` steps; the dead
        "drain remainder" branch never triggered).
        """
        if self._release_chunk_size <= 0:
            self._release_chunk_size = (
                self.domestic_stockpile / self._cfg_post_embargo_release_steps
            )
        chunk = min(self._release_chunk_size, self.domestic_stockpile)
        self.domestic_stockpile -= chunk
        self.production_this_step += chunk
        self.available_production_this_step += chunk
        # Once the stockpile is essentially empty, reset so the next
        # embargo-then-lift cycle freezes a fresh chunk size.
        if self.domestic_stockpile <= 1e-9:
            self.domestic_stockpile = 0.0
            self._release_chunk_size = 0.0

    def _trigger_disruption(self):
        """Trigger a random disruption event.

        Uses ``max(...)`` so a routine 3-5 step random incident can't
        shorten an active longer geopolitical disruption (which is set
        via ``apply_geopolitical_disruption`` and is typically 5-15
        steps). Without this, a random roll during a major embargo
        could quietly halve the modeled outage window.
        """
        rng = self.model.random_state
        self.disruption_counter = max(
            self.disruption_counter,
            rng.randint(self._cfg_disruption_duration_min,
                        self._cfg_disruption_duration_max),
        )

    def apply_geopolitical_disruption(self, duration):
        """Apply a geopolitical disruption to this mine.

        Args:
            duration: Number of steps to remain disrupted
        """
        self.disruption_counter = max(self.disruption_counter, duration)

    def get_available_supply(self):
        """Get the amount available to sell this step."""
        return self.production_this_step

    def sell_production(self, amount):
        """Sell a portion of this step's production.

        Args:
            amount: Amount to sell (tons)

        Returns:
            Actual amount sold
        """
        sold = min(amount, self.production_this_step)
        self.production_this_step -= sold
        return sold
