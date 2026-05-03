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
        self.input_inventory = 0   # Tons of processed mineral
        self.output_inventory = 0  # Units of finished product

        # Target input inventory in MINERAL TONS (= production_capacity in
        # product-units * mineral_intensity tons/unit * weeks of buffer).
        # Previously this was production_capacity * weeks, mixing units
        # with tons -- it overshot by 1/mineral_intensity (~12x for Li,
        # ~20,000x for Pt). Recompute live so substitution shrinks the
        # target as intensity falls.
        self.target_inventory_weeks = self.model.config.get(
            "manufacturer_target_inventory_weeks", 4
        )

        # Substitution tracking
        self.substitution_investment = 0.0
        self.high_price_counter = 0

        # Tracking
        self.produced_this_step = 0
        self.ordered_this_step = 0

    @property
    def target_inventory(self):
        """Target input inventory in MINERAL TONS."""
        return (
            self.production_capacity
            * self.target_inventory_weeks
            * self.mineral_intensity
        )

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
        """Check if prices are high and invest in substitution.

        The counter is "sticky": a brief dip in price decrements but does
        not reset, so substitution is triggered by sustained pressure
        rather than requiring an unbroken streak. After investment the
        counter resets, so each substitution increment requires a fresh
        sustained-pressure window.
        """
        threshold = self.model.config.get(
            "substitution_price_threshold",
            self.model.initial_price * 1.5,
        )
        trigger_steps = self.model.config.get("substitution_trigger_steps", 10)

        if self.model.current_price > threshold:
            self.high_price_counter += 1
        else:
            self.high_price_counter = max(0, self.high_price_counter - 1)

        if self.high_price_counter >= trigger_steps:
            self._invest_in_substitution()
            self.high_price_counter = 0

    def _invest_in_substitution(self):
        """Invest in R&D to reduce mineral intensity."""
        max_substitution = self.model.config.get("max_substitution", 0.30)
        substitution_rate = self.model.config.get("substitution_rate", 0.05)

        if self.substitution_investment < max_substitution:
            self.substitution_investment = min(
                self.substitution_investment + substitution_rate,
                max_substitution,
            )
            self.mineral_intensity = (
                self.initial_mineral_intensity * (1 - self.substitution_investment)
            )

    def _order_minerals(self):
        """Order minerals from processors if inventory is low.

        target_inventory and input_inventory are both in mineral tonnes.
        Order up to a fraction of the gap each step to smooth flow.
        """
        minerals_needed = max(0, self.target_inventory - self.input_inventory)
        if minerals_needed <= 0:
            return

        order_rate = self.model.config.get("manufacturer_order_rate", 0.5)
        order_amount = minerals_needed * order_rate

        for processor in self.model.processors:
            if order_amount <= 0:
                break

            available = processor.get_available_inventory()
            if available <= 0:
                continue

            desired = min(order_amount, available)
            actual = processor.accept_order(desired)

            self.input_inventory += actual
            self.ordered_this_step += actual
            order_amount -= actual

    def _produce_goods(self):
        """Produce finished goods from mineral inputs."""
        if self.input_inventory <= 0 or self.mineral_intensity <= 0:
            return

        max_from_minerals = self.input_inventory / self.mineral_intensity
        max_production = min(max_from_minerals, self.production_capacity)

        self.produced_this_step = max_production
        self.output_inventory += max_production

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
