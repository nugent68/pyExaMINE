"""
Market mechanism for supply chain model.
Handles global price dynamics and geopolitical events.
"""

import random


def update_price(current_price, total_inventory, demand_per_step,
                 price_floor, price_ceiling,
                 shortage_weeks=4.0, surplus_weeks=12.0,
                 step_pct=0.05):
    """Update global price based on weeks-of-inventory.

    Args:
        current_price: Current price ($/ton).
        total_inventory: Total mineral inventory (tons).
        demand_per_step: Mineral demand per step (tons/step).
        price_floor: Minimum price.
        price_ceiling: Maximum price.
        shortage_weeks: Below this many weeks of cover, price rises.
        surplus_weeks: Above this many weeks of cover, price falls.
        step_pct: Price adjustment fraction per step.

    Returns:
        Updated price.
    """
    if demand_per_step > 0:
        weeks_of_inventory = total_inventory / demand_per_step
    else:
        weeks_of_inventory = (shortage_weeks + surplus_weeks) / 2.0

    if weeks_of_inventory < shortage_weeks:
        new_price = current_price * (1.0 + step_pct)
    elif weeks_of_inventory > surplus_weeks:
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
