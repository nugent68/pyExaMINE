"""
RecyclingAgent: Recovers minerals from end-of-life products.
Collects from EOL pool with product lifetime delay.
"""

from mesa import Agent


class RecyclingAgent(Agent):
    """Agent representing a recycling facility that recovers minerals."""

    def __init__(self, unique_id, model, collection_rate, recovery_efficiency, processing_cost):
        """Initialize a RecyclingAgent.

        Args:
            unique_id: Unique identifier
            model: Model instance
            collection_rate: Fraction of EOL materials this recycler collects (0-1)
            recovery_efficiency: Fraction of collected materials recovered (0-1)
            processing_cost: Cost per ton to process ($/ton)
        """
        super().__init__(unique_id, model)

        # Core attributes
        self.collection_rate = collection_rate
        self.recovery_efficiency = recovery_efficiency
        self.processing_cost = processing_cost

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
        """
        collected = self.model.collect_eol(self.collection_rate)
        if collected <= 0:
            return
        self.storage += collected
        self.collected_this_step = collected

    def _sell_recovered_materials(self):
        """Process and sell stored material when profitable."""
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

        # Distribute evenly across processors, routed via the processor's
        # receive_recycled hook so it gets accounted for separately.
        amount_per_processor = recovered / len(processors)
        for processor in processors:
            processor.receive_recycled(amount_per_processor)

        self.recycled_this_step = recovered
        self.total_recycled += recovered

    def get_recycled_supply(self):
        """Get the amount recycled this step."""
        return self.recycled_this_step
