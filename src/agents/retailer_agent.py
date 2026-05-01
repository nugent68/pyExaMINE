"""
RetailerAgent: Manages inventory and sells to consumers.
Implements (s, Q) inventory policy.
"""

from mesa import Agent


class RetailerAgent(Agent):
    """Agent representing a retailer using (s,Q) inventory policy."""
    
    def __init__(self, unique_id, model, reorder_point, order_quantity):
        """Initialize a RetailerAgent.
        
        Args:
            unique_id: Unique identifier
            model: Model instance
            reorder_point: Inventory level that triggers reorder (s parameter)
            order_quantity: Amount to order when restocking (Q parameter)
        """
        super().__init__(unique_id, model)
        
        # Inventory policy parameters
        self.reorder_point = reorder_point
        self.order_quantity = order_quantity
        
        # Inventory
        self.inventory = order_quantity  # Start with some inventory
        
        # Order tracking
        self.pending_orders = []  # List of {quantity, arrival_step}
        self.lead_time = model.config.get("retailer_lead_time", 3)
        
        # Tracking
        self.sold_this_step = 0
        self.stockouts = 0
        self.total_sales = 0
    
    def step(self):
        """Execute one time step of retailer behavior."""
        self.sold_this_step = 0
        
        # 1. Receive pending orders that have arrived
        self._receive_orders()
        
        # 2. Sell to consumers (handled by consumer agents pulling)
        
        # 3. Check inventory and reorder if needed
        self._check_and_reorder()
    
    def _receive_orders(self):
        """Receive pending orders that have arrived."""
        current_step = self.model.schedule.steps
        
        arrived_orders = [order for order in self.pending_orders 
                         if order['arrival_step'] <= current_step]
        
        for order in arrived_orders:
            self.inventory += order['quantity']
            self.pending_orders.remove(order)
    
    def _check_and_reorder(self):
        """Check inventory level and reorder if below reorder point."""
        # Only reorder if no pending orders and inventory below reorder point
        if self.inventory <= self.reorder_point and len(self.pending_orders) == 0:
            self._place_order()
    
    def _place_order(self):
        """Place an order with manufacturers."""
        # Find manufacturers with available output
        manufacturers = [agent for agent in self.model.schedule.agents 
                        if hasattr(agent, 'get_available_output')]
        
        remaining = self.order_quantity
        
        for manufacturer in manufacturers:
            if remaining <= 0:
                break
            
            available = manufacturer.get_available_output()
            if available > 0:
                desired = min(remaining, available)
                actual = manufacturer.sell_output(desired)
                
                # Create pending order
                order = {
                    'quantity': actual,
                    'arrival_step': self.model.schedule.steps + self.lead_time
                }
                self.pending_orders.append(order)
                remaining -= actual
    
    def sell_to_consumer(self, amount):
        """Sell to a consumer.
        
        Args:
            amount: Amount requested
        
        Returns:
            Actual amount sold
        """
        if self.inventory <= 0:
            self.stockouts += 1
            return 0
        
        sold = min(amount, self.inventory)
        self.inventory -= sold
        self.sold_this_step += sold
        self.total_sales += sold
        
        return sold
    
    def get_available_inventory(self):
        """Get current available inventory."""
        return self.inventory
