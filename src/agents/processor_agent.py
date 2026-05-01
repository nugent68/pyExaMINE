"""
ProcessorAgent: Converts raw ore to processed mineral.
Maintains inventory and sells to manufacturers.
"""

from mesa import Agent


class ProcessorAgent(Agent):
    """Agent representing a processing facility that converts ore to pure mineral."""
    
    def __init__(self, unique_id, model, conversion_efficiency, energy_cost, capacity):
        """Initialize a ProcessorAgent.
        
        Args:
            unique_id: Unique identifier
            model: Model instance
            conversion_efficiency: Fraction of ore converted to pure mineral (0-1)
            energy_cost: Cost per ton to process ($/ton)
            capacity: Maximum processing capacity (tons/step)
        """
        super().__init__(unique_id, model)
        
        # Core attributes
        self.conversion_efficiency = conversion_efficiency
        self.energy_cost = energy_cost
        self.capacity = capacity
        
        # Inventory
        self.inventory = 0
        self.raw_ore_buffer = 0
        
        # Safety stock (don't sell below this level)
        self.safety_stock = capacity * 2.0  # 2 steps of processing capacity
        
        # Tracking
        self.processed_this_step = 0
        self.purchased_this_step = 0
        self.sold_this_step = 0
        
        # Supplier preferences (mine_id: reliability_score)
        self.supplier_preferences = {}
    
    def step(self):
        """Execute one time step of processor behavior."""
        self.processed_this_step = 0
        self.purchased_this_step = 0
        self.sold_this_step = 0
        
        # 1. Purchase ore from mines
        self._purchase_ore()
        
        # 2. Process ore to pure mineral
        self._process_ore()
        
        # 3. Sell to manufacturers (handled by model's market mechanism)
    
    def _purchase_ore(self):
        """Purchase ore from available mines."""
        mines = self.model.mines
        if not mines:
            return

        # Rank mines by cost (cheapest first).
        ranked_mines = sorted(mines, key=lambda m: m.extraction_cost)
        
        # Purchase from cheapest sources up to capacity
        remaining_capacity = self.capacity - self.raw_ore_buffer
        
        for mine in ranked_mines:
            if remaining_capacity <= 0:
                break
            
            available = mine.get_available_supply()
            if available > 0:
                # Calculate how much we want to buy
                desired = min(remaining_capacity, available)
                
                # Check if affordable at current price
                total_cost = self.model.current_price * desired
                # Simplified: assume processor can afford (has access to credit)
                
                # Purchase
                actual = mine.sell_production(desired)
                self.raw_ore_buffer += actual
                self.purchased_this_step += actual
                remaining_capacity -= actual
                
                # Update supplier reliability
                self.supplier_preferences[mine.unique_id] = \
                    self.supplier_preferences.get(mine.unique_id, 0.5) * 0.9 + 0.1
    
    def _process_ore(self):
        """Convert raw ore to processed mineral."""
        if self.raw_ore_buffer <= 0:
            return
        
        # Process up to capacity
        to_process = min(self.raw_ore_buffer, self.capacity)
        
        # Apply conversion efficiency
        processed = to_process * self.conversion_efficiency
        
        # Update buffers
        self.raw_ore_buffer -= to_process
        self.inventory += processed
        self.processed_this_step = processed
    
    def get_available_inventory(self):
        """Get the amount of processed mineral available for sale."""
        # Only sell above safety stock
        available = max(0, self.inventory - self.safety_stock)
        return available
    
    def sell_inventory(self, amount):
        """Sell processed mineral to manufacturers.
        
        Args:
            amount: Amount requested (tons)
        
        Returns:
            Actual amount sold
        """
        # Don't sell below safety stock
        available = max(0, self.inventory - self.safety_stock)
        sold = min(amount, available)
        self.inventory -= sold
        self.sold_this_step += sold
        return sold
    
    def accept_order(self, amount):
        """Accept an order from a manufacturer.
        
        Args:
            amount: Amount requested
        
        Returns:
            Amount that can be fulfilled
        """
        return self.sell_inventory(amount)
