"""StrategicReserveAgent.

A national strategic-reserve mechanism modelled after the US SPR for
oil: builds up stock when prices are low, releases stock when prices
spike or a relevant peer is embargoed. There is no equivalent in the
base model -- this agent is opt-in via the ``strategic_reserve`` block
inside a country's ``country_overrides`` policy.

Mechanism (deterministic, three thresholds + two rates):

  Build  if price < buy_below_price  and reserve < capacity:
    Pull up to buy_rate_tons_per_step from the cheapest available
    processed-mineral inventory in this reserve's country (same
    sell path manufacturers use). Domestic delivery is instant; no
    transport leg.

  Release  if (price > release_above_price) OR
              (any country in release_on_embargo_of is currently
              embargoed) and reserve > 0:
    Push up to release_rate_tons_per_step into the input_inventory
    of manufacturers in this reserve's country, allocated in
    proportion to each manufacturer's remaining headroom against
    its target_inventory.

  Idle: hold inventory unchanged.

The reserve runs between the processor and manufacturer tiers so a
release lands in manufacturer input_inventory before manufacturers'
production step on the same tick; a build runs after processors have
produced but before manufacturers attempt to order from them.

Mass-balance: ``reserve`` is included in ``total_mineral_in_system()``
so the conservation diagnostic remains tight. No yield loss is booked
(stockpiled mineral doesn't degrade in this model; carrying cost is
not represented).
"""

from mesa import Agent

from ..config.overrides import price_for


class StrategicReserveAgent(Agent):
    """Country-level strategic reserve agent."""

    def __init__(self, unique_id, model, country, capacity_tons,
                 buy_below_price, release_above_price,
                 release_on_embargo_of, buy_rate_tons_per_step,
                 release_rate_tons_per_step, initial_stock_tons=0.0):
        """Initialize a StrategicReserveAgent.

        Args:
            unique_id: Unique identifier
            model: Model instance
            country: Host country (must match domestic processor /
                manufacturer agents' .country)
            capacity_tons: Maximum stock the reserve can hold (mineral
                tonnes).
            buy_below_price: $/tonne threshold below which the reserve
                buys from domestic processors.
            release_above_price: $/tonne threshold above which the
                reserve releases to domestic manufacturers.
            release_on_embargo_of: list of peer-country names; if any
                appears in ``model.active_embargoes`` the reserve
                releases regardless of price.
            buy_rate_tons_per_step: max purchase rate per step.
            release_rate_tons_per_step: max release rate per step.
            initial_stock_tons: starting reserve stock (default 0 --
                build-only operation).
        """
        super().__init__(unique_id, model)

        self.country = country
        self.label = f"{country}/strategic_reserve"

        self.capacity = float(capacity_tons)
        self.buy_below_price = float(buy_below_price)
        self.release_above_price = float(release_above_price)
        self.release_on_embargo_of = list(release_on_embargo_of or [])
        self.buy_rate = float(buy_rate_tons_per_step)
        self.release_rate = float(release_rate_tons_per_step)

        self.reserve = min(float(initial_stock_tons), self.capacity)

        # Per-step tracking (exposed on the model for diagnostics).
        self.bought_this_step = 0.0
        self.released_this_step = 0.0

        # Cached domestic-tier lists. Country is immutable post-
        # construction so the membership of these tiers never changes.
        self._cached_domestic_processors = None
        self._cached_domestic_manufacturers = None

    def _domestic_processors(self):
        if self._cached_domestic_processors is None:
            self._cached_domestic_processors = [
                p for p in self.model.processors if p.country == self.country
            ]
        return self._cached_domestic_processors

    def _domestic_manufacturers(self):
        if self._cached_domestic_manufacturers is None:
            self._cached_domestic_manufacturers = [
                m for m in self.model.manufacturers if m.country == self.country
            ]
        return self._cached_domestic_manufacturers

    def _should_release(self):
        if self.reserve <= 0:
            return False
        # Compare against the reserve's regional price (US reserve sees
        # the US-wedged price, etc.) so the buy/release thresholds are
        # quoted in the same units the reserve actually experiences.
        if price_for(self.model, self.country) > self.release_above_price:
            return True
        for peer in self.release_on_embargo_of:
            if peer in self.model.active_embargoes:
                return True
        return False

    def _should_build(self):
        if self.reserve >= self.capacity:
            return False
        return price_for(self.model, self.country) < self.buy_below_price

    def step(self):
        """Execute one time step.

        Release takes precedence over build: in a crisis we never
        accumulate while we are also bleeding stock. Build and release
        are mutually exclusive within a step.
        """
        self.bought_this_step = 0.0
        self.released_this_step = 0.0

        if self._should_release():
            self._release()
        elif self._should_build():
            self._build()

    def _release(self):
        """Push stock into domestic manufacturers' input_inventory.

        Allocated in proportion to each manufacturer's remaining
        headroom against target_inventory so a near-full manufacturer
        doesn't soak up the release while a starved one stays empty.
        """
        manufacturers = self._domestic_manufacturers()
        if not manufacturers:
            return

        headrooms = [
            max(0.0, m.target_inventory - m.input_inventory)
            for m in manufacturers
        ]
        total_headroom = sum(headrooms)
        if total_headroom <= 0:
            return

        to_release = min(self.reserve, self.release_rate, total_headroom)
        if to_release <= 0:
            return

        released = 0.0
        for mfr, headroom in zip(manufacturers, headrooms):
            if headroom <= 0:
                continue
            share = to_release * (headroom / total_headroom)
            share = min(share, headroom)
            if share <= 0:
                continue
            mfr.receive_shipment(
                material_type='processed', quantity=share,
                mineral_tons=share, origin_jurisdiction=self.country,
            )
            released += share

        self.reserve = max(0.0, self.reserve - released)
        self.released_this_step = released

    def _build(self):
        """Buy from domestic processors at the global price.

        Iterates processors in CSV order with a small RNG shuffle to
        avoid systematic front-of-queue bias. Stops when either the
        per-step rate is exhausted, capacity is hit, or the domestic
        processor inventory above safety stock is exhausted.
        """
        processors = self._domestic_processors()
        if not processors:
            return

        # Shuffle to balance load across the (typically 1-2) domestic
        # processors. Cheap and matches the existing within-tier
        # ordering convention.
        order = list(processors)
        self.model.random_state.shuffle(order)

        headroom = max(0.0, self.capacity - self.reserve)
        remaining = min(self.buy_rate, headroom)
        if remaining <= 0:
            return

        bought = 0.0
        for processor in order:
            if remaining <= 0:
                break
            available = processor.get_available_inventory()
            if available <= 0:
                continue
            desired = min(remaining, available)
            actual = processor.accept_order(desired)
            if actual <= 0:
                continue
            bought += actual
            remaining -= actual

        self.reserve = min(self.capacity, self.reserve + bought)
        self.bought_this_step = bought
