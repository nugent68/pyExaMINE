"""
MineAgent: Extracts raw minerals from reserves.
Behavior: Produces if profitable, subject to disruptions and embargoes.
"""

from mesa import Agent


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
        #     because the price fell below extraction cost. It restarts
        #     when price recovers above extraction_cost * restart_margin.
        self.disruption_counter = 0
        self.mothballed = False
        # Restart lag: real mines can't simply re-open the moment price
        # crosses the restart threshold. Rehiring, dewatering, equipment
        # recommissioning take months to a year. While restart_counter
        # > 0 the mine is actively restarting (no production yet);
        # extraction resumes once it ticks down to zero. Aborts back to
        # 0 if price drops below the restart threshold mid-restart.
        self.restart_counter = 0

        # Production tracking. Two views per step:
        #   available_production_this_step: gross output offered to the
        #     international market this step. Set once in _produce (when
        #     not embargoed) and never decremented; used by the price
        #     signal and the gross-production data series.
        #   production_this_step: same value initially, but decremented by
        #     processor purchases as the step progresses; used by the
        #     processor-purchase loop to discover what is still for sale.
        # pithead_stockpile holds extracted-but-unsold mineral. Real mines
        # stockpile at site when the buyer doesn't take the full lift; the
        # material isn't lost the way it would be if production_this_step
        # were simply reset to zero each step. The stockpile is offered
        # back into production_this_step at the start of the next step.
        # While the country is embargoed, the pithead does NOT flow out
        # (the embargo would also bottle up the stockpile).
        self.production_this_step = 0
        self.available_production_this_step = 0
        self.cumulative_production = 0
        self.embargoed_production_this_step = 0
        self.domestic_stockpile = 0
        self.pithead_stockpile = 0.0

    @property
    def operational(self):
        """True iff the mine is currently producing."""
        return self.disruption_counter == 0 and not self.mothballed

    def step(self):
        """Execute one time step of mine behavior."""
        # 0. Capacity expansion + reserve replacement, applied each step.
        #    Without these, a 24-year run leaves nameplate capacity stuck
        #    at 2024 levels even as demand grows ~10x by 2050. Both rates
        #    are mineral-specific config knobs (see *_config.py); zero
        #    disables growth. Capacity also requires reserves -- if
        #    reserves are depleted, expansion is skipped.
        cfg = self.model.config
        steps_per_year = cfg.get("steps_per_year", 52)
        cap_growth_yr = cfg.get("mine_capacity_growth_per_year", 0.0)
        if cap_growth_yr > 0 and self.reserves > 0:
            self.production_capacity *= (1.0 + cap_growth_yr / steps_per_year)
        replacement_rate = cfg.get("reserve_replacement_rate", 0.0)
        if replacement_rate > 0 and self.cumulative_production > 0:
            # Constant fraction of *recent* extraction is replaced via
            # exploration -- approximated as fraction of the current
            # step's expected capacity output, applied to reserves.
            self.reserves += self.production_capacity * replacement_rate

        # 1. Carry forward any unsold production from last step into the
        #    pithead stockpile before resetting per-step counters. This
        #    keeps mineral mass conserved when processors don't lift the
        #    full available production (e.g. inventory backpressure).
        if self.production_this_step > 0:
            self.pithead_stockpile += self.production_this_step

        self.production_this_step = 0
        self.available_production_this_step = 0
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
        if rng.random() < self.model.config.get("mine_disruption_probability", 0.02):
            self._trigger_disruption()
            return

        # Mothball / restart logic. We treat "shut down for low price" as
        # a state separate from disruption so the mine actually reopens
        # when the price recovers (previously it never did, because
        # operational was set False but never restored).
        #
        # Restarts incur a multi-step lag (config: mine_restart_lag_steps,
        # default 26 weeks ~ 6 months) reflecting real-world rehiring
        # and recommissioning. While restart_counter > 0 the mine has
        # decided to restart but isn't yet producing. The restart aborts
        # if price falls back below the threshold during the lag.
        restart_margin = self.model.config.get("mine_restart_margin", 1.2)
        restart_lag = int(self.model.config.get("mine_restart_lag_steps", 26))
        price = self.model.current_price
        if self.mothballed:
            if price > self.extraction_cost * restart_margin:
                if self.restart_counter <= 0:
                    self.restart_counter = max(1, restart_lag)
                self.restart_counter -= 1
                if self.restart_counter <= 0:
                    self.mothballed = False
                else:
                    return
            else:
                # Price slipped back below the trigger -- abort restart.
                self.restart_counter = 0
                return
        else:
            if price < self.extraction_cost:
                self.mothballed = True
                self.restart_counter = 0
                return

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

        if self.model.is_embargoed(self.jurisdiction):
            self.domestic_stockpile += output
            self.embargoed_production_this_step = output
        else:
            self.production_this_step = output
            self.available_production_this_step = output

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
        cfg = self.model.config
        baseline = cfg.get("mine_baseline_utilization", 0.75)
        umin = cfg.get("mine_min_utilization", 0.5)
        umax = cfg.get("mine_max_utilization", 1.0)
        ratio_max = cfg.get("mine_max_utilization_ratio", 2.5)

        if self.extraction_cost <= 0:
            # Pathological config -- pin to baseline so we don't divide by 0.
            return 1.0

        price = self.model.current_price
        ratio = price / self.extraction_cost

        if ratio <= 1.0:
            utilization = umin
        elif ratio >= ratio_max:
            utilization = umax
        else:
            utilization = umin + (umax - umin) * (ratio - 1.0) / (ratio_max - 1.0)

        return utilization / baseline if baseline > 0 else 1.0

    def _release_stockpile(self):
        """Release a fraction of the post-embargo stockpile onto the market.

        Bled out over many steps (default ~26 weeks) rather than dumped at
        once, so a long embargo doesn't translate into a single-step
        supply shock when it lifts. The release adds to the same
        available_production_this_step bucket the price signal uses and
        the same production_this_step bucket processors purchase from.
        """
        release_steps = max(
            1, int(self.model.config.get("post_embargo_release_steps", 26))
        )
        chunk = self.domestic_stockpile / release_steps
        # Below ~1 step's worth of float dust, just drain the remainder
        # so the stockpile doesn't asymptote forever.
        if self.domestic_stockpile <= chunk * 1.01:
            chunk = self.domestic_stockpile
        self.domestic_stockpile -= chunk
        self.production_this_step += chunk
        self.available_production_this_step += chunk

    def _trigger_disruption(self):
        """Trigger a random disruption event."""
        duration_min = self.model.config.get("disruption_duration_min", 3)
        duration_max = self.model.config.get("disruption_duration_max", 5)
        rng = self.model.random_state
        self.disruption_counter = rng.randint(duration_min, duration_max)

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
