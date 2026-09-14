"""
RecyclingAgent: Recovers minerals from end-of-life products.
Collects from EOL pool with product lifetime delay.
"""

from mesa import Agent

from ..config.overrides import cfg_for, price_for


class RecyclingAgent(Agent):
    """Agent representing a recycling facility that recovers minerals."""

    def __init__(self, unique_id, model, country, facility,
                 collection_rate, recovery_efficiency, processing_cost,
                 capacity_per_step=None):
        """Initialize a RecyclingAgent.

        Args:
            unique_id: Unique identifier
            model: Model instance
            country: Host country
            facility: Facility name (e.g. 'Redwood Materials NV')
            collection_rate: Fraction of EOL materials this recycler collects (0-1)
            recovery_efficiency: Fraction of collected materials recovered (0-1)
            processing_cost: Cost per ton to process ($/ton)
            capacity_per_step: Maximum tonnes this facility can take in
                per step. None means uncapped (legacy behavior). When
                set, the per-step intake is capped, preventing a tiny
                facility from absorbing the full share of a huge EOL
                bucket (which the original CSV capacity column was
                meant to encode).
        """
        super().__init__(unique_id, model)

        # Identity
        self.country = country
        self.facility = facility
        self.label = f"{country}/{facility}"

        # Core attributes
        self.collection_rate = collection_rate
        self.recovery_efficiency = recovery_efficiency
        self.processing_cost = processing_cost
        self.capacity_per_step = capacity_per_step

        # Storage. Two pools so we don't double-apply recovery_efficiency
        # if material has to wait across steps for downstream headroom:
        #   storage         -- collected mineral tons, pre-recovery (raw scrap)
        #   recovered_pool  -- post-recovery, awaiting dispatch to processors
        self.storage = 0
        self.recovered_pool = 0.0

        # Tracking
        self.recycled_this_step = 0
        self.collected_this_step = 0
        self.total_recycled = 0

        # Capacity-ramp knob: if a country override sets
        # ``recycling_capacity_ramp_per_year`` > 0, this facility's
        # collection_rate and capacity_per_step both compound at that
        # rate every step (IRA-style subsidy effect). Default 0 (no
        # ramp) preserves legacy behaviour. Read once and cached -- a
        # per-step multiplicative update inside step() is then a
        # single float op.
        self._cfg_steps_per_year = int(
            cfg_for(model, country, "steps_per_year", 52)
        )
        self._cfg_capacity_ramp_per_year = float(
            cfg_for(model, country, "recycling_capacity_ramp_per_year", 0.0)
        )

    def step(self):
        """Execute one time step of recycling behavior."""
        self.recycled_this_step = 0
        self.collected_this_step = 0

        # Capacity ramp (US recyclers tunable via country override).
        # Compounds the per-step knobs that gate how much EOL material
        # this facility can absorb. capacity_per_step may be None for
        # the legacy uncapped path.
        if self._cfg_capacity_ramp_per_year > 0:
            mult = 1.0 + self._cfg_capacity_ramp_per_year / self._cfg_steps_per_year
            self.collection_rate *= mult
            if self.capacity_per_step is not None:
                self.capacity_per_step *= mult

        # 1. Collect from end-of-life pool (raw mineral tons)
        self._collect_eol_materials()

        # 2. Sell from storage to processors if profitable.
        #    recovery_efficiency is applied at the moment of sale, so material
        #    that sits in storage waiting for a profitable price is not
        #    repeatedly degraded by the efficiency factor.
        self._sell_recovered_materials()

    def _collect_eol_materials(self):
        """Collect this recycler's share of the step's initial EOL bucket.

        Uses the model's snapshot of the bucket size at the start of the
        step so each recycler claims a fixed fraction of the original
        amount, regardless of activation order. This avoids the
        compounding shortfall (1 - prod(1 - r_i)) that arose when each
        recycler in turn took a fraction of whatever was left.

        Per-recycler capacity is enforced: a 1,500 t/yr facility cannot
        absorb its full share of a millions-of-tons EOL bucket. The cap
        runs in mineral tonnes per step (capacity_per_step). When the
        cap binds, the un-collected residue is left for other recyclers
        to pick up the same step (since collect_eol_tons reads from the
        live bucket, not the snapshot).
        """
        snapshot = self.model._eol_initial_this_step
        requested = self.collection_rate * snapshot
        if self.capacity_per_step is not None:
            requested = min(requested, self.capacity_per_step)
        collected = self.model.collect_eol_tons(requested)
        if collected <= 0:
            return
        self.storage += collected
        self.collected_this_step = collected

    def _sell_recovered_materials(self):
        """Process and sell stored material when profitable.

        Recycled mineral is dispatched through the routing engine the
        same way primary processed mineral is, so a recycler in the USA
        shipping to a processor in China traverses the route's
        chokepoints with the route's lead time. Final receipt lands in
        the processor via receive_shipment(material_type='recycled'),
        which forwards to receive_recycled so the existing tracking
        still works.

        Distribution is region-preferenced: same-country processors get
        first call (split by available headroom among them), then same-
        region (via the routing module's COUNTRY_REGION map), then the
        rest of the world. Within each tier the split is proportional to
        each processor's remaining inventory headroom -- a processor
        already near its inventory cap gets a smaller share, and any
        recovered material that doesn't fit in this step's targets stays
        in ``recovered_pool`` for the next step. Without this, recyclers
        could push processors past their inventory_cap and silently
        bypass the backpressure that throttles primary ore purchases.
        """
        if price_for(self.model, self.country) <= self.processing_cost:
            # Hold raw material in storage until the price recovers.
            return

        # Move newly-collected scrap through the recovery efficiency
        # *once*, then add it to the dispatch-ready pool. Keeping
        # recovered material separate means a sit-across-steps shipment
        # (because all processors are full this step) doesn't get
        # double-discounted by recovery_efficiency next step.
        # The (1 - recovery_efficiency) fraction is a physical recycler
        # loss (slag, off-gas, residues); booked on the model so the
        # mass-balance diagnostic stays consistent.
        if self.storage > 0:
            recovered = self.storage * self.recovery_efficiency
            self.model.cumulative_recovery_loss += (self.storage - recovered)
            self.recovered_pool += recovered
            self.storage = 0

        if self.recovered_pool <= 0:
            return

        processors = self.model.processors
        if not processors:
            return

        targets = self._region_preferenced_targets(processors)
        if not targets:
            return

        headrooms = [p.headroom_for_recycled() for p in targets]
        total_headroom = sum(headrooms)
        if total_headroom <= 0:
            # All targets already at their inventory cap -- hold the
            # recovered material for next step.
            return

        to_dispatch = min(self.recovered_pool, total_headroom)
        dispatched = 0.0
        for processor, headroom in zip(targets, headrooms):
            if headroom <= 0:
                continue
            share = to_dispatch * (headroom / total_headroom)
            share = min(share, headroom)
            if share <= 0:
                continue
            self.model.dispatch_shipment(
                material_type='recycled',
                quantity=share,
                origin_country=self.country,
                dest_country=processor.country,
                destination=processor,
                mineral_tons=share,
            )
            dispatched += share

        self.recovered_pool = max(0.0, self.recovered_pool - dispatched)
        self.recycled_this_step = dispatched
        self.total_recycled += dispatched

    def _region_preferenced_targets(self, processors):
        """Pick the highest-preference tier of processors.

        Returns same-country processors if any exist; else same-region
        processors; else all processors. Empty list only if the global
        processor pool is empty (which the caller already guards).

        The chosen tier is cached on first call -- processor country is
        immutable post-construction, so the membership of each tier never
        changes after agent setup. The cache replaces what was a
        per-step scan of every processor for every recycler.
        """
        cached = getattr(self, '_cached_target_tier', None)
        if cached is not None:
            return cached

        from ..data.routing import COUNTRY_REGION

        local = [p for p in processors if p.country == self.country]
        if local:
            self._cached_target_tier = local
            return local

        my_region = COUNTRY_REGION.get(self.country, 'Other')
        regional = [
            p for p in processors
            if COUNTRY_REGION.get(p.country, 'Other') == my_region
        ]
        if regional:
            self._cached_target_tier = regional
            return regional

        self._cached_target_tier = list(processors)
        return self._cached_target_tier

    def get_recycled_supply(self):
        """Get the amount recycled this step."""
        return self.recycled_this_step
