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
            capacity: Maximum processing capacity (tons of ore/step)
        """
        super().__init__(unique_id, model)

        # Core attributes
        self.conversion_efficiency = conversion_efficiency
        self.energy_cost = energy_cost
        self.capacity = capacity

        # Inventory
        self.inventory = 0        # Processed mineral (post-conversion tonnes)
        self.raw_ore_buffer = 0   # Pre-conversion tonnes awaiting processing

        # Safety stock (don't sell below this level), in PROCESSED tonnes.
        # 2 steps of *output* capacity, not 2 steps of input.
        safety_weeks = self.model.config.get("processor_safety_stock_weeks", 2.0)
        self.safety_stock = capacity * conversion_efficiency * safety_weeks

        # Tracking
        self.processed_this_step = 0
        self.purchased_this_step = 0
        self.sold_this_step = 0
        self.recycled_received_this_step = 0

    def step(self):
        """Execute one time step of processor behavior."""
        self.processed_this_step = 0
        self.purchased_this_step = 0
        self.sold_this_step = 0
        self.recycled_received_this_step = 0

        # 1. Purchase ore from mines
        self._purchase_ore()

        # 2. Process ore to pure mineral
        self._process_ore()

        # 3. Sell to manufacturers (manufacturers pull via accept_order)

    def _purchase_ore(self):
        """Purchase ore from available mines, cheapest first."""
        mines = self.model.mines
        if not mines:
            return

        ranked_mines = sorted(mines, key=lambda m: m.extraction_cost)
        remaining_capacity = self.capacity - self.raw_ore_buffer

        for mine in ranked_mines:
            if remaining_capacity <= 0:
                break

            available = mine.get_available_supply()
            if available <= 0:
                continue

            desired = min(remaining_capacity, available)
            actual = mine.sell_production(desired)
            self.raw_ore_buffer += actual
            self.purchased_this_step += actual
            remaining_capacity -= actual

    def _process_ore(self):
        """Convert raw ore to processed mineral."""
        if self.raw_ore_buffer <= 0:
            return

        to_process = min(self.raw_ore_buffer, self.capacity)
        processed = to_process * self.conversion_efficiency

        self.raw_ore_buffer -= to_process
        self.inventory += processed
        self.processed_this_step = processed

    def receive_recycled(self, amount):
        """Accept recycled material directly into output inventory.

        Recycled material is already in pure-mineral form (the recycler
        applies recovery_efficiency before delivery), so it bypasses the
        conversion stage. Tracked separately for visibility.
        """
        if amount <= 0:
            return
        self.inventory += amount
        self.recycled_received_this_step += amount

    def get_available_inventory(self):
        """Get the amount of processed mineral available for sale."""
        return max(0, self.inventory - self.safety_stock)

    def sell_inventory(self, amount):
        """Sell processed mineral to manufacturers.

        Args:
            amount: Amount requested (tons)

        Returns:
            Actual amount sold
        """
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
