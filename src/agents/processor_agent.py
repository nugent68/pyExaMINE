"""
ProcessorAgent: Converts raw ore to processed mineral.
Maintains inventory and sells to manufacturers.
"""

from mesa import Agent

from ..config.overrides import cfg_for, procurement_avoid_list


class ProcessorAgent(Agent):
    """Agent representing a processing facility that converts ore to pure mineral."""

    def __init__(self, unique_id, model, country, facility,
                 conversion_efficiency, energy_cost, capacity):
        """Initialize a ProcessorAgent.

        Args:
            unique_id: Unique identifier
            model: Model instance
            country: Host country (used by routing engine)
            facility: Facility name (e.g. 'Tianqi-Sichuan')
            conversion_efficiency: Fraction of ore converted to pure mineral (0-1)
            energy_cost: Cost per ton to process ($/ton)
            capacity: Output capacity (tons of post-conversion mineral/step)
                as reported by the source CSVs (USGS / company nameplate).
                The agent stores this as ``output_capacity`` and derives
                input-throughput by dividing by ``conversion_efficiency``,
                so an 82%-yield facility rated at 40 kt/yr Li output can
                feed ~48.8 kt/yr of contained-Li input.
        """
        super().__init__(unique_id, model)

        # Identity
        self.country = country
        self.facility = facility
        self.label = f"{country}/{facility}"

        # Core attributes
        self.conversion_efficiency = conversion_efficiency
        self.energy_cost = energy_cost
        # CSV reports capacity as post-conversion *output* tonnes/step (per
        # the file headers, e.g. "contained Li metal output capacity").
        # Internally we work in input-throughput because the per-step
        # bottleneck is how much feedstock the plant can take. Output =
        # input * efficiency.
        self.output_capacity = capacity
        self.capacity = (
            capacity / conversion_efficiency
            if conversion_efficiency > 0 else capacity
        )

        # Inventory
        self.inventory = 0        # Processed mineral (post-conversion tonnes)
        self.raw_ore_buffer = 0   # Pre-conversion tonnes awaiting processing

        # Safety stock (don't sell below this level), in PROCESSED tonnes.
        # N weeks of *output* capacity.
        safety_weeks = cfg_for(model, country, "processor_safety_stock_weeks", 2.0)
        self.safety_stock = self.output_capacity * safety_weeks

        # Inventory ceiling: stop purchasing ore once expected post-
        # processing inventory would exceed this cap. Modeled as weeks
        # of output capacity. Without this, processors buy and process
        # indefinitely when downstream demand collapses, masking the
        # supply/demand price signal and producing unbounded inventory.
        cap_weeks = cfg_for(model, country, "processor_inventory_cap_weeks", 8.0)
        self.inventory_cap = self.output_capacity * cap_weeks

        # Tracking
        self.processed_this_step = 0
        self.purchased_this_step = 0
        self.sold_this_step = 0
        self.recycled_received_this_step = 0

        # Geopolitical-disruption state. >0 means the processor is
        # off-line for the remaining number of steps (smelter outage,
        # power curtailment, sanctions on the operator, etc.). While
        # disrupted: no purchasing, no processing, no selling. Inbound
        # shipments still arrive (ore can stockpile at the gate); only
        # the facility's own actions stop.
        self.disruption_counter = 0

        # Inbound-shipment counters maintained by TransportAgent as
        # shipments are accepted / delivered / dropped. Replaces the
        # previous O(transports * shipments) scan over every transport's
        # in_transit list to compute "pending inbound" quantities.
        # Material types observed for processors: 'ore', 'recycled',
        # 'processed' (the latter never used, but the dict is keyed
        # generically).
        self._inbound_qty = {}     # material -> running total tons
        self._inbound_count = {}   # material -> count of in-flight shipments

        # Cache config values used in step() (immutable post-init).
        self._cfg_steps_per_year = int(cfg_for(model, country, "steps_per_year", 52))
        self._cfg_capacity_growth_per_year = float(
            cfg_for(model, country, "processor_capacity_growth_per_year", 0.0)
        )

    def step(self):
        """Execute one time step of processor behavior."""
        self.processed_this_step = 0
        self.purchased_this_step = 0
        self.sold_this_step = 0
        self.recycled_received_this_step = 0

        # Tick down any active disruption. While disrupted, the
        # facility takes no action (no purchasing, no processing).
        # Inbound shipments still arrive via receive_shipment -- ore
        # stockpiles at the gate, matching real-world smelter outages
        # where offtake contracts continue but throughput halts.
        if self.disruption_counter > 0:
            self.disruption_counter -= 1
            return

        # 0. Capacity expansion. Mines and manufacturers already grow
        #    with the demand curve; without symmetric processor growth a
        #    24-year run that ramps demand ~10x leaves processors stuck
        #    at 2024 levels and creates an artificial mid-stream
        #    bottleneck. Growth is compounded per step (sticky like
        #    mines, since refining capacity isn't easily torn down).
        if self._cfg_capacity_growth_per_year > 0:
            mult = 1.0 + self._cfg_capacity_growth_per_year / self._cfg_steps_per_year
            self.capacity *= mult
            self.output_capacity *= mult
            self.safety_stock *= mult
            self.inventory_cap *= mult

        # 1. Purchase ore from mines
        self._purchase_ore()

        # 2. Process ore to pure mineral
        self._process_ore()

        # 3. Sell to manufacturers (manufacturers pull via accept_order)

    def _purchase_ore(self):
        """Purchase ore from available mines, cheapest first.

        Purchased ore is dispatched to a transport agent (the route
        table picks the mode -- typically ship for cross-region, rail
        for overland Asia <-> Europe) and only lands in raw_ore_buffer
        when the shipment arrives. This means in_transit_ore +
        raw_ore_buffer is the relevant "pipeline" quantity for capacity
        planning, not raw_ore_buffer alone.
        """
        ranked_mines = self.model.mines_sorted_by_cost
        if not ranked_mines:
            return

        # Procurement-avoid filter. If this processor's country has a
        # policy override that forbids buying from listed (or currently
        # embargoed) origins, drop those mines from the merit order.
        # Empty when no override is set, so default behaviour is
        # unchanged. Computed once per step here -- the avoid list is
        # short and the membership check below is O(1).
        avoid = procurement_avoid_list(self.model, self.country)
        if avoid:
            ranked_mines = [m for m in ranked_mines if m.country not in avoid]
            if not ranked_mines:
                return

        in_transit = self._inbound_qty.get('ore', 0.0)

        # Inventory backpressure: how much *more* processed mineral could
        # we tolerate before hitting the inventory ceiling? Convert that
        # back into ore-input terms via conversion_efficiency. The
        # raw_ore_buffer + in_transit have not been processed yet but
        # will be, so they count against the ceiling at full efficiency.
        #
        # Backpressure alone is sufficient -- it caps the *eventual*
        # processed inventory at inventory_cap, naturally limiting how
        # much ore can be in the pipeline. The previous version also
        # imposed a per-step throughput cap (`capacity - raw_ore -
        # in_transit`) that double-counted in_transit and clipped the
        # pipeline to ~1 week of capacity. With a 3-week ore lead time
        # from mines, that throttled actual processing to
        # capacity / (1 + lead_time) ~ 25-30% of nameplate -- so
        # processors couldn't keep manufacturers fed even though mines
        # were producing 4x demand. Removing the per-step cap lets the
        # pipeline grow naturally to ~ (inventory_cap_weeks -
        # safety_weeks) of input throughput.
        eff = self.conversion_efficiency
        committed_processed = self.inventory + (self.raw_ore_buffer + in_transit) * eff
        headroom_processed = max(0.0, self.inventory_cap - committed_processed)
        backpressure_room = headroom_processed / eff if eff > 0 else 0.0

        remaining_capacity = max(0.0, backpressure_room)

        for mine in ranked_mines:
            if remaining_capacity <= 0:
                break

            available = mine.get_available_supply()
            if available <= 0:
                continue

            desired = min(remaining_capacity, available)
            actual = mine.sell_production(desired)
            if actual <= 0:
                continue

            self.purchased_this_step += actual
            remaining_capacity -= actual

            self.model.dispatch_shipment(
                material_type='ore',
                quantity=actual,
                origin_country=mine.country,
                dest_country=self.country,
                destination=self,
                mineral_tons=0.0,
            )

    def headroom_for_recycled(self):
        """Inventory headroom available for new recycled deliveries (post-
        conversion tonnes).

        Recycled mineral lands directly in ``inventory`` (no conversion
        loss), so it counts 1:1 against ``inventory_cap``. Existing
        inventory + raw_ore_buffer (x efficiency) + ore in transit (x
        efficiency) + recycled in transit (x 1) all reserve cap space;
        what's left is the headroom a recycler can dispatch into.
        Returns 0.0 if the processor is already at or above its cap.

        ``_inbound_qty`` is maintained by TransportAgent on accept /
        deliver / drop, replacing what used to be an O(transports x
        shipments) scan over every transport's in_transit list.
        """
        eff = self.conversion_efficiency
        in_transit_ore = self._inbound_qty.get('ore', 0.0)
        in_transit_recycled = self._inbound_qty.get('recycled', 0.0)
        committed = (
            self.inventory
            + self.raw_ore_buffer * eff
            + in_transit_ore * eff
            + in_transit_recycled
        )
        return max(0.0, self.inventory_cap - committed)

    def _process_ore(self):
        """Convert raw ore to processed mineral."""
        if self.raw_ore_buffer <= 0:
            return

        to_process = min(self.raw_ore_buffer, self.capacity)
        processed = to_process * self.conversion_efficiency
        # Yield-loss tonnage (1 - efficiency of input) is a real
        # physical loss to tailings; book it on the model so the
        # mass-balance diagnostic stays consistent.
        self.model.cumulative_processor_yield_loss += (to_process - processed)

        self.raw_ore_buffer -= to_process
        self.inventory += processed
        self.processed_this_step = processed

    def receive_shipment(self, material_type, quantity, mineral_tons=0.0,
                         origin_jurisdiction=''):
        """Accept a delivery from a TransportAgent.

        For raw ore (material_type='ore'), the quantity is contained
        mineral tons and lands in raw_ore_buffer for processing.
        For already-processed mineral (material_type='processed'), it
        bypasses conversion and goes straight to inventory.
        For recycled mineral (material_type='recycled'), it routes
        through receive_recycled so the recycled-supply tracking is
        preserved.
        """
        if quantity <= 0:
            return
        if material_type == 'ore':
            self.raw_ore_buffer += quantity
        elif material_type == 'processed':
            self.inventory += quantity
        elif material_type == 'recycled':
            self.receive_recycled(quantity)
        else:
            # Unknown material type -- discard rather than corrupt buffers.
            return

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
            Amount that can be fulfilled. Disrupted processors return
            0 -- offtake stops alongside processing during an outage.
        """
        if self.disruption_counter > 0:
            return 0
        return self.sell_inventory(amount)

    def apply_geopolitical_disruption(self, duration):
        """Apply a geopolitical disruption to this processor.

        Uses ``max(...)`` so a stacked event (e.g. embargo on the host
        country plus a smelter-specific incident) doesn't shorten the
        longer outage window.
        """
        if duration <= 0:
            return
        self.disruption_counter = max(self.disruption_counter, int(duration))
