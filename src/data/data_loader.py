"""
Per-facility data loader for the worldwide mineral supply-chain model.

Reads CSVs under data/ at the project root:
  - {mineral}_mines.csv         (per-facility production / reserves / cost)
  - {mineral}_processors.csv    (per-facility refining capacity)
  - {mineral}_recyclers.csv     (per-facility recycling capacity)
  - {mineral}_manufacturers.csv (country-level share)
  - {mineral}_consumers.csv     (country-level demand share)
  - demand.csv                  (global demand by mineral / year / scenario)
  - transport_fleet.csv         (per-country transport agents by mode)

Each loader returns dictionaries / list-of-dicts in tonnes / tonnes-per-year.
Rows beginning with "#" in the CSVs are stripped before parsing.
"""

import os
import io
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd


# Project root: this file is .../src/data/data_loader.py
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _PROJECT_ROOT / 'data'


# Map "Lithium" -> "lithium" prefix used in CSV filenames.
_PREFIX = {'Lithium': 'lithium', 'Nickel': 'nickel', 'Platinum': 'platinum'}


def _read_csv(path: Path) -> pd.DataFrame:
    """Read a CSV, stripping out comment lines that start with '#'."""
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")
    with open(path, 'r') as f:
        lines = [ln for ln in f if not ln.startswith('#')]
    return pd.read_csv(io.StringIO(''.join(lines)))


def _strip_unit_suffix(df: pd.DataFrame) -> pd.DataFrame:
    """Strip ``[unit]`` suffix from any column header so the rest of the
    code sees clean names. Unit conversion is a no-op for now -- all
    facility CSVs are already in tonnes / tonnes/year. The stripped names
    end up as e.g. 'production_2024'.
    """
    rename = {}
    for col in df.columns:
        if '[' in col:
            rename[col] = col.split('[', 1)[0].strip()
    if rename:
        df = df.rename(columns=rename)
    return df


def _data_dir() -> Path:
    """Allow MINERAL_DATA_DIR env override (handy for tests)."""
    override = os.environ.get('MINERAL_DATA_DIR')
    if override:
        return Path(override)
    return _DATA_DIR


def _prefix_for(mineral: str) -> str:
    if mineral not in _PREFIX:
        raise ValueError(f"Unknown mineral '{mineral}'. Known: {list(_PREFIX)}")
    return _PREFIX[mineral]


def load_mines(mineral: str) -> List[Dict]:
    """Return list of mine facility dicts for the named mineral."""
    df = _strip_unit_suffix(_read_csv(_data_dir() / f"{_prefix_for(mineral)}_mines.csv"))
    out = []
    for _, row in df.iterrows():
        out.append({
            'country': str(row['country']).strip(),
            'facility': str(row['facility']).strip(),
            'production_capacity': float(row['production_2024']),
            'reserves': float(row['reserves']),
            'extraction_cost': float(row['extraction_cost']),
        })
    return out


def load_processors(mineral: str) -> List[Dict]:
    """Return list of processor facility dicts for the named mineral."""
    df = _strip_unit_suffix(_read_csv(_data_dir() / f"{_prefix_for(mineral)}_processors.csv"))
    out = []
    for _, row in df.iterrows():
        out.append({
            'country': str(row['country']).strip(),
            'facility': str(row['facility']).strip(),
            'capacity': float(row['capacity_2024']),
            'conversion_efficiency': float(row['conversion_efficiency']),
            'energy_cost': float(row['energy_cost']),
        })
    return out


def load_recyclers(mineral: str) -> List[Dict]:
    """Return list of recycler facility dicts for the named mineral."""
    df = _strip_unit_suffix(_read_csv(_data_dir() / f"{_prefix_for(mineral)}_recyclers.csv"))
    out = []
    for _, row in df.iterrows():
        out.append({
            'country': str(row['country']).strip(),
            'facility': str(row['facility']).strip(),
            'capacity': float(row['capacity_2024']),
            'recovery_efficiency': float(row['recovery_efficiency']),
            'processing_cost': float(row['processing_cost']),
        })
    return out


def load_manufacturer_countries(mineral: str) -> List[Dict]:
    """Return list of {country, share} dicts. Shares are normalised to sum 1."""
    df = _read_csv(_data_dir() / f"{_prefix_for(mineral)}_manufacturers.csv")
    total = df['share'].sum()
    if total <= 0:
        raise ValueError(f"Manufacturer shares for {mineral} sum to {total}")
    return [
        {'country': str(r['country']).strip(), 'share': float(r['share']) / total}
        for _, r in df.iterrows()
    ]


def load_consumer_countries(mineral: str) -> List[Dict]:
    """Return list of {country, share} dicts. Shares normalised to sum 1."""
    df = _read_csv(_data_dir() / f"{_prefix_for(mineral)}_consumers.csv")
    total = df['share'].sum()
    if total <= 0:
        raise ValueError(f"Consumer shares for {mineral} sum to {total}")
    return [
        {'country': str(r['country']).strip(), 'share': float(r['share']) / total}
        for _, r in df.iterrows()
    ]


def load_demand(mineral: str) -> Dict[str, float]:
    """Return {scenario_key: tonnes_per_year} for the named mineral.

    Keys are 'YYYY' for the baseline and 'YYYY_scenario' otherwise (e.g.
    '2024', '2030_NetZero').
    """
    df = _read_csv(_data_dir() / 'demand.csv')
    df = df[df['mineral'] == mineral]
    out = {}
    for _, r in df.iterrows():
        year = str(int(r['year']))
        scen = str(r.get('scenario', '')).strip()
        key = year if scen in ('', 'baseline') else f"{year}_{scen}"
        out[key] = float(r['demand_t_per_yr'])
    return out


def load_transport_fleet() -> List[Dict]:
    """Return list of transport-agent specs across all countries / modes.

    Each entry has keys: country, mode, n_agents, capacity_per_agent.
    The model expands one TransportAgent per agent slot.
    """
    df = _read_csv(_data_dir() / 'transport_fleet.csv')
    out = []
    for _, r in df.iterrows():
        out.append({
            'country': str(r['country']).strip(),
            'mode': str(r['mode']).strip().lower(),
            'n_agents': int(r['n_agents']),
            'capacity_per_agent': float(r['capacity_per_agent']),
        })
    return out


def load_mineral_data(mineral: str) -> Dict:
    """Load the full per-mineral data bundle.

    Returns a dict with:
      mines, processors, recyclers   -- list of facility dicts
      manufacturer_countries,
      consumer_countries             -- list of {country, share} dicts
      demand                         -- {scenario_key: t/yr}
      transport_fleet                -- list of transport-agent specs
    """
    return {
        'mines':                  load_mines(mineral),
        'processors':             load_processors(mineral),
        'recyclers':              load_recyclers(mineral),
        'manufacturer_countries': load_manufacturer_countries(mineral),
        'consumer_countries':     load_consumer_countries(mineral),
        'demand':                 load_demand(mineral),
        'transport_fleet':        load_transport_fleet(),
    }
