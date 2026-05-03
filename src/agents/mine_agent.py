"""
MineAgent: Extracts raw minerals from reserves.
Behavior: Produces if profitable, subject to disruptions and embargoes.
"""

from mesa import Agent


class MineAgent(Agent):
    """Agent representing a mine that extracts raw minerals."""

    def __init__(self, unique_id, model, jurisdiction, ore_grade,
                 production_capacity, extraction_cost, reserves):
        """Initialize a MineAgent.

        Args:
            unique_id: Unique identifier
            model: Model instance
            jurisdiction: Country/region name
            ore_grade: Fraction of pure mineral in ore (0-1, metadata only)
            production_capacity: Maximum tons/step (contained mineral)
            extraction_cost: Cost per ton to extract ($/ton)
            reserves: Total remaining reserves (tons)
        """
        super().__init__(unique_id, model)

        # Core attributes
        self.jurisdiction = jurisdiction
        self.ore_grade = ore_grade
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

        # Production tracking. Two views per step:
        #   available_production_this_step: gross output offered to the
        #     international market this step. Set once in _produce (when
        #     not embargoed) and never decremented; used by the price
        #     signal and the gross-production data series.
        #   production_this_step: same value initially, but decremented by
        #     processor purchases as the step progresses; used by the
        #     processor-purchase loop to discover what is still for sale.
        self.production_this_step = 0
        self.available_production_this_step = 0
        self.cumulative_production = 0
        self.embargoed_production_this_step = 0
        self.domestic_stockpile = 0

    @property
    def operational(self):
        """True iff the mine is currently producing."""
        return self.disruption_counter == 0 and not self.mothballed

    def step(self):
        """Execute one time step of mine behavior."""
        self.production_this_step = 0
        self.available_production_this_step = 0
        self.embargoed_production_this_step = 0

        # Drain any post-embargo stockpile onto the market each step,
        # regardless of disruption / mothball status (the material is
        # already extracted and sitting in a warehouse). Skipped while
        # the country is still embargoed.
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
        restart_margin = self.model.config.get("mine_restart_margin", 1.2)
        price = self.model.current_price
        if self.mothballed:
            if price > self.extraction_cost * restart_margin:
                self.mothballed = False
            else:
                return
        else:
            if price < self.extraction_cost:
                self.mothballed = True
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
