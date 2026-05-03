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
            actual = manufacturer.sell_output(desired)
            if actual <= 0:
                continue

            self.pending_orders.append({
                'quantity': actual,
                'arrival_step': self.model.current_step + self.lead_time,
            })
            remaining -= actual

    def sell_to_consumer(self, amount):
        """Sell to a consumer.

        Args:
            amount: Amount requested

        Returns:
            Actual amount sold
        """
        if self.inventory <= 0:
            self.stockouts += 1
            return 0

        sold = min(amount, self.inventory)
        self.inventory -= sold
        self.sold_this_step += sold
        self.total_sales += sold
        return sold

    def get_available_inventory(self):
        """Get current available inventory."""
        return self.inventory
