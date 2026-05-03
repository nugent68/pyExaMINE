"""
Data loader for USGS Critical Minerals Mapping data.
Parses USGS_CMM.csv and extracts production, reserves, and demand data.

Column headers may carry a unit annotation in square brackets, e.g.
``Lithium_Production_2024[t/yr]``. The loader strips the suffix from the
column name and rescales the column values into base units (tonnes for
stocks, tonnes/year for flows). Downstream code therefore always sees
quantities in tonnes / tonnes-per-year and never needs to know which
unit the source CSV used.
"""

import re
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple


# Conversion factors from each supported unit to the base unit
# (tonnes for stocks, tonnes/year for flows). Counts and percents pass
# through unchanged.
UNIT_TO_BASE_FACTOR: Dict[str, float] = {
    "":       1.0,
    "t":      1.0,
    "t/yr":   1.0,
    "tonne":  1.0,
    "tonnes": 1.0,
    "kg":     1e-3,
    "kg/yr":  1e-3,
    "g":      1e-6,
    "g/yr":   1e-6,
    "kt":     1e3,
    "kt/yr":  1e3,
    "Mt":     1e6,
    "Mt/yr":  1e6,
    "%":      1.0,
    "count":  1.0,
}

_UNIT_PATTERN = re.compile(r"^(?P<base>.+?)\s*\[\s*(?P<unit>[^\]]*)\s*\]\s*$")


def _parse_column_unit(column: str) -> Tuple[str, str]:
    """Split a column header into (logical_name, unit_token).

    Columns without a ``[unit]`` suffix return an empty unit token.
    """
    m = _UNIT_PATTERN.match(column)
    if not m:
        return column, ""
    return m.group("base").strip(), m.group("unit").strip()


class USGSDataLoader:
    """Loads and processes USGS Critical Minerals Mapping data."""

    def __init__(self, csv_path: str = "USGS_CMM.csv"):
        """Initialize the data loader.

        Args:
            csv_path: Path to USGS_CMM.csv file
        """
        self.csv_path = csv_path
        self.data: pd.DataFrame = None
        self.units: Dict[str, str] = {}
        self._load_data()

    def _load_data(self):
        """Load the CSV, normalize column units to tonnes / tonnes-per-year."""
        try:
            raw = pd.read_csv(self.csv_path)
        except Exception as e:
            raise FileNotFoundError(f"Could not load {self.csv_path}: {e}")

        rename_map: Dict[str, str] = {}
        for col in raw.columns:
            base, unit = _parse_column_unit(col)
            if unit and unit not in UNIT_TO_BASE_FACTOR:
                raise ValueError(
                    f"Column '{col}' uses unsupported unit '{unit}'. "
                    f"Supported: {sorted(UNIT_TO_BASE_FACTOR)}"
                )
            factor = UNIT_TO_BASE_FACTOR.get(unit, 1.0)
            if factor != 1.0:
                raw[col] = raw[col] * factor
            rename_map[col] = base
            self.units[base] = unit

        self.data = raw.rename(columns=rename_map)
        print(f"Loaded USGS data: {len(self.data)} countries")
    
    def _find_production_column(self, mineral: str) -> str:
        """Return the production column for a mineral.

        Matches any column starting with ``{mineral}_Production_`` so the
        CSV can use any year suffix (e.g., ``_2024`` or ``_2025``).
        """
        prefix = f"{mineral}_Production_"
        for col in self.data.columns:
            if col.startswith(prefix):
                return col
        raise ValueError(f"No production column for mineral '{mineral}'")

    def get_producing_countries(self, mineral: str) -> pd.DataFrame:
        """Get countries that produce a specific mineral.

        Args:
            mineral: Mineral name (e.g., 'Lithium', 'Nickel', 'Platinum')

        Returns:
            DataFrame with producing countries and their data
        """
        production_col = self._find_production_column(mineral)
        reserves_col = f"{mineral}_Reserves"

        if reserves_col not in self.data.columns:
            raise ValueError(f"No reserves column for mineral '{mineral}'")

        producers = self.data[self.data[production_col] > 0].copy()

        cols = ['Country', production_col, reserves_col]
        demand_cols = [c for c in self.data.columns if mineral in c and 'Demand' in c]
        cols.extend(demand_cols)

        deposits_col = f"{mineral}_Num_Deposits"
        if deposits_col in self.data.columns:
            cols.append(deposits_col)

        result = producers[cols].copy().fillna(0)
        print(f"{mineral}: Found {len(result)} producing countries")
        return result
    
    def get_global_demand(self, mineral: str) -> Dict[str, float]:
        """Get global demand forecasts for a mineral.
        
        Args:
            mineral: Mineral name
        
        Returns:
            Dictionary with demand values for different years
        """
        demand_data = {}
        demand_cols = [col for col in self.data.columns if mineral in col and 'Demand' in col]
        
        for col in demand_cols:
            # Extract year and (optional) scenario from column name. A
            # plain "..._Demand_2024" yields key "2024"; a scenario
            # "..._Demand_2030_NetZero" yields key "2030_NetZero".
            parts = col.split('_')
            year_idx = [i for i, p in enumerate(parts) if p.isdigit()]
            if year_idx:
                year = parts[year_idx[0]]
                scenario = '_'.join(parts[year_idx[0] + 1:])
                key = f"{year}_{scenario}" if scenario else year

                # Use the first non-zero value across rows so a 0 in row 0
                # doesn't shadow a real global figure further down.
                values = self.data[col].dropna()
                non_zero = values[values > 0]
                if len(non_zero) > 0:
                    demand_data[key] = float(non_zero.iloc[0])
                elif len(values) > 0:
                    demand_data[key] = float(values.iloc[0])
                else:
                    demand_data[key] = 0.0
        
        return demand_data
    
    def calculate_market_shares(self, mineral: str) -> pd.DataFrame:
        """Calculate market share percentage for each producing country."""
        producers = self.get_producing_countries(mineral)
        production_col = self._find_production_column(mineral)

        total_production = producers[production_col].sum()
        producers['Market_Share_Pct'] = (producers[production_col] / total_production) * 100

        return producers
    
    def derive_mine_parameters(self, country: str, mineral: str,
                               base_extraction_cost: float = 10000,
                               default_ore_grade: float = 0.7) -> Dict:
        """Derive mine parameters for a specific country and mineral.

        Args:
            country: Country name
            mineral: Mineral name
            base_extraction_cost: Base extraction cost in $/ton
            default_ore_grade: Ore grade to assign (metadata only; the
                model treats production_capacity as already in
                contained-mineral tonnes per the USGS convention).

        Returns:
            Dictionary with mine parameters (production_capacity in
            tonnes/year, reserves in tonnes).
        """
        producers = self.get_producing_countries(mineral)
        country_data = producers[producers['Country'] == country]

        if len(country_data) == 0:
            raise ValueError(f"{country} does not produce {mineral}")

        country_data = country_data.iloc[0]
        production_col = self._find_production_column(mineral)
        reserves_col = f"{mineral}_Reserves"

        production = country_data[production_col]
        reserves = country_data[reserves_col]

        # Regional cost multipliers (simplified)
        regional_multipliers = {
            'China': 0.8, 'Indonesia': 0.9, 'Russia': 1.1, 'Australia': 1.3,
            'Canada': 1.2, 'United States': 1.4, 'Chile': 1.0, 'Brazil': 0.95,
            'South Africa': 1.0, 'Congo (Kinshasa)': 0.7, 'Philippines': 0.85,
            'Other countries': 1.0,
        }
        multiplier = regional_multipliers.get(country, 1.0)
        extraction_cost = base_extraction_cost * multiplier

        return {
            'jurisdiction': country,
            'production_capacity': production,
            'reserves': reserves,
            'ore_grade': default_ore_grade,
            'extraction_cost': extraction_cost,
        }
    
    def get_all_mines_data(self, mineral: str, base_extraction_cost: float = 10000) -> List[Dict]:
        """Get mine parameters for all producing countries.
        
        Args:
            mineral: Mineral name
            base_extraction_cost: Base extraction cost
        
        Returns:
            List of dictionaries with mine parameters
        """
        producers = self.get_producing_countries(mineral)
        mines_data = []
        
        for _, row in producers.iterrows():
            country = row['Country']
            try:
                mine_params = self.derive_mine_parameters(country, mineral, base_extraction_cost)
                mines_data.append(mine_params)
            except Exception as e:
                print(f"Warning: Could not derive parameters for {country}: {e}")
        
        return mines_data


# Convenience function
def load_mineral_data(mineral: str, csv_path: str = "USGS_CMM.csv") -> Tuple[List[Dict], Dict]:
    """Load complete data for a mineral.
    
    Args:
        mineral: Mineral name
        csv_path: Path to USGS CSV
    
    Returns:
        Tuple of (mines_data, demand_data)
    """
    loader = USGSDataLoader(csv_path)
    mines_data = loader.get_all_mines_data(mineral)
    demand_data = loader.get_global_demand(mineral)
    
    return mines_data, demand_data
