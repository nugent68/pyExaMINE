"""
Market mechanism for supply chain model.
Handles global price dynamics and geopolitical events.
"""

import random


def update_price(current_price, total_inventory, average_demand, price_floor, price_ceiling):
    """Update global price based on supply/demand ratio.
    
    Args:
        current_price: Current price ($/ton)
        total_inventory: Total processor inventory (tons)
        average_demand: Average consumer demand (tons/step)
        price_floor: Minimum price
        price_ceiling: Maximum price
    
    Returns:
        Updated price
    """
    # Calculate inventory-to-demand ratio
    if average_demand > 0:
        inventory_ratio = total_inventory / average_demand
    else:
        inventory_ratio = 1.0
    
    # Price adjustment based on ratio
    if inventory_ratio < 0.5:
        # Supply shortage: price increases 5%
        new_price = current_price * 1.05
    elif inventory_ratio > 1.5:
        # Oversupply: price decreases 5%
        new_price = current_price * 0.95
    else:
        # Balanced: no change
        new_price = current_price
    
    # Bound price
    new_price = max(price_floor, min(new_price, price_ceiling))
    
    return new_price


def check_geopolitical_event(probability, random_state=None):
    """Check if a geopolitical event occurs.
    
    Args:
        probability: Probability of event (0-1)
        random_state: Random state for reproducibility
    
    Returns:
        True if event occurs, False otherwise
    """
    if random_state is not None:
        return random_state.random() < probability
    else:
        return random.random() < probability


def select_affected_jurisdiction(jurisdictions, random_state=None):
    """Select a random jurisdiction for geopolitical disruption.
    
    Args:
        jurisdictions: List of jurisdiction names
        random_state: Random state for reproducibility
    
    Returns:
        Selected jurisdiction name
    """
    if not jurisdictions:
        return None
    
    if random_state is not None:
        return random_state.choice(jurisdictions)
    else:
        return random.choice(jurisdictions)


def calculate_disruption_duration(min_duration, max_duration, random_state=None):
    """Calculate duration of a geopolitical disruption.
    
    Args:
        min_duration: Minimum duration (steps)
        max_duration: Maximum duration (steps)
        random_state: Random state for reproducibility
    
    Returns:
        Duration in steps
    """
    if random_state is not None:
        return random_state.randint(min_duration, max_duration)
    else:
        return random.randint(min_duration, max_duration)
