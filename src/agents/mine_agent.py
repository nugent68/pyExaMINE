"""
MineAgent: Extracts raw minerals from reserves.
Behavior: Produces if profitable, subject to disruptions.
"""

from mesa import Agent
import random


class MineAgent(Agent):
    """Agent representing a mine that extracts raw minerals."""
    
    def __init__(self, unique_id, model, jurisdiction, ore_grade, 
                 production_capacity, extraction_cost, reserves):
        """Initialize a MineAgent.
        
        Args:
            unique_id: Unique identifier
            model: Model instance
            jurisdiction: Country/region name
            ore_grade: Fraction of pure mineral in ore (0-1)
            production_capacity: Maximum tons/step
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
        
        # Operational state
        self.operational = True
        self.disruption_counter = 0
        
        # Production tracking
        self.production_this_step = 0
        self.cumulative_production = 0
        
    def step(self):
        """Execute one time step of mine behavior."""
        self.production_this_step = 0
        
        # Check if recovering from disruption
        if self.disruption_counter > 0:
            self.disruption_counter -= 1
            if self.disruption_counter == 0:
                self.operational = True
            return
        
        # Random disruption check (2% probability)
        if self.operational and random.random() < self.model.config.get("mine_disruption_probability", 0.02):
            self._trigger_disruption()
            return
        
        # Check profitability
        if not self._is_profitable():
            self.operational = False
            return
        
        # Produce minerals if operational and reserves available
        if self.operational and self.reserves > 0:
            self._produce()
    
    def _is_profitable(self):
        """Check if mining is profitable at current prices."""
        current_price = self.model.current_price
        
        # Mine operates if price > extraction cost
        # Restarts if price > extraction_cost * 1.2 (20% margin)
        if not self.operational:
            return current_price > self.extraction_cost * 1.2
        else:
            return current_price > self.extraction_cost
    
    def _produce(self):
        """Produce minerals for this step.

        USGS production figures are reported as contained-mineral output, so
        production_capacity is already in tonnes of mineral. Refining yield
        loss is modeled separately on the processor side via conversion_efficiency.
        ore_grade is retained as metadata for cost/reporting only.
        """
        output = min(self.production_capacity, self.reserves)

        self.reserves -= output
        self.production_this_step = output
        self.cumulative_production += output
        
        # Offer production to processors (handled by model's market mechanism)
    
    def _trigger_disruption(self):
        """Trigger a random disruption event."""
        duration_min = self.model.config.get("disruption_duration_min", 3)
        duration_max = self.model.config.get("disruption_duration_max", 5)
        
        self.operational = False
        self.disruption_counter = random.randint(duration_min, duration_max)
    
    def apply_geopolitical_disruption(self, duration):
        """Apply a geopolitical disruption to this mine.
        
        Args:
            duration: Number of steps to remain disrupted
        """
        self.operational = False
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
