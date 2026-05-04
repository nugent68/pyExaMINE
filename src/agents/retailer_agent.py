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

    The (s, Q) policy parameters track an EWMA of *realised* consumer
    requests rather than a price-blind anchor:

        demand_ewma <- (1-alpha) * demand_ewma + alpha * last_step_requests

    where ``last_step_requests`` is the sum of every consumer's purchase
    request to this retailer in the previous step (= consumer
    current_demand limited to local retailers), and ``alpha`` defaults
    to 0.05 (~13-week half-life). reorder_point and order_quantity are
    properties that recompute from this EWMA each access.

    The previous policy multiplied the 2024 baseline by
    demand_growth_factor() -- correct for the long-run demand trend
    but blind to consumer price elasticity. During an embargo or
    chokepoint episode, consumers cut purchases (price spike + hard
    threshold) yet the retailer kept ordering at the full anchored
    rate, so inventory built mid-shock and the (s,Q) cycle drifted.
    The EWMA tracks both the trend (via realised volume growing with
    demand) and the elasticity (via realised volume contracting with
    price spikes) without explicitly knowing either.
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
        # with demand_ewma each access).
        self.base_country_demand = base_country_demand
        self.reorder_mult = reorder_mult
        self.order_mult = order_mult

        # Realised-demand tracker. Initialized to the 2024 baseline so
        # the policy sizes correctly from step 0 (before any consumer
        # has had a chance to record a request). _requests_this_step
        # accumulates inside sell_to_consumer; the EWMA folds it in at
        # the start of the next retailer.step().
        self.demand_ewma = base_country_demand
        self._requests_this_step = 0.0

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

        # Inbound-shipment counters maintained by TransportAgent. For
        # retailers the only inbound material type is 'product'.
        self._inbound_qty = {}     # material -> running total units
        self._inbound_count = {}   # material -> count of in-flight shipments

    @property
    def current_country_demand(self):
        """Current expected per-step product demand for this country.

        Returns the EWMA of realised consumer requests. With a 13-week
        half-life (alpha ~ 0.05) this is slow enough to ignore single-
        week noise but fast enough to follow real elasticity-driven
        contractions during multi-month price spikes.
        """
        return self.demand_ewma

    @property
    def reorder_point(self):
        return self.current_country_demand * self.reorder_mult

    @property
    def order_quantity(self):
        return self.current_country_demand * self.order_mult

    def step(self):
        """Execute one time step of retailer behavior."""
        # Fold the previous step's realised consumer requests into the
        # demand EWMA before resetting per-step counters. Consumers run
        # in tier 6 (after retailers in tier 5), so the requests we
        # read here were accumulated by the consumers' last visit --
        # i.e. the previous step's realised demand.
        alpha = float(self.model.config.get("retailer_demand_ewma_alpha", 0.05))
        self.demand_ewma = (
            (1.0 - alpha) * self.demand_ewma + alpha * self._requests_this_step
        )
        self._requests_this_step = 0.0

        self.sold_this_step = 0
        self.received_this_step = 0
        self.received_mineral_this_step = 0

        # 1. Sales to consumers happen reactively via sell_to_consumer().
        # 2. Check inventory and reorder if needed.
        self._check_and_reorder()

    def _check_and_reorder(self):
        # Inbound counters are maintained by TransportAgent so this is
        # O(1) instead of an O(transports x shipments) scan.
        on_order = self._inbound_qty.get('product', 0.0)
        n_pending = self._inbound_count.get('product', 0)
        inventory_position = self.inventory + on_order

        if inventory_position <= self.reorder_point and n_pending < self.max_pending:
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
        # Track the realised request volume (whether or not we can
        # fulfill it) so the demand EWMA reflects what consumers
        # actually wanted, not just what we managed to ship.
        if amount > 0:
            self._requests_this_step += amount
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
