"""
TransportAgent: Moves materials between agents with delays.
Implements realistic transport modes (ship, rail, truck) with different
costs and lead times. Activated last in the per-step tier order so
shipments accepted earlier in the same step are queued (not delivered
zero-lead-time).
"""

from mesa import Agent


class TransportAgent(Agent):
    """Agent representing a transport service that moves materials with delays."""

    def __init__(self, unique_id, model, country, mode, cost_per_unit, capacity):
        """Initialize a TransportAgent.

        Args:
            unique_id: Unique identifier
            model: Model instance
            country: Home country of this transport asset (used to prefer
                origin-country fleet when selecting carriers)
            mode: Transport mode ('ship', 'rail', 'truck')
            cost_per_unit: Cost per ton ($/ton)
            capacity: Maximum capacity (tons/step accepted; soft cap)

        Note: lead_time is no longer per-agent; it is now determined by
        the route table per shipment (e.g., Australia->China is 3 weeks
        regardless of which Australian carrier is picked).
        """
        super().__init__(unique_id, model)

        # Identity
        self.country = country
        self.label = f"{country}/{mode}"

        # Core attributes
        self.mode = mode
        self.cost_per_unit = cost_per_unit
        self.capacity = capacity

        # Transport queue: list of shipments in transit. Each is a dict:
        #   material: 'ore' or 'processed'
        #   quantity: tons (units for finished goods if ever wired up)
        #   mineral: embedded mineral content (tons) -- only meaningful
        #            for processed mineral; 0 for raw ore
        #   destination: agent to deliver to (must implement
        #                receive_shipment)
        #   origin_jurisdiction / dest_jurisdiction: for disruption checks
        #   arrival_step: current_step + lead_time when accepted
        #   cost: total transport cost
        self.in_transit = []

        # Disruption state. Maps jurisdiction -> end_step (exclusive). On
        # each step we drop expired entries so transport recovers
        # automatically when the geopolitical disruption window ends.
        self.disrupted_jurisdictions = {}

        # Tracking
        self.accepted_this_step = 0.0
        self.delivered_this_step = 0.0
        self.total_delivered = 0.0

    @staticmethod
    def _bump_inbound(destination, material, quantity, sign):
        """Adjust the destination's pending-inbound counters by ``sign``.

        The receiver agents (Processor / Manufacturer / Retailer) carry
        ``_inbound_qty`` and ``_inbound_count`` dicts keyed by material
        type so that "how much is on its way to me?" is O(1) instead
        of O(transports x shipments). This helper updates both. Other
        agent types (recyclers, etc.) don't carry the dicts and are
        skipped via duck-typing.
        """
        if destination is None:
            return
        qty_dict = getattr(destination, '_inbound_qty', None)
        if qty_dict is None:
            return
        count_dict = destination._inbound_count
        qty_dict[material] = qty_dict.get(material, 0.0) + sign * quantity
        count_dict[material] = count_dict.get(material, 0) + sign

    def step(self):
        """Execute one time step of transport behavior."""
        self.delivered_this_step = 0.0
        self.accepted_this_step = 0.0

        # 1. Drop expired disruptions so transport recovers automatically.
        current_step = self.model.current_step
        expired = [j for j, end in self.disrupted_jurisdictions.items()
                   if end <= current_step]
        for j in expired:
            del self.disrupted_jurisdictions[j]

        # 2. Deliver shipments that have reached their arrival step,
        #    unless their route is currently disrupted (in which case
        #    push the arrival out by one step and try again next time).
        self._deliver_shipments()

    def _deliver_shipments(self):
        current_step = self.model.current_step
        closed_choke = getattr(self.model, 'closed_chokepoints', set())
        max_deferral = int(
            self.model.config.get("transport_max_deferral_steps", 26)
        )

        # Iterate over a copy so we can mutate in_transit safely.
        for shipment in list(self.in_transit):
            if shipment['arrival_step'] > current_step:
                continue

            origin = shipment.get('origin_jurisdiction', '')
            dest = shipment.get('dest_jurisdiction', '')
            disrupted = (origin in self.disrupted_jurisdictions or
                         dest in self.disrupted_jurisdictions)

            # Per-shipment chokepoint check: if any chokepoint on the
            # route is currently closed, defer one step and retry. This
            # is what implements "Suez closure delays anything routed
            # through Suez until the chokepoint reopens".
            shipment_choke = shipment.get('chokepoints') or []
            choked = any(cp in closed_choke for cp in shipment_choke)

            if disrupted or choked:
                shipment['defer_count'] = shipment.get('defer_count', 0) + 1
                # Cap the deferral so a permanently-closed chokepoint
                # doesn't accumulate in-transit material indefinitely.
                # When the cap binds we drop the shipment and add its
                # mineral content to the model-level lost_in_transit
                # counter so mass-balance diagnostics can see the loss.
                # 26 wks (~6 months) is a defensible default: after
                # half a year of total route closure, real shippers
                # cancel the bill of lading and write off the cargo.
                if shipment['defer_count'] > max_deferral:
                    self._drop_shipment(shipment, reason='deferral_cap')
                    continue
                shipment['arrival_step'] += 1
                continue

            destination = shipment.get('destination')
            if destination is None or not hasattr(destination, 'receive_shipment'):
                # No one to receive -- drop the shipment, count the loss.
                self._drop_shipment(shipment, reason='no_destination')
                continue

            destination.receive_shipment(
                material_type=shipment['material'],
                quantity=shipment['quantity'],
                mineral_tons=shipment.get('mineral', 0.0),
                origin_jurisdiction=origin,
            )
            self.delivered_this_step += shipment['quantity']
            self.total_delivered += shipment['quantity']
            self._bump_inbound(destination, shipment['material'],
                               shipment['quantity'], -1)
            self.in_transit.remove(shipment)

    def _drop_shipment(self, shipment, reason):
        """Drop a shipment and book its mineral content as in-transit loss.

        For ore / processed / recycled shipments the ``quantity`` field
        IS the mineral tonnage; for finished-goods shipments the
        ``mineral`` field carries the embedded mineral content. Either
        way we add the right tonnage to the model's running counter so
        the mass-balance diagnostic stays consistent with reality.
        """
        material = shipment.get('material')
        if material in ('ore', 'processed', 'recycled'):
            mineral_tons = shipment.get('quantity', 0.0)
        else:
            mineral_tons = shipment.get('mineral', 0.0)
        if mineral_tons > 0:
            self.model.lost_in_transit_mineral += mineral_tons
        self._bump_inbound(shipment.get('destination'),
                           shipment.get('material'),
                           shipment.get('quantity', 0.0),
                           -1)
        self.in_transit.remove(shipment)

    def accept_shipment(self, material_type, quantity, destination,
                        origin_jurisdiction='', dest_jurisdiction='',
                        mineral_tons=0.0, chokepoints=None, lead_time=None):
        """Accept a new shipment for delivery after lead_time steps.

        Args:
            material_type: 'ore' or 'processed'
            quantity: Amount to ship (tons)
            destination: Agent that will receive the shipment (must
                implement receive_shipment)
            origin_jurisdiction / dest_jurisdiction: Country names (for
                disruption checks).
            mineral_tons: Embedded mineral content for processed mineral.
            chokepoints: List of chokepoint names traversed by this
                route. The transport agent will defer delivery while any
                of them is currently closed.
            lead_time: Per-shipment lead time in steps. Determined by the
                route table (e.g., Australia->China is 3 weeks); falls
                back to 1 if not supplied.

        Returns True if accepted; capacity is a soft cap, not enforced.
        """
        if quantity <= 0:
            return False

        if lead_time is None:
            lead_time = 1

        shipment = {
            'material': material_type,
            'quantity': quantity,
            'mineral': mineral_tons,
            'destination': destination,
            'origin_jurisdiction': origin_jurisdiction,
            'dest_jurisdiction': dest_jurisdiction,
            'chokepoints': list(chokepoints) if chokepoints else [],
            'arrival_step': self.model.current_step + int(lead_time),
            'defer_count': 0,
            'cost': self.cost_per_unit * quantity,
        }

        self.in_transit.append(shipment)
        self.accepted_this_step += quantity
        self._bump_inbound(destination, material_type, quantity, +1)
        return True

    def apply_disruption(self, jurisdiction, duration):
        """Mark this jurisdiction as disrupted for ``duration`` steps.

        Subsequent self.step() calls will refuse to deliver any shipment
        whose origin or destination is in this set, and will drop the
        entry once current_step >= end_step (no separate cleanup call
        from the model needed).
        """
        if duration <= 0:
            return
        end_step = self.model.current_step + duration
        existing = self.disrupted_jurisdictions.get(jurisdiction, 0)
        self.disrupted_jurisdictions[jurisdiction] = max(existing, end_step)

    def remove_disruption(self, jurisdiction):
        """Clear a disruption immediately (rarely needed; see step())."""
        self.disrupted_jurisdictions.pop(jurisdiction, None)

    def get_total_in_transit(self):
        """Get total quantity currently in transit."""
        return sum(s['quantity'] for s in self.in_transit)
