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

    def __init__(self, unique_id, model, mode, cost_per_unit, lead_time, capacity):
        """Initialize a TransportAgent.

        Args:
            unique_id: Unique identifier
            model: Model instance
            mode: Transport mode ('ship', 'rail', 'truck')
            cost_per_unit: Cost per ton ($/ton)
            lead_time: Delivery delay in steps
            capacity: Maximum capacity (tons/step accepted; soft cap)
        """
        super().__init__(unique_id, model)

        # Core attributes
        self.mode = mode
        self.cost_per_unit = cost_per_unit
        self.lead_time = lead_time
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

        # Iterate over a copy so we can mutate in_transit safely.
        for shipment in list(self.in_transit):
            if shipment['arrival_step'] > current_step:
                continue

            origin = shipment.get('origin_jurisdiction', '')
            dest = shipment.get('dest_jurisdiction', '')
            if (origin in self.disrupted_jurisdictions or
                    dest in self.disrupted_jurisdictions):
                shipment['arrival_step'] += 1
                continue

            destination = shipment.get('destination')
            if destination is None or not hasattr(destination, 'receive_shipment'):
                # No one to receive -- drop the shipment but record it.
                self.in_transit.remove(shipment)
                continue

            destination.receive_shipment(
                material_type=shipment['material'],
                quantity=shipment['quantity'],
                mineral_tons=shipment.get('mineral', 0.0),
                origin_jurisdiction=origin,
            )
            self.delivered_this_step += shipment['quantity']
            self.total_delivered += shipment['quantity']
            self.in_transit.remove(shipment)

    def accept_shipment(self, material_type, quantity, destination,
                        origin_jurisdiction='', dest_jurisdiction='',
                        mineral_tons=0.0):
        """Accept a new shipment for delivery after lead_time steps.

        Args:
            material_type: 'ore' or 'processed'
            quantity: Amount to ship (tons; for ore this is contained
                mineral tons per the model's USGS convention)
            destination: Agent that will receive the shipment (must
                implement receive_shipment(material_type, quantity,
                mineral_tons, origin_jurisdiction))
            origin_jurisdiction: Country the goods are leaving
            dest_jurisdiction: Country the goods are arriving in
            mineral_tons: Embedded mineral content (only meaningful for
                processed mineral that already carries an as-built
                intensity through the chain)

        Returns:
            True if accepted (we currently always accept; capacity is a
            soft cap recorded for diagnostics only).
        """
        if quantity <= 0:
            return False

        shipment = {
            'material': material_type,
            'quantity': quantity,
            'mineral': mineral_tons,
            'destination': destination,
            'origin_jurisdiction': origin_jurisdiction,
            'dest_jurisdiction': dest_jurisdiction,
            'arrival_step': self.model.current_step + self.lead_time,
            'cost': self.cost_per_unit * quantity,
        }

        self.in_transit.append(shipment)
        self.accepted_this_step += quantity
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
