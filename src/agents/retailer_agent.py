"""
RetailerAgent: Manages inventory and sells to local consumers.
Implements (s, Q) inventory policy. Orders from manufacturers anywhere
in the world via the model's routing engine; the inbound shipment moves
through a TransportAgent with route-specific lead time and chokepoint
delays.
"""

from mesa import Agent


class RetailerAgent(Agent):
    """Agent representing a country's retail aggregate using (s,Q) policy.

    The (s, Q) policy parameters scale with the demand-trajectory growth
    factor: ``reorder_point`` and ``order_quantity`` are properties that
    recompute each access from the *current* anchored country demand
    (= 2024 baseline x demand_growth_factor), not the 2024 baseline
    alone. Without this scaling the policy is sized for the 2024 demand
    level and starves consumers as demand grows -- a 10x Li demand
    ramp by 2050 would leave the retailer ordering 10% of what's
    needed and the fulfillment rate would collapse to ~10%.

    The anchored growth factor is used (not the consumer's
    price-elasticity-modulated current_demand) so the (s, Q) policy
    sizes off the underlying demand trend rather than reacting to
    short-term price spikes.
    """

    def __init__(self, unique_id, model, country, base_country_demand,
                 reorder_mult, order_mult):
        """Initialize a RetailerAgent.

        Args:
            unique_id: Unique identifier
            model: Model instance
            country: Host country (used by routing engine when ordering
                from manufacturers and serving local consumers)
            base_country_demand: 2024 baseline per-step product demand
                from this country (units/step). Scales over time via
                ``model.demand_growth_factor()``.
            reorder_mult: ``reorder_point = current_country_demand *
                reorder_mult`` (typically 2-3 weeks of demand).
            order_mult: ``order_quantity = current_country_demand *
                order_mult`` (typically 3-4 weeks of demand).
        """
        super().__init__(unique_id, model)

        # Identity
        self.country = country
        self.label = f"{country}/retail"

        # Inventory policy parameters (sizing inputs; the actual
        # reorder_point / order_quantity are properties that scale
        # with the demand growth factor each access).
        self.base_country_demand = base_country_demand
        self.reorder_mult = reorder_mult
        self.order_mult = order_mult

        # Inventory. Warm-start is one current-step order_quantity, which
        # at construction (step 0) equals the 2024-baseline order
        # quantity since demand_growth_factor(0) == 1.
        self.inventory = self.order_quantity
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

    @property
    def current_country_demand(self):
        """Current expected per-step product demand for this country.

        Uses the model's anchored demand-growth multiplier rather than
        the consumer's instantaneous current_demand so the (s, Q)
        sizing tracks the underlying demand trend instead of getting
        feedback-distorted by transient price spikes.
        """
        return self.base_country_demand * self.model.demand_growth_factor()

    @property
    def reorder_point(self):
        return self.current_country_demand * self.reorder_mult

    @property
    def order_quantity(self):
        return self.current_country_demand * self.order_mult

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
        """Place an order with manufacturers, region-preferenced.

        Tries same-country manufacturers first, then same-region, then
        the rest of the world. Within each tier, manufacturers are
        shuffled so no single firm is systematically starved. This
        mirrors the recycler's region-preferenced sourcing.

        Without region preferencing, orders are split across many
        long-lead-time foreign manufacturers (e.g. a Chinese retailer
        random-order would source ~50% from China mfr but the rest from
        Europe/Korea/Japan with 3-7 week lead times). The on_order
        sum then treats those long-lead shipments as effectively
        present, suppressing reorders, while real inventory drains to
        zero in a 1-2 step window. Region preferencing keeps the
        actual lead time short so the (s, Q) cycle works as designed.
        """
        from ..data.routing import COUNTRY_REGION

        manufacturers = list(self.model.manufacturers)
        my_region = COUNTRY_REGION.get(self.country, 'Other')

        local = [m for m in manufacturers if m.country == self.country]
        regional = [
            m for m in manufacturers
            if m.country != self.country
            and COUNTRY_REGION.get(m.country, 'Other') == my_region
        ]
        global_rest = [
            m for m in manufacturers
            if m.country != self.country
            and COUNTRY_REGION.get(m.country, 'Other') != my_region
        ]
        for tier in (local, regional, global_rest):
            self.model.random_state.shuffle(tier)

        remaining = self.order_quantity
        for tier in (local, regional, global_rest):
            if remaining <= 0:
                break
            for manufacturer in tier:
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
