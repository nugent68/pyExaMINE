"""
Market mechanism for supply chain model.
Handles global price dynamics and geopolitical events.
"""

import random


def update_price(current_price, supply_flow, demand_flow,
                 price_floor, price_ceiling,
                 shortage_ratio=0.95, surplus_ratio=1.10,
                 step_pct=0.05):
    """Update global price based on the supply/demand flow ratio.

    Args:
        current_price: Current price ($/ton).
        supply_flow: Mineral entering the market this step (tons/step) —
            mine output (excluding any embargoed/stockpiled production)
            plus recycled supply. Typically smoothed over a small window
            by the caller.
        demand_flow: Mineral demanded this step (tons/step), already
            converted from product units via mineral intensity. Typically
            smoothed over the same window.
        price_floor: Minimum price.
        price_ceiling: Maximum price.
        shortage_ratio: If supply/demand falls below this, price rises.
        surplus_ratio: If supply/demand exceeds this, price falls.
        step_pct: Price adjustment fraction per step.

    Returns:
        Updated price.

    Why flow-based: an inventory-level signal is masked by safety-stock
    rules in the agents (processors hold inventory at safety stock, so
    the inventory level barely moves even under a major upstream shock).
    Comparing fresh supply against fresh demand makes the price respond
    immediately when production or recycling falls behind consumption.
    """
    if demand_flow <= 0:
        return current_price

    ratio = supply_flow / demand_flow

    if ratio < shortage_ratio:
        new_price = current_price * (1.0 + step_pct)
    elif ratio > surplus_ratio:
        new_price = current_price * (1.0 - step_pct)
    else:
        new_price = current_price

    return max(price_floor, min(new_price, price_ceiling))


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
