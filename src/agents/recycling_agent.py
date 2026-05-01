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
            collection_rate: Fraction of EOL materials collected (0-1)
            recovery_efficiency: Fraction of collected materials recovered (0-1)
            processing_cost: Cost per ton to process ($/ton)
        """
        super().__init__(unique_id, model)
        
        # Core attributes
        self.collection_rate = collection_rate
        self.recovery_efficiency = recovery_efficiency
        self.processing_cost = processing_cost
        
        # Storage
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
        """Collect a fraction of this step's EOL materials (mineral tons)."""
        available_mineral_tons = self.model.get_eol_materials()
        if available_mineral_tons <= 0:
            return

        collected_mineral = available_mineral_tons * self.collection_rate
        self.storage += collected_mineral
        self.collected_this_step = collected_mineral
        self.model.remove_from_eol_pool(collected_mineral)

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

        amount_per_processor = recovered / len(processors)
        for processor in processors:
            processor.inventory += amount_per_processor

        self.recycled_this_step = recovered
        self.total_recycled += recovered
    
    def get_recycled_supply(self):
        """Get the amount recycled this step."""
        return self.recycled_this_step
