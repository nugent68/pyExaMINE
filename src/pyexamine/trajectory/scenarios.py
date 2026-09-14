"""Canonical 2050 scenarios as scenario dicts (for the trajectory pipeline).

Mirrors the ``SCENARIOS_2050`` dict in ``scripts/regenerate_outputs.py``
in the schema ``surrogate.features.encode`` accepts. Used to pair the
committed ``ensemble_runs/2050/`` trajectory CSVs with the parameter
vectors that produced them, so the trajectory dataset loader doesn't
have to re-derive scenario metadata from the directory name at every
fetch.

Single source of truth for the 2050 horizon -- if the regenerate-outputs
script's scenarios change, update this file too. (We intentionally
don't import from ``regenerate_outputs.py`` because that module pulls in
the whole simulator at import time.)
"""

from __future__ import annotations


def _embargo(country: str, start_step: int, duration: int) -> dict:
    return {"country": country, "start_step": start_step, "duration": duration}


def _chokepoint(name: str, start_step: int, duration: int) -> dict:
    return {"chokepoint": name, "start_step": start_step, "duration": duration}


#: 2050-horizon canonical scenarios, keyed by ``(mineral, folder)``.  Each
#: value is a scenario dict ready to feed into ``ft.encode``.  The
#: ``baseline`` entries below are synthetic -- the committed baseline
#: trajectories under ``ensemble_runs/2050/baseline/<mineral>`` carry no
#: events, so the corresponding scenario dict has empty ``embargoes`` and
#: ``chokepoint_crises`` lists.
CANONICAL_2050: dict[tuple[str, str], dict] = {
    # asia_crisis_2030: China embargo + Malacca + Suez closure at step 312 (~2030)
    ("lithium",  "asia_crisis_2030"): {
        "mineral": "lithium",
        "embargoes":         [_embargo("China", 312, 52)],
        "chokepoint_crises": [_chokepoint("Malacca Strait", 312, 16),
                              _chokepoint("Suez Canal",     312, 12)],
    },
    ("nickel",   "asia_crisis_2030"): {
        "mineral": "nickel",
        "embargoes":         [_embargo("China", 312, 52)],
        "chokepoint_crises": [_chokepoint("Malacca Strait", 312, 16),
                              _chokepoint("Suez Canal",     312, 12)],
    },
    ("platinum", "asia_crisis_2030"): {
        "mineral": "platinum",
        "embargoes":         [_embargo("China", 312, 52)],
        "chokepoint_crises": [_chokepoint("Malacca Strait", 312, 16),
                              _chokepoint("Suez Canal",     312, 12)],
    },
    ("cobalt",   "asia_crisis_2030"): {
        "mineral": "cobalt",
        "embargoes":         [_embargo("China", 312, 52)],
        "chokepoint_crises": [_chokepoint("Malacca Strait", 312, 16),
                              _chokepoint("Suez Canal",     312, 12)],
    },
    ("manganese", "asia_crisis_2030"): {
        "mineral": "manganese",
        "embargoes":         [_embargo("China", 312, 52)],
        "chokepoint_crises": [_chokepoint("Malacca Strait", 312, 16),
                              _chokepoint("Suez Canal",     312, 12)],
    },

    # li_nationalism_2035: Chile + Australia 2-yr embargo + 1-yr Suez at step 572
    ("lithium", "li_nationalism_2035"): {
        "mineral": "lithium",
        "embargoes":         [_embargo("Chile",     572, 104),
                              _embargo("Australia", 572, 104)],
        "chokepoint_crises": [_chokepoint("Suez Canal", 572, 52)],
    },

    # indonesia_squeeze_2032: Indonesia 2-yr embargo + Malacca 6-mo at step 416
    ("nickel", "indonesia_squeeze_2032"): {
        "mineral": "nickel",
        "embargoes":         [_embargo("Indonesia", 416, 104)],
        "chokepoint_crises": [_chokepoint("Malacca Strait", 416, 26)],
    },

    # sa_pt_crisis_2030: South Africa 1-yr embargo + Cape closure 16 wks
    ("platinum", "sa_pt_crisis_2030"): {
        "mineral": "platinum",
        "embargoes":         [_embargo("South Africa", 312, 52)],
        "chokepoint_crises": [_chokepoint("Cape of Good Hope", 312, 16)],
    },

    # drc_embargo_2030: 3-yr DRC embargo at step 312. DRC is ~70% of
    # global mined Co; this is the equivalent shock to Indonesia-on-Ni
    # or South-Africa-on-Pt for the cobalt supply chain.
    ("cobalt", "drc_embargo_2030"): {
        "mineral": "cobalt",
        "embargoes":         [_embargo("DRC", 312, 156)],
        "chokepoint_crises": [],
    },

    # sa_strike_2032: South-Africa Mn 18-mo embargo at step 416. South
    # Africa alone is ~30% of global Mn mine output; combined with the
    # adjacent Mn-rich Kalahari operations this would dominate the
    # battery-grade HPMSM feedstock for the duration.
    ("manganese", "sa_strike_2032"): {
        "mineral": "manganese",
        "embargoes":         [_embargo("South Africa", 416, 78)],
        "chokepoint_crises": [],
    },

    # multi_crisis_2040: Russia + Indonesia 78-wk embargo + Suez + Hormuz at step 832
    ("lithium",  "multi_crisis_2040"): {
        "mineral": "lithium",
        "embargoes":         [_embargo("Russia",    832, 78),
                              _embargo("Indonesia", 832, 78)],
        "chokepoint_crises": [_chokepoint("Suez Canal",       832, 26),
                              _chokepoint("Strait of Hormuz", 832, 8)],
    },
    ("nickel",   "multi_crisis_2040"): {
        "mineral": "nickel",
        "embargoes":         [_embargo("Russia",    832, 78),
                              _embargo("Indonesia", 832, 78)],
        "chokepoint_crises": [_chokepoint("Suez Canal",       832, 26),
                              _chokepoint("Strait of Hormuz", 832, 8)],
    },
    ("platinum", "multi_crisis_2040"): {
        "mineral": "platinum",
        "embargoes":         [_embargo("Russia",    832, 78),
                              _embargo("Indonesia", 832, 78)],
        "chokepoint_crises": [_chokepoint("Suez Canal",       832, 26),
                              _chokepoint("Strait of Hormuz", 832, 8)],
    },
    ("cobalt",   "multi_crisis_2040"): {
        "mineral": "cobalt",
        "embargoes":         [_embargo("Russia",    832, 78),
                              _embargo("Indonesia", 832, 78)],
        "chokepoint_crises": [_chokepoint("Suez Canal",       832, 26),
                              _chokepoint("Strait of Hormuz", 832, 8)],
    },
    ("manganese","multi_crisis_2040"): {
        "mineral": "manganese",
        "embargoes":         [_embargo("Russia",    832, 78),
                              _embargo("Indonesia", 832, 78)],
        "chokepoint_crises": [_chokepoint("Suez Canal",       832, 26),
                              _chokepoint("Strait of Hormuz", 832, 8)],
    },

    # baselines: no events, identical scenario dict per mineral
    ("lithium",   "baseline"): {"mineral": "lithium",
                                "embargoes": [], "chokepoint_crises": []},
    ("nickel",    "baseline"): {"mineral": "nickel",
                                "embargoes": [], "chokepoint_crises": []},
    ("platinum",  "baseline"): {"mineral": "platinum",
                                "embargoes": [], "chokepoint_crises": []},
    ("cobalt",    "baseline"): {"mineral": "cobalt",
                                "embargoes": [], "chokepoint_crises": []},
    ("manganese", "baseline"): {"mineral": "manganese",
                                "embargoes": [], "chokepoint_crises": []},

    # china_us_embargo_2030: 3-year China embargo starting step 312
    # (~2030). Designed for the US-policy-lever study: combine this
    # scenario with a --us-policy file to evaluate strategic reserve /
    # procurement-avoidance / recycling-ramp responses against the
    # baseline US (empty policy) under the same shock.
    ("lithium",  "china_us_embargo_2030"): {
        "mineral": "lithium",
        "embargoes":         [_embargo("China", 312, 156)],
        "chokepoint_crises": [],
    },
    ("nickel",   "china_us_embargo_2030"): {
        "mineral": "nickel",
        "embargoes":         [_embargo("China", 312, 156)],
        "chokepoint_crises": [],
    },
    ("platinum", "china_us_embargo_2030"): {
        "mineral": "platinum",
        "embargoes":         [_embargo("China", 312, 156)],
        "chokepoint_crises": [],
    },
    ("cobalt",   "china_us_embargo_2030"): {
        "mineral": "cobalt",
        "embargoes":         [_embargo("China", 312, 156)],
        "chokepoint_crises": [],
    },
    ("manganese","china_us_embargo_2030"): {
        "mineral": "manganese",
        "embargoes":         [_embargo("China", 312, 156)],
        "chokepoint_crises": [],
    },
}


def list_canonical_pairs() -> list[tuple[str, str]]:
    """Return ``(mineral, folder)`` pairs in a stable order."""
    return sorted(CANONICAL_2050)
