"""
Market mechanism for supply chain model.

Cost-anchored price dynamics:

- The per-step move is *proportional* to the log of the supply/demand
  ratio (capped at ``max_step_pct``), so a small imbalance produces a
  small move and a severe shortage produces a large but bounded one.
  The discrete +5%/-5% / dead-band rule it replaces saturated all
  shortage scenarios at the same speed, making them indistinguishable.
- An exponential pull toward the merit-order ``marginal_cost`` provides
  long-run mean reversion. When the price drifts away from cost (up
  during a shock, or down under structural surplus), the anchor brings
  it back over many steps.
- The soft band scales with the cost curve: the floor is a fraction of
  the *cheapest active extraction cost*, and the ceiling is a multiple
  of marginal cost. Both update each step as the cost curve shifts
  (depletion, mothballing, capacity expansion). Outer hard
  ``price_floor``/``price_ceiling`` config values remain as catastrophe
  bounds but normally don't bind.
"""

import math
import random


def update_price(
    current_price,
    supply_flow,
    demand_flow,
    marginal_cost,
    cheapest_cost,
    *,
    elasticity=0.4,
    max_step_pct=0.15,
    anchor_strength=0.05,
    ceiling_mc_multiple=8.0,
    floor_cost_fraction=0.6,
    hard_floor=0.0,
    hard_ceiling=float('inf'),
):
    """Update global price using a proportional move + log-linear anchor.

    Args:
        current_price: Current price ($/ton).
        supply_flow: Mineral entering the market this step (tons/step) --
            mine output (excluding any embargoed/stockpiled production)
            plus recycled supply. Typically smoothed over a small window
            by the caller.
        demand_flow: Mineral demanded this step (tons/step), already
            converted from product units via mineral intensity. Typically
            smoothed over the same window.
        marginal_cost: Short-run merit-order marginal cost ($/ton) --
            the extraction cost of the last operational mine that would
            need to be called online to meet demand at this step.
            Acts as the equilibrium anchor.
        cheapest_cost: Cheapest active extraction cost ($/ton). Sets
            the soft floor of the price band.
        elasticity: Per-step move per unit of log(supply/demand). A 30%
            shortage (ratio 0.7, log -0.36) moves the price by
            elasticity * 0.36 = 14% (capped at max_step_pct).
        max_step_pct: Hard cap on per-step move magnitude (positive
            and negative).
        anchor_strength: Per-step pull toward marginal_cost in log
            space. 0.05 means the price closes ~5% of the log-gap to
            marginal_cost each step.
        ceiling_mc_multiple: Soft ceiling = ceiling_mc_multiple *
            marginal_cost. Default 8x lets a true crisis still show as
            a level rather than saturating a fixed config ceiling.
        floor_cost_fraction: Soft floor = floor_cost_fraction *
            cheapest_cost. Default 60% lets price dip briefly below
            cash-cost but not arbitrarily low.
        hard_floor / hard_ceiling: Outer catastrophe bounds (config
            ``price_floor`` / ``price_ceiling``). Normally don't bind.

    Returns:
        Updated price.
    """
    if current_price <= 0:
        return current_price

    # 1. Imbalance-driven proportional move (log-symmetric).
    if supply_flow > 0 and demand_flow > 0:
        log_ratio = math.log(supply_flow / demand_flow)
        # log_ratio < 0 => shortage => move > 0 (price up).
        move = -elasticity * log_ratio
        if move > max_step_pct:
            move = max_step_pct
        elif move < -max_step_pct:
            move = -max_step_pct
    elif demand_flow > 0:
        # Total supply collapse with demand still present -- this is
        # the worst case (e.g. every mine mothballed). The log-ratio
        # would be -infinity; saturate at the maximum upward move so
        # the price keeps climbing each step until something restarts.
        # Without this branch the imbalance signal silently flatlines
        # at exactly zero supply, leaving the anchor (= restart
        # threshold) as the equilibrium and preventing restarts.
        move = max_step_pct
    else:
        move = 0.0

    # 2. Log-linear anchor toward marginal cost.
    if marginal_cost > 0:
        anchor_pull = anchor_strength * math.log(marginal_cost / current_price)
    else:
        anchor_pull = 0.0

    new_price = current_price * math.exp(move + anchor_pull)

    # 3. Soft cost-curve band.
    if marginal_cost > 0:
        soft_ceiling = ceiling_mc_multiple * marginal_cost
        new_price = min(new_price, soft_ceiling)
    if cheapest_cost > 0:
        soft_floor = floor_cost_fraction * cheapest_cost
        new_price = max(new_price, soft_floor)

    # 4. Outer catastrophe bounds (rarely bind; safety net only).
    return max(hard_floor, min(new_price, hard_ceiling))


def marginal_cost(mines, demand_per_step, offline_premium=1.0):
    """Short-run merit-order marginal cost ($/ton).

    Walks operational mines (not mothballed, not currently disrupted) in
    cost order, accumulating per-step capacity. Returns the extraction
    cost of the mine where cumulative capacity first meets
    ``demand_per_step``. If aggregate operational capacity falls short,
    returns the highest-cost operational mine (the binding marginal
    producer at full system stretch).

    When *no* mines are operational at all, returns
    ``cheapest_extraction_cost * offline_premium``. The premium reflects
    the fact that bringing a mothballed mine back online requires the
    price to be above its restart threshold (typically 1.2x extraction
    cost). Without the premium, the soft floor pins the price right at
    the cheapest extraction cost -- below the restart threshold -- and
    nothing ever restarts. Default ``1.0`` keeps backward-compatible
    behavior when callers don't pass the model's restart margin.

    Mothballed mines aren't counted in the operational walk because
    they aren't producing this step. As price rises and triggers their
    restart, they rejoin the cost curve naturally on subsequent steps.
    """
    if not mines:
        return 0.0
    if demand_per_step <= 0:
        return min(m.extraction_cost for m in mines if m.extraction_cost > 0)

    operational = sorted(
        (m for m in mines if m.disruption_counter == 0 and not m.mothballed
         and m.extraction_cost > 0),
        key=lambda m: m.extraction_cost,
    )
    if not operational:
        # No active producers -- the price needed to bring the cheapest
        # mothballed mine back online is its extraction cost times the
        # restart margin.
        cheapest = min(m.extraction_cost for m in mines if m.extraction_cost > 0)
        return cheapest * offline_premium

    cumulative = 0.0
    last_cost = operational[0].extraction_cost
    for mine in operational:
        last_cost = mine.extraction_cost
        cumulative += getattr(mine, 'production_capacity', 0.0)
        if cumulative >= demand_per_step:
            return last_cost
    # Operational capacity falls short -- the most expensive active mine
    # is the binding marginal producer.
    return last_cost


def cheapest_active_cost(mines, offline_premium=1.0):
    """Cheapest extraction cost among operational mines, for the floor.

    See ``marginal_cost`` for the rationale on ``offline_premium`` --
    the fallback when no mines are operational also gets multiplied by
    the premium so the soft floor lifts above the restart trigger.
    """
    if not mines:
        return 0.0
    operational = [
        m for m in mines
        if m.disruption_counter == 0 and not m.mothballed and m.extraction_cost > 0
    ]
    if operational:
        return min(m.extraction_cost for m in operational)
    cheapest = min(m.extraction_cost for m in mines if m.extraction_cost > 0)
    return cheapest * offline_premium


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
