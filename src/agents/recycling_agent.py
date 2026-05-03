"""
RecyclingAgent: Recovers minerals from end-of-life products.
Collects from EOL pool with product lifetime delay.
"""

from mesa import Agent


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

        # Storage (collected mineral tons, pre-recovery-efficiency)
        self.storage = 0

        # Tracking
        self.recycled_this_step = 0
        self.collected_this_step = 0
        self.total_recycled = 0

    def step(self):
        """Execute one time step of recycling behavior."""
        self.recycled_this_step = 0
        self.collected_this_step = 0

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
        first call (split evenly among them), then same-region (via
        the routing module's COUNTRY_REGION map), then the rest of the
        world. Within each tier the split is even. This avoids the
        original even-across-the-globe split sending small amounts of
        recycled USA mineral to a Tianqi facility every step.
        """
        if self.storage <= 0:
            return

        if self.model.current_price <= self.processing_cost:
            # Hold raw material in storage until the price recovers.
            return

        recovered = self.storage * self.recovery_efficiency
        self.storage = 0

        processors = self.model.processors
        if not processors or recovered <= 0:
            return

        targets = self._region_preferenced_targets(processors)
        if not targets:
            return

        amount_per_processor = recovered / len(targets)
        for processor in targets:
            self.model.dispatch_shipment(
                material_type='recycled',
                quantity=amount_per_processor,
                origin_country=self.country,
                dest_country=processor.country,
                destination=processor,
                mineral_tons=amount_per_processor,
            )

        self.recycled_this_step = recovered
        self.total_recycled += recovered

    def _region_preferenced_targets(self, processors):
        """Pick the highest-preference tier of processors.

        Returns same-country processors if any exist; else same-region
        processors; else all processors. Empty list only if the global
        processor pool is empty (which the caller already guards).
        """
        from ..data.routing import COUNTRY_REGION

        local = [p for p in processors if p.country == self.country]
        if local:
            return local

        my_region = COUNTRY_REGION.get(self.country, 'Other')
        regional = [
            p for p in processors
            if COUNTRY_REGION.get(p.country, 'Other') == my_region
        ]
        if regional:
            return regional

        return list(processors)

    def get_recycled_supply(self):
        """Get the amount recycled this step."""
        return self.recycled_this_step
