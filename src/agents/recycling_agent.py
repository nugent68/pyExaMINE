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
        
        # 1. Collect from end-of-life pool
        self._collect_eol_materials()
        
        # 2. Process collected materials
        self._process_materials()
        
        # 3. Sell recovered materials to processors if profitable
        self._sell_recovered_materials()
    
    def _collect_eol_materials(self):
        """Collect materials from the end-of-life pool."""
        # Get materials from EOL pool with product lifetime lag
        available_eol = self.model.get_eol_materials()
        
        if available_eol > 0:
            # Collect a fraction
            collected = available_eol * self.collection_rate
            self.storage += collected
            self.collected_this_step = collected
            
            # Remove from EOL pool
            self.model.remove_from_eol_pool(collected)
    
    def _process_materials(self):
        """Process collected materials to recover pure mineral."""
        if self.storage <= 0:
            return
        
        # Process all storage (simplified)
        recovered = self.storage * self.recovery_efficiency
        
        # Clear storage and track recovered amount
        self.storage = 0
        self.recycled_this_step = recovered
    
    def _sell_recovered_materials(self):
        """Sell recovered materials to processors if profitable."""
        if self.recycled_this_step <= 0:
            return
        
        # Check profitability
        revenue_per_ton = self.model.current_price
        
        # Only sell if revenue > cost (simplified: ignore collection cost, already incurred)
        if revenue_per_ton > self.processing_cost:
            # Find processors to sell to
            processors = [agent for agent in self.model.schedule.agents 
                         if hasattr(agent, 'inventory') and hasattr(agent, 'conversion_efficiency')]
            
            if processors:
                # Distribute equally (simplified)
                amount_per_processor = self.recycled_this_step / len(processors)
                
                for processor in processors:
                    # Inject into processor inventory
                    processor.inventory += amount_per_processor
                    self.total_recycled += amount_per_processor
                
                # Clear recycled amount
                self.recycled_this_step = 0
        else:
            # Store for later (keep in recycled_this_step, will accumulate)
            pass
    
    def get_recycled_supply(self):
        """Get the amount recycled this step."""
        return self.recycled_this_step
