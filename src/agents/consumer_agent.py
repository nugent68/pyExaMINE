"""
ConsumerAgent: Generates demand with price sensitivity.
Contributes to end-of-life pool for recycling.
"""

from mesa import Agent
import numpy as np


class ConsumerAgent(Agent):
    """Agent representing consumer demand with price elasticity."""

    def __init__(self, unique_id, model, country, base_demand, price_sensitivity):
        """Initialize a ConsumerAgent.

        Args:
            unique_id: Unique identifier
            model: Model instance
            country: Host country (used to prefer local retailers)
            base_demand: Base demand per step (units)
            price_sensitivity: Price elasticity coefficient (negative)
        """
        super().__init__(unique_id, model)

        # Identity
        self.country = country
        self.label = f"{country}/consumers"

        # Demand parameters
        self.base_demand = base_demand
        self.price_sensitivity = price_sensitivity
        self.demand_threshold_multiplier = model.config.get(
            "consumer_demand_threshold_multiplier", 2.0
        )

        # Current state
        self.current_demand = base_demand
        self.fulfilled_demand = 0
        self.unfulfilled_demand = 0

        # Tracking
        self.total_purchased = 0

    def step(self):
        """Execute one time step of consumer behavior."""
        self.fulfilled_demand = 0
        self.unfulfilled_demand = 0

        # 1. Calculate current demand based on price
        self._calculate_demand()

        # 2. Attempt to purchase from retailers
        self._purchase_from_retailers()

        # 3. End-of-life pool contribution happens inside _purchase_from_retailers

    def _calculate_demand(self):
        """Calculate demand based on current price elasticity."""
        price_ratio = self.model.current_price / self.model.initial_price

        if price_ratio > 0:
            price_effect = self.price_sensitivity * np.log(price_ratio)
            self.current_demand = self.base_demand * np.exp(price_effect)
        else:
            self.current_demand = self.base_demand

        # Apply threshold: cut demand sharply if price exceeds tolerance.
        max_acceptable_price = (
            self.model.initial_price * self.demand_threshold_multiplier
        )
        if self.model.current_price > max_acceptable_price:
            self.current_demand *= 0.5

        self.current_demand = max(0, self.current_demand)

    def _purchase_from_retailers(self):
        """Attempt to purchase goods from retailers in this country.

        End consumers buy locally (no cross-border individual shipping),
        so we only consider retailers whose .country matches this
        consumer's. If the local retailer is stocked out, the demand is
        unfulfilled (the consumer doesn't import an EV from another
        country).
        """
        local_retailers = [r for r in self.model.retailers if r.country == self.country]

        if not local_retailers:
            self.unfulfilled_demand = self.current_demand
            return

        order = list(local_retailers)
        self.model.random_state.shuffle(order)

        remaining_demand = self.current_demand
        mineral_acquired = 0.0
        for retailer in order:
            if remaining_demand <= 0:
                break
            purchased, mineral = retailer.sell_to_consumer(remaining_demand)
            self.fulfilled_demand += purchased
            self.total_purchased += purchased
            mineral_acquired += mineral
            remaining_demand -= purchased

        self.unfulfilled_demand = remaining_demand

        if self.fulfilled_demand > 0:
            self.model.add_to_eol_pool(self.fulfilled_demand, mineral_acquired)
