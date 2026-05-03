"""
TransportAgent: Moves materials between agents with delays.
Implements realistic transport modes (ship, rail, truck) with different costs and lead times.
"""

from mesa import Agent
from collections import deque


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
            capacity: Maximum capacity (tons/step)
        """
        super().__init__(unique_id, model)
        
        # Core attributes
        self.mode = mode
        self.cost_per_unit = cost_per_unit
        self.lead_time = lead_time
        self.capacity = capacity
        
        # Transport queue: list of shipments in transit
        self.in_transit = []
        
        # Disruption state
        self.disrupted_jurisdictions = set()
        
        # Tracking
        self.delivered_this_step = 0
        self.total_delivered = 0
    
    def step(self):
        """Execute one time step of transport behavior."""
        self.delivered_this_step = 0
        
        # Process deliveries
        self._deliver_shipments()
        
        # Clean up old disruptions (handled by model)
    
    def _deliver_shipments(self):
        """Deliver shipments that have reached their destination."""
        current_step = self.model.current_step
        
        # Find shipments ready for delivery
        ready_shipments = [s for s in self.in_transit if s['arrival_step'] <= current_step]
        
        for shipment in ready_shipments:
            # Check if route is disrupted
            origin_jurisdiction = shipment.get('origin_jurisdiction', '')
            dest_jurisdiction = shipment.get('dest_jurisdiction', '')
            
            if (origin_jurisdiction in self.disrupted_jurisdictions or 
                dest_jurisdiction in self.disrupted_jurisdictions):
                # Delay delivery
                shipment['arrival_step'] += 1
                continue
            
            # Deliver
            self.in_transit.remove(shipment)
            self.delivered_this_step += shipment['quantity']
            self.total_delivered += shipment['quantity']
            
            # Note: Actual delivery to destination handled by model coordination
    
    def accept_shipment(self, material_type, quantity, origin, destination, 
                       origin_jurisdiction='', dest_jurisdiction=''):
        """Accept a new shipment request.
        
        Args:
            material_type: Type of material
            quantity: Amount to ship (tons)
            origin: Origin agent ID
            destination: Destination agent ID
            origin_jurisdiction: Origin country/region
            dest_jurisdiction: Destination country/region
        
        Returns:
            True if accepted, False if capacity exceeded
        """
        # Check capacity (simplified: just accept for now)
        arrival_step = self.model.current_step + self.lead_time
        
        shipment = {
            'material': material_type,
            'quantity': quantity,
            'origin': origin,
            'destination': destination,
            'origin_jurisdiction': origin_jurisdiction,
            'dest_jurisdiction': dest_jurisdiction,
            'arrival_step': arrival_step,
            'cost': self.cost_per_unit * quantity
        }
        
        self.in_transit.append(shipment)
        return True
    
    def apply_disruption(self, jurisdiction, duration):
        """Apply a geopolitical disruption affecting transport to/from a jurisdiction.
        
        Args:
            jurisdiction: Jurisdiction to disrupt
            duration: Number of steps
        """
        self.disrupted_jurisdictions.add(jurisdiction)
        
        # Schedule removal (handled by model)
    
    def remove_disruption(self, jurisdiction):
        """Remove a disruption from a jurisdiction.
        
        Args:
            jurisdiction: Jurisdiction to restore
        """
        self.disrupted_jurisdictions.discard(jurisdiction)
    
    def get_total_in_transit(self):
        """Get total quantity currently in transit."""
        return sum(s['quantity'] for s in self.in_transit)
