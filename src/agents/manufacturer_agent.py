"""
ManufacturerAgent: Produces goods using processed minerals.
Implements substitution investment when prices remain high.
"""

from mesa import Agent


class ManufacturerAgent(Agent):
    """Agent representing a manufacturer that uses minerals to produce goods."""

    def __init__(self, unique_id, model, country, mineral_intensity,
                 production_capacity):
        """Initialize a ManufacturerAgent.

        Args:
            unique_id: Unique identifier
            model: Model instance
            country: Host country (used by routing engine when ordering
                inputs from processors)
            mineral_intensity: Tons of mineral per unit of product
            production_capacity: Maximum production (units/step)
        """
        super().__init__(unique_id, model)

        # Identity
        self.country = country
        self.facility = f"{country} aggregate"
        self.label = f"{country}/manufacturers"

        # Core attributes
        self.initial_mineral_intensity = mineral_intensity
        self.mineral_intensity = mineral_intensity
        self.production_capacity = production_capacity

        # Inventory
        self.input_inventory = 0   # Tons of processed mineral
        self.output_inventory = 0  # Units of finished product
        # Mineral content embedded in output_inventory (tons). Tracked
        # alongside units so EOL pool deposits use the *as-built*
        # intensity of each batch rather than re-reading current
        # mineral_intensity (which drifts down with substitution).
        self.output_inventory_mineral = 0.0

        # Target input inventory in MINERAL TONS (= production_capacity in
        # product-units * mineral_intensity tons/unit * weeks of buffer).
        # Previously this was production_capacity * weeks, mixing units
        # with tons -- it overshot by 1/mineral_intensity (~12x for Li,
        # ~20,000x for Pt). Recompute live so substitution shrinks the
        # target as intensity falls.
        self.target_inventory_weeks = self.model.config.get(
            "manufacturer_target_inventory_weeks", 4
        )

        # Substitution tracking. Two sticky counters: high_price_counter
        # accumulates while price > substitution_price_threshold and
        # decrements on dips/recoveries; low_price_counter accumulates
        # while price < substitution_revert_threshold and decrements
        # otherwise. Substitution fires when high_price_counter crosses
        # substitution_trigger_steps; reversion fires when
        # low_price_counter crosses substitution_revert_trigger_steps.
        # Reversion brings substitution_investment back toward 0 (and
        # mineral_intensity back toward initial), modelling the
        # real-world fact that cheap-Li markets see LFP cathodes lose
        # share back to NMC, cheap-Pd markets revert to Pd-rich
        # autocats, etc. Reversion is intentionally slower (longer
        # trigger window, smaller per-cycle rate) than forward
        # substitution to reflect that committing to new chemistry is
        # cheap relative to switching back once production lines are
        # built.
        self.substitution_investment = 0.0
        self.high_price_counter = 0
        self.low_price_counter = 0

        # Tracking
        self.produced_this_step = 0
        self.ordered_this_step = 0

        # Inbound-shipment counters maintained by TransportAgent. For
        # manufacturers the only inbound material type is 'processed'.
        self._inbound_qty = {}     # material -> running total tons
        self._inbound_count = {}   # material -> count of in-flight shipments

    @property
    def effective_capacity(self):
        """Production capacity scaled by the model's demand-growth factor.

        Real manufacturer capacity expands as demand grows; without this
        scaling, a 24-year run that loads a 2024->2050 demand curve would
        leave manufacturers stuck at 2024 capacity, conflating an
        un-modeled capacity-investment lag with the mineral supply
        constraint we actually want to study.
        """
        growth = self.model.demand_growth_factor()
        return self.production_capacity * growth

    @property
    def target_inventory(self):
        """Target input inventory in MINERAL TONS."""
        return (
            self.effective_capacity
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
        """Check price and update substitution / reversion counters.

        Two sticky counters operate in opposite price regions:
        - ``high_price_counter`` increments while price >
          ``substitution_price_threshold`` and decrements otherwise.
          Triggers ``_invest_in_substitution`` at
          ``substitution_trigger_steps``.
        - ``low_price_counter`` increments while price <
          ``substitution_revert_threshold`` and decrements otherwise.
          Triggers ``_revert_substitution`` at
          ``substitution_revert_trigger_steps`` (typically 2-3x longer
          than the substitution trigger).

        Both counters are sticky so transient dips/spikes don't trip
        either action -- only sustained pressure (or sustained relief)
        does. The two thresholds form a dead zone (default ~0.67x to
        1.5x of initial price) inside which both counters decay.
        """
        cfg = self.model.config
        threshold = cfg.get(
            "substitution_price_threshold",
            self.model.initial_price * 1.5,
        )
        revert_threshold = cfg.get(
            "substitution_revert_threshold",
            self.model.initial_price * 0.667,
        )
        trigger_steps = cfg.get("substitution_trigger_steps", 10)
        revert_trigger_steps = cfg.get("substitution_revert_trigger_steps", 26)

        price = self.model.current_price
        if price > threshold:
            self.high_price_counter += 1
            self.low_price_counter = max(0, self.low_price_counter - 1)
        elif price < revert_threshold:
            self.low_price_counter += 1
            self.high_price_counter = max(0, self.high_price_counter - 1)
        else:
            self.high_price_counter = max(0, self.high_price_counter - 1)
            self.low_price_counter = max(0, self.low_price_counter - 1)

        if self.high_price_counter >= trigger_steps:
            self._invest_in_substitution()
            self.high_price_counter = 0
        elif self.low_price_counter >= revert_trigger_steps:
            self._revert_substitution()
            self.low_price_counter = 0

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

    def _revert_substitution(self):
        """Revert some prior substitution under sustained low prices.

        Once a manufacturer has invested in substitution, sustained
        low prices on the substituted-from mineral make the original
        chemistry attractive again (e.g., cheap Li makes NMC
        competitive vs LFP, cheap Pd makes Pd-rich autocats attractive
        vs Pt-rich). We reduce ``substitution_investment`` by
        ``substitution_revert_rate`` (typically smaller than
        ``substitution_rate``) and recompute intensity. Cannot revert
        below 0, and a no-op when no substitution has happened yet.
        """
        if self.substitution_investment <= 0:
            return
        revert_rate = self.model.config.get("substitution_revert_rate", 0.03)
        self.substitution_investment = max(
            0.0, self.substitution_investment - revert_rate,
        )
        self.mineral_intensity = (
            self.initial_mineral_intensity * (1 - self.substitution_investment)
        )

    def _order_minerals(self):
        """Order minerals from processors if inventory is low.

        target_inventory and input_inventory are both in mineral tonnes.
        Order up to a fraction of the gap each step to smooth flow.
        Ordered material is dispatched via a TransportAgent; the
        transport mode is determined by the route table (typically
        ship for cross-region, rail for overland Asia <-> Europe), not
        by the manufacturer. Only lands in input_inventory when the
        shipment arrives, so on-order quantities are tracked against
        the gap to avoid over-ordering during the transit window.

        Processor ordering is shuffled each step so no single processor
        is systematically front-of-queue. Without the shuffle the first
        processor in CSV order absorbed every manufacturer's order
        first while later processors (especially smaller / further-
        listed ones) sat idle. Shuffling balances utilisation across
        the processor pool without affecting aggregate dynamics.
        """
        in_transit = self._inbound_qty.get('processed', 0.0)
        effective_inventory = self.input_inventory + in_transit
        minerals_needed = max(0, self.target_inventory - effective_inventory)
        if minerals_needed <= 0:
            return

        order_rate = self.model.config.get("manufacturer_order_rate", 0.5)
        order_amount = minerals_needed * order_rate

        processors = list(self.model.processors)
        self.model.random_state.shuffle(processors)

        for processor in processors:
            if order_amount <= 0:
                break

            available = processor.get_available_inventory()
            if available <= 0:
                continue

            desired = min(order_amount, available)
            actual = processor.accept_order(desired)
            if actual <= 0:
                continue

            self.ordered_this_step += actual
            order_amount -= actual

            self.model.dispatch_shipment(
                material_type='processed',
                quantity=actual,
                origin_country=processor.country,
                dest_country=self.country,
                destination=self,
                mineral_tons=actual,
            )

    def receive_shipment(self, material_type, quantity, mineral_tons=0.0,
                         origin_jurisdiction=''):
        """Accept a delivery from a TransportAgent.

        For 'processed' mineral, quantity is mineral tons and lands in
        input_inventory. Other material types are silently dropped to
        avoid corrupting buffers.
        """
        if quantity <= 0:
            return
        if material_type == 'processed':
            self.input_inventory += quantity

    def _produce_goods(self):
        """Produce finished goods from mineral inputs."""
        if self.input_inventory <= 0 or self.mineral_intensity <= 0:
            return

        max_from_minerals = self.input_inventory / self.mineral_intensity
        max_production = min(max_from_minerals, self.effective_capacity)

        minerals_consumed = max_production * self.mineral_intensity
        self.produced_this_step = max_production
        self.output_inventory += max_production
        self.output_inventory_mineral += minerals_consumed
        self.input_inventory -= minerals_consumed

    def get_available_output(self):
        """Get available finished goods for retailers."""
        return self.output_inventory

    def sell_output(self, amount):
        """Sell finished goods to retailers.

        Args:
            amount: Amount requested (units)

        Returns:
            Tuple (units_sold, mineral_tons_embedded). The mineral
            content is the proportional share of output_inventory_mineral
            for the sold units, so the as-built intensity travels with
            the goods.
        """
        sold = min(amount, self.output_inventory)
        if sold <= 0 or self.output_inventory <= 0:
            return 0.0, 0.0

        mineral_share = self.output_inventory_mineral * (sold / self.output_inventory)
        self.output_inventory -= sold
        self.output_inventory_mineral = max(0.0, self.output_inventory_mineral - mineral_share)
        return sold, mineral_share
