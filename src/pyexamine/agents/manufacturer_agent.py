"""
ManufacturerAgent: Produces goods using processed minerals.
Implements substitution investment when prices remain high.
"""

from mesa import Agent

from ..config.overrides import cfg_for, price_for, procurement_avoid_list


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
        self.target_inventory_weeks = cfg_for(
            model, country, "manufacturer_target_inventory_weeks", 4
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

        # Cache config values referenced from step() / helpers (config
        # is immutable post-construction).
        c = country
        self._cfg_substitution_threshold = float(
            cfg_for(model, c, "substitution_price_threshold",
                    model.initial_price * 1.5)
        )
        self._cfg_substitution_revert_threshold = float(
            cfg_for(model, c, "substitution_revert_threshold",
                    model.initial_price * 0.667)
        )
        self._cfg_substitution_trigger_steps = int(
            cfg_for(model, c, "substitution_trigger_steps", 10)
        )
        self._cfg_substitution_revert_trigger_steps = int(
            cfg_for(model, c, "substitution_revert_trigger_steps", 26)
        )
        self._cfg_substitution_rate = float(
            cfg_for(model, c, "substitution_rate", 0.05)
        )
        self._cfg_substitution_revert_rate = float(
            cfg_for(model, c, "substitution_revert_rate", 0.03)
        )
        self._cfg_max_substitution = float(
            cfg_for(model, c, "max_substitution", 0.30)
        )
        self._cfg_order_rate = float(
            cfg_for(model, c, "manufacturer_order_rate", 0.5)
        )

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
        """Target input inventory in MINERAL TONS.

        Uses ``initial_mineral_intensity`` (the as-designed Li-per-product
        amount) rather than the *current* substituted-down intensity so
        the manufacturer's input buffer stays sized to the original
        production footprint regardless of any substitution that has
        happened in-flight. Real-world manufacturers that switch to a
        lower-Li chemistry (e.g. NMC -> LFP) do not materially shrink
        their precursor warehouses; they re-purpose the same physical
        buffer for a different precursor. Coupling the buffer to the
        post-substitution intensity caused a counterintuitive policy
        result in the Stage 2 v2 sweep where aggressive substitution
        increased cumulative unfulfilled demand, because the smaller
        buffer left manufacturers more vulnerable to subsequent supply
        pinches. Production (in _produce_goods) still uses the
        post-substitution ``mineral_intensity`` so the per-product
        Li savings remain in effect.
        """
        return (
            self.effective_capacity
            * self.target_inventory_weeks
            * self.initial_mineral_intensity
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

        Uses the regional ``price_for(country)`` so a country with a
        ``price_spread`` override sees a wedged price -- US under IRA
        sees a higher price and substitutes earlier, while non-IRA
        manufacturers respond to the unmultiplied global price. With
        no spread the result is identical to the global current_price.
        """
        price = price_for(self.model, self.country)
        if price > self._cfg_substitution_threshold:
            self.high_price_counter += 1
            self.low_price_counter = max(0, self.low_price_counter - 1)
        elif price < self._cfg_substitution_revert_threshold:
            self.low_price_counter += 1
            self.high_price_counter = max(0, self.high_price_counter - 1)
        else:
            self.high_price_counter = max(0, self.high_price_counter - 1)
            self.low_price_counter = max(0, self.low_price_counter - 1)

        if self.high_price_counter >= self._cfg_substitution_trigger_steps:
            self._invest_in_substitution()
            self.high_price_counter = 0
        elif self.low_price_counter >= self._cfg_substitution_revert_trigger_steps:
            self._revert_substitution()
            self.low_price_counter = 0

    def _invest_in_substitution(self):
        """Invest in R&D to reduce mineral intensity."""
        if self.substitution_investment < self._cfg_max_substitution:
            self.substitution_investment = min(
                self.substitution_investment + self._cfg_substitution_rate,
                self._cfg_max_substitution,
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
        self.substitution_investment = max(
            0.0, self.substitution_investment - self._cfg_substitution_revert_rate,
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

        order_amount = minerals_needed * self._cfg_order_rate

        processors = list(self.model.processors)
        # Procurement-avoid filter: drop processors in countries this
        # manufacturer's policy refuses to source from (empty by
        # default; populated for US manufacturers when a policy file
        # is loaded).
        avoid = procurement_avoid_list(self.model, self.country)
        if avoid:
            processors = [p for p in processors if p.country not in avoid]
            if not processors:
                return
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
