"""
RetailerAgent: Manages inventory and sells to local consumers.
Implements (s, Q) inventory policy. Orders from manufacturers anywhere
in the world via the model's routing engine; the inbound shipment moves
through a TransportAgent with route-specific lead time and chokepoint
delays.
"""

from mesa import Agent


class RetailerAgent(Agent):
    """Agent representing a country's retail aggregate using (s,Q) policy."""

    def __init__(self, unique_id, model, country, reorder_point, order_quantity):
        """Initialize a RetailerAgent.

        Args:
            unique_id: Unique identifier
            model: Model instance
            country: Host country (used by routing engine when ordering
                from manufacturers and serving local consumers)
            reorder_point: Inventory level that triggers reorder (s parameter)
            order_quantity: Amount to order when restocking (Q parameter)
        """
        super().__init__(unique_id, model)

        # Identity
        self.country = country
        self.label = f"{country}/retail"

        # Inventory policy parameters
        self.reorder_point = reorder_point
        self.order_quantity = order_quantity

        # Inventory
        self.inventory = order_quantity  # Start with some inventory
        # Mineral content embedded in on-hand inventory (tons). Carried
        # as as-built so consumers' EOL deposits are correct even after
        # substitution events drift current intensity downward.
        self.inventory_mineral = 0.0

        # Order pipeline limit (number of outstanding shipments addressed
        # to this retailer). On-order quantity is computed by scanning
        # transport in_transit, so no separate pending_orders list is
        # needed.
        self.max_pending = model.config.get("retailer_max_pending_orders", 3)

        # Tracking
        self.sold_this_step = 0
        self.stockouts = 0
        self.total_sales = 0
        self.received_this_step = 0
        self.received_mineral_this_step = 0

    def step(self):
        """Execute one time step of retailer behavior."""
        self.sold_this_step = 0
        self.received_this_step = 0
        self.received_mineral_this_step = 0

        # 1. Sales to consumers happen reactively via sell_to_consumer().
        # 2. Check inventory and reorder if needed.
        self._check_and_reorder()

    def _pending_inbound(self):
        """Iterate shipments of finished goods currently in transit to this retailer."""
        for transport in self.model.transport_agents:
            for shipment in transport.in_transit:
                if (shipment.get('destination') is self
                        and shipment.get('material') == 'product'):
                    yield shipment

    def _check_and_reorder(self):
        pending = list(self._pending_inbound())
        on_order = sum(s['quantity'] for s in pending)
        inventory_position = self.inventory + on_order

        if inventory_position <= self.reorder_point and len(pending) < self.max_pending:
            self._place_order()

    def _place_order(self):
        """Place an order with manufacturers anywhere in the world.

        Iterates manufacturers (random order so no manufacturer is
        starved) and dispatches finished-goods shipments through the
        routing engine. The shipment lands in this retailer's inventory
        when the transport agent delivers.
        """
        manufacturers = list(self.model.manufacturers)
        self.model.random_state.shuffle(manufacturers)
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

            # Dispatch via transport from manufacturer.country to self.country.
            self.model.dispatch_shipment(
                material_type='product',
                quantity=actual,
                origin_country=manufacturer.country,
                dest_country=self.country,
                destination=self,
                mineral_tons=mineral,
            )
            remaining -= actual

    def receive_shipment(self, material_type, quantity, mineral_tons=0.0,
                         origin_jurisdiction=''):
        """Accept finished-goods delivery from a TransportAgent."""
        if quantity <= 0:
            return
        if material_type != 'product':
            return
        self.inventory += quantity
        self.inventory_mineral += mineral_tons
        self.received_this_step += quantity
        self.received_mineral_this_step += mineral_tons

    def sell_to_consumer(self, amount):
        """Sell to a consumer in this country.

        Returns (units_sold, mineral_tons_embedded) so as-built intensity
        travels with the goods to the EOL pool.
        """
        if self.inventory <= 0:
            self.stockouts += 1
            return 0.0, 0.0

        sold = min(amount, self.inventory)
        mineral_share = (
            self.inventory_mineral * (sold / self.inventory)
            if self.inventory > 0 else 0.0
        )
        self.inventory -= sold
        self.inventory_mineral = max(0.0, self.inventory_mineral - mineral_share)
        self.sold_this_step += sold
        self.total_sales += sold
        return sold, mineral_share

    def get_available_inventory(self):
        """Get current available inventory (units)."""
        return self.inventory
