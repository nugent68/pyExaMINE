"""
Maritime / overland routing table for inter-country mineral shipments.

Each origin-destination country pair maps to one or more candidate routes.
A Route is (chokepoints, lead_time_weeks, mode). The shipping engine picks
the first route in the list whose chokepoints are all currently *open*; if
none qualifies, the shipment is delayed (deferred one step, retried).

Country-level routing is too granular for ~50 countries (2500 cells), so
the table is defined per (origin_region, dest_region). Each country is
mapped to a region; route lookup falls back to (region, region). Domestic
shipments (same country) get a 1-step truck route with no chokepoint.

Chokepoints currently modelled:
- Strait of Hormuz   (Persian Gulf, oil + Middle-East trade)
- Suez Canal         (Mediterranean <-> Red Sea, Europe<->Asia main route)
- Malacca Strait     (Indian Ocean <-> South China Sea, Asia<->Indian Ocean)
- Panama Canal       (Atlantic <-> Pacific, Americas)
- Cape of Good Hope  (alternate to Suez; never closed but adds lead time)

The Cape route is listed as an alt for routes that normally use Suez or
Panama -- if Suez closes, Cape becomes the de-facto route at +3-4 weeks.
"""

from typing import List, Tuple, Optional

CHOKEPOINTS = [
    'Strait of Hormuz',
    'Suez Canal',
    'Malacca Strait',
    'Panama Canal',
    'Cape of Good Hope',
]

# Region assignment. Countries not listed default to 'Other'.
COUNTRY_REGION = {
    # AsiaEast
    'China': 'AsiaEast',
    'Korea': 'AsiaEast',
    'Japan': 'AsiaEast',
    'Taiwan': 'AsiaEast',
    # AsiaSE
    'Indonesia': 'AsiaSE',
    'Philippines': 'AsiaSE',
    'Thailand': 'AsiaSE',
    'Vietnam': 'AsiaSE',
    'Malaysia': 'AsiaSE',
    # AsiaSouth
    'India': 'AsiaSouth',
    # Oceania
    'Australia': 'Oceania',
    'New Caledonia': 'Oceania',
    # AmericasPacific (West coast access)
    'USA': 'AmericasPacific',
    'Canada': 'AmericasPacific',
    'Mexico': 'AmericasPacific',
    'Chile': 'AmericasPacific',
    # AmericasAtlantic (East coast / Atlantic access)
    'Argentina': 'AmericasAtlantic',
    'Brazil': 'AmericasAtlantic',
    'Cuba': 'AmericasAtlantic',
    # EuropeAtlantic
    'UK': 'EuropeAtlantic',
    'Germany': 'EuropeAtlantic',
    'Netherlands': 'EuropeAtlantic',
    'Belgium': 'EuropeAtlantic',
    'France': 'EuropeAtlantic',
    'Spain': 'EuropeAtlantic',
    'Italy': 'EuropeAtlantic',
    'Portugal': 'EuropeAtlantic',
    'Switzerland': 'EuropeAtlantic',
    # EuropeBaltic / Nordic
    'Norway': 'EuropeBaltic',
    'Sweden': 'EuropeBaltic',
    'Finland': 'EuropeBaltic',
    # EuropeEast (rail-connected to Asia)
    'Russia': 'EuropeEast',
    'Turkey': 'EuropeEast',
    # AfricaSouth
    'South Africa': 'AfricaSouth',
    'Zimbabwe': 'AfricaSouth',
    'Madagascar': 'AfricaSouth',
    # MiddleEast
    'Saudi Arabia': 'MiddleEast',
    'UAE': 'MiddleEast',
    # Other catch-all
    'Other countries': 'Other',
}

# Per (origin_region, dest_region): list of route candidates.
# Each candidate: (chokepoints_list, lead_time_weeks, mode).
# Define one direction; lookup also tries the reverse pair.
_REGION_ROUTES = {
    # Within / between Asia
    ('AsiaEast', 'AsiaEast'):           [([], 1, 'ship')],
    ('AsiaEast', 'AsiaSE'):             [([], 1, 'ship')],
    ('AsiaEast', 'AsiaSouth'):          [(['Malacca Strait'], 3, 'ship'),
                                         (['Cape of Good Hope'], 9, 'ship')],
    ('AsiaEast', 'Oceania'):            [([], 3, 'ship')],
    ('AsiaEast', 'AmericasPacific'):    [([], 3, 'ship')],
    ('AsiaEast', 'AmericasAtlantic'):   [(['Panama Canal'], 5, 'ship'),
                                         (['Cape of Good Hope'], 8, 'ship')],
    ('AsiaEast', 'EuropeAtlantic'):     [(['Malacca Strait', 'Suez Canal'], 5, 'ship'),
                                         (['Cape of Good Hope'], 8, 'ship')],
    ('AsiaEast', 'EuropeBaltic'):       [(['Malacca Strait', 'Suez Canal'], 6, 'ship'),
                                         (['Cape of Good Hope'], 9, 'ship')],
    ('AsiaEast', 'EuropeEast'):         [([], 2, 'rail')],
    ('AsiaEast', 'AfricaSouth'):        [([], 4, 'ship')],
    ('AsiaEast', 'MiddleEast'):         [(['Malacca Strait', 'Strait of Hormuz'], 4, 'ship'),
                                         (['Cape of Good Hope'], 9, 'ship')],

    ('AsiaSE', 'AsiaSE'):               [([], 1, 'ship')],
    ('AsiaSE', 'AsiaSouth'):            [(['Malacca Strait'], 2, 'ship'),
                                         ([], 4, 'ship')],   # around Sumatra
    ('AsiaSE', 'Oceania'):              [([], 2, 'ship')],
    ('AsiaSE', 'AmericasPacific'):      [([], 4, 'ship')],
    ('AsiaSE', 'AmericasAtlantic'):     [(['Panama Canal'], 6, 'ship'),
                                         (['Cape of Good Hope'], 9, 'ship')],
    ('AsiaSE', 'EuropeAtlantic'):       [(['Malacca Strait', 'Suez Canal'], 5, 'ship'),
                                         (['Cape of Good Hope'], 8, 'ship')],
    ('AsiaSE', 'EuropeBaltic'):         [(['Malacca Strait', 'Suez Canal'], 6, 'ship'),
                                         (['Cape of Good Hope'], 9, 'ship')],
    ('AsiaSE', 'EuropeEast'):           [(['Malacca Strait', 'Suez Canal'], 6, 'ship'),
                                         (['Cape of Good Hope'], 9, 'ship')],
    ('AsiaSE', 'AfricaSouth'):          [([], 4, 'ship')],
    ('AsiaSE', 'MiddleEast'):           [(['Strait of Hormuz'], 3, 'ship')],

    ('AsiaSouth', 'AsiaSouth'):         [([], 1, 'truck')],
    ('AsiaSouth', 'Oceania'):           [([], 4, 'ship')],
    ('AsiaSouth', 'AmericasPacific'):   [(['Cape of Good Hope'], 8, 'ship'),
                                         (['Panama Canal'], 7, 'ship')],
    ('AsiaSouth', 'AmericasAtlantic'):  [(['Cape of Good Hope'], 7, 'ship'),
                                         (['Suez Canal'], 6, 'ship')],
    ('AsiaSouth', 'EuropeAtlantic'):    [(['Suez Canal'], 4, 'ship'),
                                         (['Cape of Good Hope'], 7, 'ship')],
    ('AsiaSouth', 'EuropeBaltic'):      [(['Suez Canal'], 5, 'ship'),
                                         (['Cape of Good Hope'], 8, 'ship')],
    ('AsiaSouth', 'EuropeEast'):        [(['Suez Canal'], 5, 'ship'),
                                         (['Cape of Good Hope'], 8, 'ship')],
    ('AsiaSouth', 'AfricaSouth'):       [([], 4, 'ship')],
    ('AsiaSouth', 'MiddleEast'):        [(['Strait of Hormuz'], 2, 'ship')],

    # Oceania (Australia, NC)
    ('Oceania', 'Oceania'):             [([], 1, 'ship')],
    ('Oceania', 'AmericasPacific'):     [([], 4, 'ship')],
    ('Oceania', 'AmericasAtlantic'):    [(['Panama Canal'], 6, 'ship'),
                                         (['Cape of Good Hope'], 8, 'ship')],
    ('Oceania', 'EuropeAtlantic'):      [(['Malacca Strait', 'Suez Canal'], 6, 'ship'),
                                         (['Cape of Good Hope'], 9, 'ship')],
    ('Oceania', 'EuropeBaltic'):        [(['Malacca Strait', 'Suez Canal'], 7, 'ship'),
                                         (['Cape of Good Hope'], 10, 'ship')],
    ('Oceania', 'EuropeEast'):          [(['Malacca Strait', 'Suez Canal'], 7, 'ship'),
                                         (['Cape of Good Hope'], 10, 'ship')],
    ('Oceania', 'AfricaSouth'):         [([], 5, 'ship')],
    ('Oceania', 'MiddleEast'):          [(['Strait of Hormuz'], 4, 'ship')],

    # Americas
    ('AmericasPacific', 'AmericasPacific'):   [([], 2, 'ship')],
    ('AmericasPacific', 'AmericasAtlantic'):  [(['Panama Canal'], 3, 'ship'),
                                               (['Cape of Good Hope'], 8, 'ship')],
    ('AmericasPacific', 'EuropeAtlantic'):    [(['Panama Canal'], 4, 'ship'),
                                               ([], 5, 'ship')],   # via NA-east coast then Atlantic
    ('AmericasPacific', 'EuropeBaltic'):      [(['Panama Canal'], 5, 'ship'),
                                               ([], 6, 'ship')],
    ('AmericasPacific', 'EuropeEast'):        [(['Panama Canal'], 5, 'ship')],
    ('AmericasPacific', 'AfricaSouth'):       [(['Cape of Good Hope'], 6, 'ship'),
                                               (['Panama Canal'], 7, 'ship')],
    ('AmericasPacific', 'MiddleEast'):        [(['Panama Canal', 'Suez Canal'], 7, 'ship'),
                                               (['Cape of Good Hope', 'Strait of Hormuz'], 9, 'ship')],

    ('AmericasAtlantic', 'AmericasAtlantic'): [([], 2, 'ship')],
    ('AmericasAtlantic', 'EuropeAtlantic'):   [([], 3, 'ship')],
    ('AmericasAtlantic', 'EuropeBaltic'):     [([], 4, 'ship')],
    ('AmericasAtlantic', 'EuropeEast'):       [([], 4, 'ship')],
    ('AmericasAtlantic', 'AfricaSouth'):      [([], 4, 'ship')],
    ('AmericasAtlantic', 'MiddleEast'):       [(['Suez Canal'], 6, 'ship'),
                                               (['Cape of Good Hope', 'Strait of Hormuz'], 8, 'ship')],

    # Europe
    ('EuropeAtlantic', 'EuropeAtlantic'):     [([], 1, 'truck')],
    ('EuropeAtlantic', 'EuropeBaltic'):       [([], 1, 'ship')],
    ('EuropeAtlantic', 'EuropeEast'):         [([], 2, 'rail')],
    ('EuropeAtlantic', 'AfricaSouth'):        [(['Suez Canal'], 4, 'ship'),
                                               (['Cape of Good Hope'], 5, 'ship')],
    ('EuropeAtlantic', 'MiddleEast'):         [(['Suez Canal'], 3, 'ship'),
                                               (['Cape of Good Hope', 'Strait of Hormuz'], 7, 'ship')],

    ('EuropeBaltic', 'EuropeBaltic'):         [([], 1, 'ship')],
    ('EuropeBaltic', 'EuropeEast'):           [([], 2, 'rail')],
    ('EuropeBaltic', 'AfricaSouth'):          [(['Suez Canal'], 5, 'ship'),
                                               (['Cape of Good Hope'], 6, 'ship')],
    ('EuropeBaltic', 'MiddleEast'):           [(['Suez Canal'], 4, 'ship'),
                                               (['Cape of Good Hope', 'Strait of Hormuz'], 8, 'ship')],

    ('EuropeEast', 'EuropeEast'):             [([], 1, 'rail')],
    ('EuropeEast', 'AfricaSouth'):            [(['Suez Canal'], 5, 'ship'),
                                               (['Cape of Good Hope'], 6, 'ship')],
    ('EuropeEast', 'MiddleEast'):             [(['Suez Canal'], 4, 'ship')],

    # Africa / Middle East
    ('AfricaSouth', 'AfricaSouth'):           [([], 1, 'truck')],
    ('AfricaSouth', 'MiddleEast'):            [(['Strait of Hormuz'], 4, 'ship')],
    ('MiddleEast', 'MiddleEast'):             [([], 1, 'truck')],
}


def _norm_region(country: str) -> str:
    return COUNTRY_REGION.get(country, 'Other')


def get_routes(origin_country: str, dest_country: str) -> List[Tuple[List[str], int, str]]:
    """Return a list of route candidates from origin to destination.

    Each candidate is (chokepoints, lead_time_weeks, mode). The first
    candidate is the primary route; subsequent ones are alternates with
    longer lead times. Caller picks the first candidate whose chokepoints
    are all currently open.

    Domestic shipments (same country) return a 1-step truck route. A pair
    not in the table falls back to a generic ship route at 5 weeks with
    no chokepoint, which is conservative enough to keep simulations
    running for any country we haven't catalogued.
    """
    if origin_country == dest_country:
        return [([], 1, 'truck')]

    o_region = _norm_region(origin_country)
    d_region = _norm_region(dest_country)

    if (o_region, d_region) in _REGION_ROUTES:
        return list(_REGION_ROUTES[(o_region, d_region)])
    if (d_region, o_region) in _REGION_ROUTES:
        return list(_REGION_ROUTES[(d_region, o_region)])

    # Fallback: assume direct ship at 5 weeks with no chokepoint.
    return [([], 5, 'ship')]


def select_open_route(
    origin_country: str,
    dest_country: str,
    closed_chokepoints,
) -> Optional[Tuple[List[str], int, str]]:
    """Return the first route candidate whose chokepoints are all open.

    Returns None if every candidate is blocked.
    """
    closed = set(closed_chokepoints)
    for chokepoints, lead_time, mode in get_routes(origin_country, dest_country):
        if not any(cp in closed for cp in chokepoints):
            return (chokepoints, lead_time, mode)
    return None


def select_route_or_fallback(
    origin_country: str,
    dest_country: str,
    closed_chokepoints,
) -> Optional[Tuple[List[str], int, str]]:
    """Return the best available route, or the longest fallback if blocked.

    Picks the first open route. If every candidate is blocked, returns the
    last (longest-lead) candidate so the caller can still dispatch the
    shipment; the transport agent's per-step delivery check will defer
    arrival until a chokepoint reopens. This keeps the model running
    even under multi-chokepoint crises -- shipments queue rather than
    being silently dropped.
    """
    open_route = select_open_route(origin_country, dest_country, closed_chokepoints)
    if open_route is not None:
        return open_route
    candidates = get_routes(origin_country, dest_country)
    return candidates[-1] if candidates else None
