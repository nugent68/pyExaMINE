"""
RetailerAgent: Manages inventory and sells to consumers.
Implements (s, Q) inventory policy with multi-order pipeline.
"""

from mesa import Agent


class RetailerAgent(Agent):
    """Agent representing a retailer using (s,Q) inventory policy."""

    def __init__(self, unique_id, model, reorder_point, order_quantity):
        """Initialize a RetailerAgent.

        Args:
            unique_id: Unique identifier
            model: Model instance
            reorder_point: Inventory level that triggers reorder (s parameter)
            order_quantity: Amount to order when restocking (Q parameter)
        """
        super().__init__(unique_id, model)

        # Inventory policy parameters
        self.reorder_point = reorder_point
        self.order_quantity = order_quantity

        # Inventory
        self.inventory = order_quantity  # Start with some inventory
        # Mineral content embedded in on-hand inventory (tons). Travels
        # with units so consumers' EOL deposits use as-built intensity.
        # Initial inventory is "pre-history" stock with no recorded
        # intensity; treat it as 0 so it neither under- nor over-counts.
        # It will mostly be flushed within the first few steps anyway.
        self.inventory_mineral = 0.0

        # Order pipeline. Multiple outstanding orders are allowed up to
        # max_pending so the lead-time pipeline doesn't cap throughput
        # at order_quantity / lead_time.
        self.pending_orders = []
        self.lead_time = model.config.get("retailer_lead_time", 3)
        self.max_pending = model.config.get("retailer_max_pending_orders", 3)

        # Tracking
        self.sold_this_step = 0
        self.stockouts = 0
        self.total_sales = 0

    def step(self):
        """Execute one time step of retailer behavior."""
        self.sold_this_step = 0

        # 1. Receive pending orders that have arrived
        self._receive_orders()

        # 2. Sell to consumers (handled by consumer agents pulling)

        # 3. Check inventory and reorder if needed
        self._check_and_reorder()

    def _receive_orders(self):
        """Receive pending orders that have arrived."""
        current_step = self.model.current_step

        arrived = [o for o in self.pending_orders if o['arrival_step'] <= current_step]
        for order in arrived:
            self.inventory += order['quantity']
            self.inventory_mineral += order.get('mineral', 0.0)
            self.pending_orders.remove(order)

    def _check_and_reorder(self):
        """Reorder if inventory is below reorder_point and pipeline has room."""
        # Inventory position = on hand + on order; reorder if below s.
        on_order = sum(o['quantity'] for o in self.pending_orders)
        inventory_position = self.inventory + on_order

        if (
            inventory_position <= self.reorder_point
            and len(self.pending_orders) < self.max_pending
        ):
            self._place_order()

    def _place_order(self):
        """Place an order with manufacturers."""
        manufacturers = self.model.manufacturers
        remaining = self.order_quantity

        for manufacturer in manufacturers:
            if remaining <= 0:
                break

            available = manufacturer.get_available_output()
            if available <= 0:
                continue

            desired = min(remaining, available)
            actual, mineral = manufacturer.sell_output(desired)
            if actual <= 0:
                continue

            self.pending_orders.append({
                'quantity': actual,
                'mineral': mineral,
                'arrival_step': self.model.current_step + self.lead_time,
            })
            remaining -= actual

    def sell_to_consumer(self, amount):
        """Sell to a consumer.

        Args:
            amount: Amount requested

        Returns:
            Tuple (units_sold, mineral_tons_embedded). Mineral content
            is the proportional share of inventory_mineral so as-built
            intensity travels with the goods all the way to EOL.
        """
        if self.inventory <= 0:
            self.stockouts += 1
            return 0.0, 0.0

        sold = min(amount, self.inventory)
        mineral_share = self.inventory_mineral * (sold / self.inventory) if self.inventory > 0 else 0.0
        self.inventory -= sold
        self.inventory_mineral = max(0.0, self.inventory_mineral - mineral_share)
        self.sold_this_step += sold
        self.total_sales += sold
        return sold, mineral_share

    def get_available_inventory(self):
        """Get current available inventory."""
        return self.inventory
