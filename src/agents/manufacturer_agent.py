"""
ManufacturerAgent: Produces goods using processed minerals.
Implements substitution investment when prices remain high.
"""

from mesa import Agent


class ManufacturerAgent(Agent):
    """Agent representing a manufacturer that uses minerals to produce goods."""
    
    def __init__(self, unique_id, model, mineral_intensity, production_capacity):
        """Initialize a ManufacturerAgent.
        
        Args:
            unique_id: Unique identifier
            model: Model instance
            mineral_intensity: Tons of mineral per unit of product
            production_capacity: Maximum production (units/step)
        """
        super().__init__(unique_id, model)
        
        # Core attributes
        self.initial_mineral_intensity = mineral_intensity
        self.mineral_intensity = mineral_intensity
        self.production_capacity = production_capacity
        
        # Inventory
        self.input_inventory = 0  # Tons of processed mineral
        self.output_inventory = 0  # Units of finished product
        
        # Target inventory (weeks of supply)
        target_weeks = self.model.config.get("manufacturer_target_inventory_weeks", 4)
        self.target_inventory = self.production_capacity * target_weeks
        
        # Substitution tracking
        self.substitution_investment = 0.0
        self.high_price_counter = 0
        
        # Tracking
        self.produced_this_step = 0
        self.ordered_this_step = 0
    
    def step(self):
        """Execute one time step of manufacturer behavior."""
        self.produced_this_step = 0
        self.ordered_this_step = 0
        
        # 1. Check price history and update substitution
        self._check_price_and_substitute()
        
        # 2. Order minerals from processors if inventory low
        self._order_minerals()
        
        # 3. Produce goods
        self._produce_goods()
    
    def _check_price_and_substitute(self):
        """Check if prices are high and invest in substitution."""
        threshold = self.model.config.get("substitution_price_threshold", 
                                         self.model.initial_price * 1.5)
        trigger_steps = self.model.config.get("substitution_trigger_steps", 10)
        
        # Track consecutive high-price steps
        if self.model.current_price > threshold:
            self.high_price_counter += 1
        else:
            self.high_price_counter = 0
        
        # Invest in substitution if price high for long enough
        if self.high_price_counter >= trigger_steps:
            self._invest_in_substitution()
            self.high_price_counter = 0  # Reset counter
    
    def _invest_in_substitution(self):
        """Invest in R&D to reduce mineral intensity."""
        max_substitution = self.model.config.get("max_substitution", 0.30)
        substitution_rate = self.model.config.get("substitution_rate", 0.05)
        
        # Check if we can still substitute more
        if self.substitution_investment < max_substitution:
            self.substitution_investment += substitution_rate
            self.substitution_investment = min(self.substitution_investment, max_substitution)
            
            # Reduce mineral intensity
            self.mineral_intensity = self.initial_mineral_intensity * (1 - self.substitution_investment)
    
    def _order_minerals(self):
        """Order minerals from processors if inventory is low."""
        # Calculate how much we need
        minerals_needed = max(0, self.target_inventory - self.input_inventory)
        
        if minerals_needed > 0:
            # Rate-limit ordering: only order a fraction per step to smooth flow
            order_rate = 0.5  # Order up to 50% of need per step
            order_amount = minerals_needed * order_rate
            
            # Try to order from processors - use model's processor list directly
            processors = self.model.processors
            
            for processor in processors:
                if order_amount <= 0:
                    break
                
                available = processor.get_available_inventory()
                if available > 0:
                    # Order what we need (up to order_amount limit)
                    desired = min(order_amount, available)
                    actual = processor.accept_order(desired)
                    
                    self.input_inventory += actual
                    self.ordered_this_step += actual
                    order_amount -= actual
    
    def _produce_goods(self):
        """Produce finished goods from mineral inputs."""
        if self.input_inventory <= 0 or self.mineral_intensity <= 0:
            return
        
        # Calculate how much we can produce
        max_from_minerals = self.input_inventory / self.mineral_intensity
        max_production = min(max_from_minerals, self.production_capacity)
        
        # Produce
        self.produced_this_step = max_production
        self.output_inventory += max_production
        
        # Consume minerals
        minerals_consumed = max_production * self.mineral_intensity
        self.input_inventory -= minerals_consumed
    
    def get_available_output(self):
        """Get available finished goods for retailers."""
        return self.output_inventory
    
    def sell_output(self, amount):
        """Sell finished goods to retailers.
        
        Args:
            amount: Amount requested (units)
        
        Returns:
            Actual amount sold
        """
        sold = min(amount, self.output_inventory)
        self.output_inventory -= sold
        return sold
